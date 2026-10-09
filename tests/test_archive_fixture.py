"""Offline end-to-end fixture: real pipeline, Drive protocol and GitHub checkpoints."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from src.downloader import Downloader
from src.main import main
from src.state import empty_state

VID = "abcdefghijk"
CID = "UC" + "a" * 22
MEDIA = b"deterministic fixture bytes; no real YouTube or Drive access"
FEED = f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <yt:channelId>{CID}</yt:channelId><entry>
    <yt:videoId>{VID}</yt:videoId><yt:channelId>{CID}</yt:channelId>
    <title>Fixture feed overlap</title><published>2026-10-01T00:00:00Z</published>
  </entry>
</feed>'''.encode()
ENV = {"GDRIVE_CLIENT_ID": "fixture-client", "GDRIVE_CLIENT_SECRET": "fixture-secret",
       "GDRIVE_REFRESH_TOKEN": "fixture-refresh", "GDRIVE_ROOT_FOLDER_ID": "fixture_root_id",
       "GITHUB_TOKEN": "fixture-github", "GITHUB_REPOSITORY": "owner/repo",
       "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch"}


class Fixture:
    def __init__(self):
        self.state = empty_state()
        self.sha = "0"
        self.files = {"fixture_root_id": {"id": "fixture_root_id", "mimeType": "application/vnd.google-apps.folder",
                                         "ownedByMe": True, "capabilities": {"canAddChildren": True}}}
        self.requests = []
        self.ids = 0
        self.downloads = 0
        self.extracts = 0
        self.uploads = 0
        self.feed_reads = 0
        self.session = None
        self.fail_completion = False

    @staticmethod
    def response(data, status=200, headers=None):
        return status, headers or {}, json.dumps(data).encode()

    def http(self, method, url, *, headers=None, body=None, **kwargs):
        parsed = urlsplit(url)
        self.requests.append((method, parsed.hostname, parsed.path))
        if parsed.hostname == "api.github.com":
            if method == "GET":
                return self.response({"sha": self.sha, "content": base64.b64encode(json.dumps(self.state).encode()).decode()})
            payload = json.loads(body)
            candidate = json.loads(base64.b64decode(payload["content"]))
            if self.fail_completion and candidate["videos"].get(VID, {}).get("status") == "complete":
                return self.response({}, 403)
            assert payload["sha"] == self.sha
            self.state = copy.deepcopy(candidate)
            self.sha = str(int(self.sha) + 1)
            return self.response({"content": {"sha": self.sha}})
        if parsed.hostname == "oauth2.googleapis.com":
            return self.response({"access_token": "fixture-access", "expires_in": 3600})
        if parsed.hostname == "www.youtube.com":
            assert method == "GET" and parsed.path == "/feeds/videos.xml"
            assert parse_qs(parsed.query) == {"channel_id": [CID]}
            self.feed_reads += 1
            return 200, {}, FEED
        if parsed.path.endswith("/generateIds"):
            self.ids += 1
            return self.response({"ids": [f"fixture_file_{self.ids:04}"]})
        if parsed.path.startswith("/upload/drive/"):
            if method == "POST":
                self.session = json.loads(body)
                record = self.state["videos"][VID]
                assert record["drive_file_id"] == self.session["id"] and record["status"] == "uploading"
                assert record["md5"] == hashlib.md5(MEDIA).hexdigest()
                return self.response({}, headers={"location": "https://www.googleapis.com/upload/drive/v3/files/session"})
            assert body == MEDIA
            self.uploads += 1
            self.files[self.session["id"]] = {**self.session, "size": str(len(body)), "md5Checksum": hashlib.md5(body).hexdigest()}
            return self.response({"id": self.session["id"]})
        if parsed.path == "/drive/v3/files":
            if method == "GET":
                return self.response({"files": []})
            obj = json.loads(body)
            assert obj["id"] in self.state["folders"].values()
            self.files[obj["id"]] = obj
            return self.response({"id": obj["id"]})
        fid = parsed.path.rsplit("/", 1)[-1]
        if fid in self.files:
            return self.response(self.files[fid])
        assert method == "GET" and parse_qs(parsed.query).get("fields")
        return self.response({}, 404)

    def downloader(self, *args):
        fixture = self

        class MediaFixture:
            public_fields = staticmethod(Downloader.public_fields)

            def metadata(self, vid, quality):
                fixture.extracts += 1
                return {"id": vid, "title": "Fixture video", "channel_id": CID, "upload_date": "20100506"}

            def download(self, vid, quality, info, directory):
                fixture.downloads += 1
                path = Path(directory) / "fixture.mp4"
                path.write_bytes(MEDIA)
                return path

        return MediaFixture()


class ArchiveFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, self.previous)
        Path("config").mkdir()
        Path("config/channels.yml").write_text("channels: []\n")
        self.fixture = Fixture()

    def run_archive(self, env):
        with (patch("src.state.raw_http", self.fixture.http), patch("src.drive.raw_http", self.fixture.http),
              patch("src.discover.raw_http", self.fixture.http),
              patch("src.main.Downloader", self.fixture.downloader), patch("src.main.time.sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            code = main([], env)
        return code, json.loads(Path("run-summary.json").read_text())

    def test_manual_then_duplicate_and_daily_reuse_one_reserved_id(self):
        env = {**ENV, "INPUT_URLS": "https://youtu.be/" + VID}
        code, first = self.run_archive(env)
        self.assertEqual((code, first["status"]), (0, "complete"))
        before = (self.fixture.downloads, self.fixture.extracts, self.fixture.uploads, self.fixture.ids)
        code, duplicate = self.run_archive({**env, "INPUT_DESTINATION": "Elsewhere", "INPUT_MAX_QUALITY": "1080"})
        self.assertEqual((code, duplicate["skipped"]), (0, 1))
        self.assertEqual(duplicate["videos"][0]["drive_file_id"], first["videos"][0]["drive_file_id"])
        self.assertEqual(before, (self.fixture.downloads, self.fixture.extracts, self.fixture.uploads, self.fixture.ids))
        archived = copy.deepcopy(self.fixture.state["videos"][VID])
        Path("config/channels.yml").write_text(f"channels:\n  - name: Fixture\n    channel_id: {CID}\n    enabled: true\n")
        code, daily = self.run_archive({**ENV, "GITHUB_EVENT_NAME": "schedule"})
        self.assertEqual((code, daily["successful"]), (0, 0))
        self.assertEqual(daily["discovery_errors"], [])
        self.assertEqual(self.fixture.feed_reads, 1, "the enabled feed must actually rediscover the archived video")
        self.assertEqual(self.fixture.state["videos"][VID], archived)
        self.assertEqual(before, (self.fixture.downloads, self.fixture.extracts, self.fixture.uploads, self.fixture.ids))
        self.assertEqual(self.fixture.uploads, 1)
        self.assertEqual(self.fixture.state["videos"][VID]["youtube_date_source"], "yt-dlp.upload_date")

    def test_online_readiness_uses_real_clients_with_no_external_side_effects(self):
        before = copy.deepcopy(self.fixture.state)
        with (patch("src.state.raw_http", self.fixture.http), patch("src.drive.raw_http", self.fixture.http),
              patch("src.main.Downloader", side_effect=AssertionError("downloader forbidden")),
              contextlib.redirect_stdout(io.StringIO())):
            code = main(["--readiness", "--online"], ENV)
        report = json.loads(Path("readiness-summary.json").read_text())
        self.assertEqual((code, report["status"]), (0, "ready_for_authorized_test"))
        self.assertEqual(self.fixture.state, before)
        self.assertTrue(all(method == "GET" or host == "oauth2.googleapis.com"
                            for method, host, path in self.fixture.requests))
        self.assertEqual((self.fixture.uploads, self.fixture.downloads, self.fixture.ids), (0, 0, 0))

    def test_lost_completion_checkpoint_recovers_without_second_transfer(self):
        env = {**ENV, "INPUT_URLS": "https://youtu.be/" + VID}
        self.fixture.fail_completion = True
        code, failed = self.run_archive(env)
        self.assertEqual((code, failed["status"]), (1, "fatal"))
        self.assertEqual(self.fixture.state["videos"][VID]["status"], "uploading")
        before = (self.fixture.downloads, self.fixture.extracts, self.fixture.uploads, self.fixture.ids)
        self.fixture.fail_completion = False
        code, recovered = self.run_archive(env)
        self.assertEqual((code, recovered["status"]), (0, "complete"))
        self.assertTrue(recovered["videos"][0]["recovered"])
        self.assertEqual(before, (self.fixture.downloads, self.fixture.extracts, self.fixture.uploads, self.fixture.ids))

    def test_daily_feed_then_manual_request_skip_the_same_video(self):
        Path("config/channels.yml").write_text(f"channels:\n  - name: Fixture\n    channel_id: {CID}\n")
        feed = [{"video_id": VID, "channel_id": CID, "title": "Feed title", "published_at": "2026-10-01"}]
        with patch("src.main.discover_queue.__defaults__", (lambda channel: feed,)):
            code, daily = self.run_archive({**ENV, "GITHUB_EVENT_NAME": "schedule"})
        self.assertEqual((code, daily["successful"]), (0, 1))
        before = self.fixture.downloads, self.fixture.extracts, self.fixture.uploads, self.fixture.ids
        code, manual = self.run_archive({**ENV, "INPUT_URLS": "https://youtu.be/" + VID})
        self.assertEqual((code, manual["skipped"]), (0, 1))
        self.assertEqual(before, (self.fixture.downloads, self.fixture.extracts, self.fixture.uploads, self.fixture.ids))
