import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.common import HttpError, SetupError
from src.downloader import Downloader
from src.drive import FOLDER, Drive
from src.main import main
from src.readiness import REQUIRED, check_configuration, readiness
from src.state import State

ENV = {"GDRIVE_CLIENT_ID": "fixture-client", "GDRIVE_CLIENT_SECRET": "fixture-secret",
       "GDRIVE_REFRESH_TOKEN": "fixture-refresh", "GDRIVE_ROOT_FOLDER_ID": "fixture_root_id",
       "GITHUB_TOKEN": "fixture-github", "GITHUB_REPOSITORY": "owner/repo"}
ROOT = {"mimeType": FOLDER, "ownedByMe": True, "capabilities": {"canAddChildren": True}}
CONFIG = {"channels": []}


class ReadinessTests(unittest.TestCase):
    def test_collects_all_missing_and_invalid_fields_without_values(self):
        with self.assertRaises(SetupError) as ctx:
            check_configuration({"GDRIVE_ROOT_FOLDER_ID": "https://private-canary.test/folders/x",
                                 "GDRIVE_CLIENT_ID": "client\ncanary"})
        details = ctx.exception.details
        self.assertEqual(details["missing_fields"], ["GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"])
        self.assertEqual(details["invalid_fields"], ["GDRIVE_CLIENT_ID", "GDRIVE_ROOT_FOLDER_ID"])
        self.assertNotIn("canary", str(ctx.exception) + json.dumps(details))

    @patch("src.state.raw_http", side_effect=AssertionError("network forbidden"))
    @patch("src.drive.raw_http", side_effect=AssertionError("network forbidden"))
    def test_missing_configuration_never_contacts_network(self, drive_http, github_http):
        report, code = readiness({}, CONFIG, online=True)
        self.assertEqual((code, report["status"]), (2, "setup_required"))
        self.assertEqual(report["missing_fields"], list(REQUIRED))
        drive_http.assert_not_called()
        github_http.assert_not_called()

    @patch("src.state.GitHubState")
    @patch("src.drive.Drive")
    def test_online_inspection_has_no_checkpoint_download_or_upload(self, drive_class, state_class):
        state = State()
        state.save = Mock(side_effect=AssertionError("writes forbidden"))
        state_class.return_value = state
        report, code = readiness(ENV, CONFIG, online=True)
        self.assertEqual((code, report["status"]), (0, "ready_for_authorized_test"))
        drive_class.return_value.preflight.assert_called_once_with(record_root=False)
        self.assertEqual(report["checks"]["checkpoint_write"], "not_checked")
        self.assertEqual(report["checks"]["first_upload"], "not_checked")
        self.assertEqual(report["checks"]["duplicate_run"], "not_checked")
        self.assertEqual(report["completed_records"], 0)
        state.save.assert_not_called()
        drive_class.return_value.upload.assert_not_called()

    def test_root_checks_ownership_location_and_state_without_mutation(self):
        state = State()
        drive = Drive(ENV, state)
        before = copy.deepcopy(state.data)
        drive.get = Mock(return_value=ROOT)
        drive.preflight(record_root=False)
        self.assertEqual(state.data, before)
        for change in [{"ownedByMe": False}, {"driveId": "shared-drive"}, {"trashed": True},
                       {"mimeType": "video/mp4"}, {"capabilities": {"canAddChildren": False}}]:
            drive.get.return_value = {**ROOT, **change}
            with self.assertRaises(SetupError) as ctx:
                drive.preflight(record_root=False)
            self.assertEqual(ctx.exception.details["code"], "root_folder_invalid")
            self.assertEqual(state.data, before)
        drive.get.return_value = ROOT
        state.data["root_fingerprint"] = "different-existing-root"
        with self.assertRaises(SetupError) as ctx:
            drive.preflight(record_root=False)
        self.assertEqual(ctx.exception.details["code"], "root_state_conflict")

    @patch("src.drive.backoff")
    @patch("src.drive.raw_http")
    def test_transient_oauth_failure_does_not_request_reauthorization(self, http, sleep):
        http.return_value = (503, {}, b'{"error_description":"private-canary"}')
        with self.assertRaises(HttpError) as ctx:
            Drive(ENV, State()).refresh()
        self.assertNotIn("canary", str(ctx.exception))
        self.assertEqual(http.call_count, 4)

    @patch("src.drive.raw_http")
    def test_malformed_oauth_response_is_sanitized(self, http):
        http.return_value = (200, {}, b'{"access_token":"private-canary","expires_in":"bad"}')
        with self.assertRaises(SetupError) as ctx:
            Drive(ENV, State()).refresh()
        self.assertEqual(ctx.exception.details["code"], "oauth_response_invalid")
        self.assertNotIn("canary", str(ctx.exception))

    def test_cli_readiness_preserves_existing_archive_summary_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path.cwd()
            try:
                os.chdir(tmp)
                Path("config").mkdir()
                Path("config/channels.yml").write_text("channels: []\n")
                Path("run-summary.json").write_text("prior archive result")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main(["--readiness", "--online"], {})
                report = json.loads(Path("readiness-summary.json").read_text())
                self.assertEqual(Path("run-summary.json").read_text(), "prior archive result")
                self.assertFalse(Path(".work").exists())
            finally:
                os.chdir(previous)
        self.assertEqual(code, 2)
        self.assertEqual(report["missing_fields"], list(REQUIRED))

    def test_upload_provenance_ignores_extraction_feed_and_invalid_dates(self):
        good = Downloader.public_fields({"id": "abcdefghijk", "upload_date": "20100506", "epoch": 1790000000})
        self.assertEqual(good["youtube_upload_date"], "2010-05-06")
        self.assertEqual(good["youtube_date_source"], "yt-dlp.upload_date")
        for date in [None, "20260230", "20040101", "22000101", "2026011"]:
            row = Downloader.public_fields({"id": "abcdefghijk", "upload_date": date, "epoch": 1790000000})
            self.assertNotIn("youtube_upload_date", row)
