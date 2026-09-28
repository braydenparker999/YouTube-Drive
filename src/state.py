"""GitHub JSON checkpoints with optimistic locking, before external side effects."""
import base64
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .common import VIDEO_ID, HttpError, StateError, backoff, raw_http, transient

STATUSES = {"discovered", "downloading", "downloaded", "uploading", "complete", "failed"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_state():
    return {"version": 1, "videos": {}, "folders": {}}


def validate(data):
    try:
        if data["version"] != 1 or not isinstance(data["videos"], dict) or not isinstance(data["folders"], dict):
            raise ValueError
        for vid, item in data["videos"].items():
            if not VIDEO_ID.fullmatch(vid) or item["video_id"] != vid or item["status"] not in STATUSES:
                raise ValueError
            if item["status"] == "complete" and not (item.get("drive_file_id") and item.get("completed_at")):
                raise ValueError
        return data
    except (KeyError, TypeError, ValueError):
        raise StateError("Archive state is malformed; refusing to reset it or risk duplicates.") from None


class State:
    def __init__(self, data=None, saver=None):
        self.data = validate(data if data is not None else empty_state())
        self.saver = saver or (lambda data: None)

    def save(self):
        validate(self.data)
        self.saver(copy.deepcopy(self.data))

    def add(self, item):
        vid = item["video_id"]
        if vid not in self.data["videos"]:
            self.data["videos"][vid] = {"channel_id": None, "title": None, "published_at": None,
                                        "drive_file_id": None, "completed_at": None,
                                        **item, "status": "discovered", "updated_at": now()}

    def update(self, vid, **values):
        self.data["videos"][vid].update(values, updated_at=now())
        self.save()

    def complete(self, vid):
        return self.data["videos"].get(vid, {}).get("status") == "complete"


class GitHubState(State):
    def __init__(self, repository, branch, token):
        self.url = f"https://api.github.com/repos/{repository}/contents/state/archive.json"
        self.branch = branch
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "youtube-drive-archiver"}
        obj = self.read_remote()
        self.sha = obj["sha"]
        super().__init__(self.decode(obj), self.write_remote)

    @staticmethod
    def decode(obj):
        try:
            return validate(json.loads(base64.b64decode(obj["content"])))
        except (KeyError, ValueError):
            raise StateError("Cannot decode remote state; refusing to start.") from None

    def read_remote(self):
        for attempt in range(4):
            try:
                status, _, body = raw_http("GET", self.url + "?ref=" + quote(self.branch, safe=""), headers=self.headers)
            except HttpError as exc:
                status, body = exc.status, b""
            if status == 200:
                obj = json.loads(body)
                if obj.get("encoding") == "none":
                    # Contents API stops embedding data above 1 MiB. Fetch the
                    # immutable blob by SHA so metadata and contents cannot race.
                    blob_url = self.url.split("/contents/")[0] + "/git/blobs/" + obj["sha"]
                    try:
                        status, _, raw = raw_http("GET", blob_url,
                            headers={**self.headers, "Accept": "application/vnd.github.raw+json"})
                    except HttpError as exc:
                        status, raw = exc.status, b""
                    if status == 200:
                        obj["content"] = base64.b64encode(raw).decode()
                    else:
                        if transient(status) and attempt < 3:
                            backoff(attempt + 1)
                            continue
                        raise StateError(f"Cannot read large archive state (HTTP {status}).")
                return obj
            if not transient(status) or attempt == 3:
                raise StateError(f"Cannot read archive state (HTTP {status}); check repository permissions/state file.")
            backoff(attempt + 1)

    def write_remote(self, data):
        encoded = base64.b64encode((json.dumps(data, indent=2, sort_keys=True) + "\n").encode()).decode()
        payload = {"message": "chore(state): checkpoint archive [skip ci]", "branch": self.branch,
                   "sha": self.sha, "content": encoded}
        for attempt in range(4):
            try:
                status, _, body = raw_http("PUT", self.url, headers={**self.headers, "Content-Type": "application/json"},
                                           body=json.dumps(payload).encode())
            except HttpError as exc:
                status, body = exc.status, b""
            if status in {200, 201}:
                self.sha = json.loads(body)["content"]["sha"]
                return
            # A timed-out PUT may have committed. Reconcile before retrying.
            if status in {409, 422} or transient(status):
                remote = self.read_remote()
                if self.decode(remote) == data:
                    self.sha = remote["sha"]
                    return
                if remote["sha"] != self.sha:
                    raise StateError("Concurrent state modification detected; stopped before further uploads.")
                if attempt < 3:
                    backoff(attempt + 1)
                    continue
            raise StateError(f"Cannot checkpoint archive state (HTTP {status}); check Actions contents:write and branch rules.")


def save_local(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n")
    temp.replace(path)
