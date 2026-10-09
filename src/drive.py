"""OAuth refresh and resumable uploads using pre-reserved Drive IDs."""
import hashlib
import json
import mimetypes
import time
from urllib.parse import urlencode, urlsplit

from .common import DRIVE_ID, ArchiveError, HttpError, SetupError, backoff, error_code, raw_http, transient

BASE = "https://www.googleapis.com/drive/v3"
FIELDS = "id,name,mimeType,size,md5Checksum,trashed,parents,appProperties,ownedByMe,driveId,capabilities(canAddChildren)"
FOLDER = "application/vnd.google-apps.folder"


class Drive:
    def __init__(self, env, state):
        self.client_id = env["GDRIVE_CLIENT_ID"]
        self.client_secret = env["GDRIVE_CLIENT_SECRET"]
        self.refresh_token = env["GDRIVE_REFRESH_TOKEN"]
        self.root = env["GDRIVE_ROOT_FOLDER_ID"]
        self.state = state
        self.access_token, self.expires_at = "", 0

    def refresh(self):
        for attempt in range(4):
            try:
                status, _, body = raw_http("POST", "https://oauth2.googleapis.com/token",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    body=urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                                    "refresh_token": self.refresh_token, "grant_type": "refresh_token"}).encode())
            except HttpError as exc:
                status, body = exc.status, b""
            if status == 200:
                try:
                    obj = json.loads(body)
                    token = obj["access_token"]
                    expires = int(obj.get("expires_in", 3600))
                    if not isinstance(token, str) or not token or expires <= 120:
                        raise ValueError
                except (ValueError, TypeError, KeyError):
                    raise SetupError("Google OAuth returned an unusable token response; no response contents were logged.",
                                     code="oauth_response_invalid", owner_actions=["Owner: inspect existing Google client authorization before retrying."]) from None
                self.access_token = token
                self.expires_at = time.monotonic() + expires - 120
                return
            code = error_code(body)
            if not transient(status, code) or attempt == 3:
                if transient(status, code):
                    raise HttpError(status, code)
                fields = ("GDRIVE_REFRESH_TOKEN",) if code == "invalid_grant" else ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET")
                raise SetupError(f"Google OAuth refresh failed (HTTP {status}, {code or 'authorization_error'}). Review existing production authorization; see README.",
                                 code="oauth_authorization_required", invalid_fields=fields,
                                 owner_actions=["Owner: verify the matching existing OAuth client and production refresh token. Reauthorization requires separate approval."])
            backoff(attempt + 1)

    def raw(self, method, url, headers=None, body=None):
        for auth_attempt in range(2):
            if time.monotonic() >= self.expires_at:
                self.refresh()
            result = raw_http(method, url, headers={**(headers or {}), "Authorization": "Bearer " + self.access_token}, body=body)
            if result[0] != 401 or auth_attempt:
                return result
            self.expires_at = 0
        raise SetupError("Google authorization failed.")

    def api(self, method, path, data=None, *, missing_ok=False):
        url = BASE + path
        body = json.dumps(data).encode() if data is not None else None
        for attempt in range(5):
            try:
                status, _, result = self.raw(method, url, {"Content-Type": "application/json"}, body)
            except HttpError as exc:
                status, result = exc.status, b""
            if 200 <= status < 300:
                return json.loads(result) if result else {}
            if status == 404 and missing_ok:
                return None
            code = error_code(result)
            if not transient(status, code) or attempt == 4:
                raise HttpError(status, code)
            backoff(attempt + 1)

    def get(self, fid):
        if not DRIVE_ID.fullmatch(fid):
            raise ArchiveError("Invalid Drive file ID in state/config.")
        return self.api("GET", f"/files/{fid}?" + urlencode({"fields": FIELDS}), missing_ok=True)

    def new_id(self):
        return self.api("GET", "/files/generateIds?count=1&space=drive&type=files")["ids"][0]

    def preflight(self, *, record_root=True):
        try:
            root = self.get(self.root)
        except HttpError as exc:
            if exc.status not in {401, 403}:
                raise
            raise SetupError(f"Configured Drive root could not be inspected (HTTP {exc.status}); check existing account and folder access.",
                             code="root_access_required", invalid_fields=["GDRIVE_ROOT_FOLDER_ID"],
                             owner_actions=["Owner: verify that the existing OAuth account owns the configured My Drive folder."]) from None
        if (not root or root.get("trashed") or root.get("mimeType") != FOLDER or root.get("driveId") or
                root.get("ownedByMe") is not True or not root.get("capabilities", {}).get("canAddChildren")):
            raise SetupError("GDRIVE_ROOT_FOLDER_ID must identify a writable My Drive folder owned by your Google account.",
                             code="root_folder_invalid", invalid_fields=["GDRIVE_ROOT_FOLDER_ID"],
                             owner_actions=["Owner: verify the folder ID, ownership, My Drive location and writable capability without changing sharing settings."])
        fingerprint = hashlib.sha256(self.root.encode()).hexdigest()
        previous = self.state.data.get("root_fingerprint")
        if previous and fingerprint != previous:
            raise SetupError("Drive root changed. Keep the existing root or deliberately migrate state before changing it.",
                             code="root_state_conflict", invalid_fields=["GDRIVE_ROOT_FOLDER_ID"],
                             owner_actions=["Owner: restore the existing root configuration or review a separate state migration. Do not reset archive state."])
        if record_root:
            self.state.data["root_fingerprint"] = fingerprint

    def folder(self, parent, name, existing_id=None):
        if existing_id:
            obj = self.get(existing_id)
            if not obj or obj.get("trashed") or obj.get("mimeType") != FOLDER or parent not in obj.get("parents", []):
                raise ArchiveError("Configured creator folder must be an existing child of the archive root.")
            return existing_id
        key = hashlib.sha256((parent + "\0" + name).encode()).hexdigest()
        folders = self.state.data["folders"]
        if key not in folders:
            # Adopt a unique existing folder, once; subsequent runs use its ID.
            escaped = name.replace("\\", "\\\\").replace("'", "\\'")
            q = f"'{parent}' in parents and name = '{escaped}' and mimeType = '{FOLDER}' and trashed = false"
            found = self.api("GET", "/files?" + urlencode({"q": q, "fields": "files(id),nextPageToken", "pageSize": 2}))
            if len(found.get("files", [])) > 1 or found.get("nextPageToken"):
                raise ArchiveError("Multiple destination folders have the same name; set drive_folder_id or remove ambiguity.")
            folders[key] = found["files"][0]["id"] if found.get("files") else self.new_id()
            self.state.save()  # Reservation MUST be durable before creation.
        fid = folders[key]
        obj = self.get(fid)
        if obj is None:
            try:
                self.api("POST", "/files?fields=id", {"id": fid, "name": name, "mimeType": FOLDER, "parents": [parent]})
            except HttpError as exc:
                if exc.status != 409:
                    raise
            obj = self.get(fid)
        if not obj or obj.get("trashed") or obj.get("mimeType") != FOLDER or parent not in obj.get("parents", []):
            raise ArchiveError("Saved destination folder is missing, moved, or trashed; repair it before retrying.")
        return fid

    def verified(self, item):
        fid = item.get("drive_file_id")
        if not fid:
            return None
        obj = self.get(fid)
        if obj is None:
            return None
        if obj.get("trashed"):
            raise ArchiveError("Reserved Drive file is trashed; restore it before retrying.")
        if (obj.get("appProperties", {}).get("youtube_video_id") != item["video_id"] or
            str(obj.get("size")) != str(item.get("size")) or
            obj.get("md5Checksum") != item.get("md5") or
            not item.get("md5")):
            raise ArchiveError("Drive file verification mismatch; refusing to mark complete or create another copy.")
        return obj

    @staticmethod
    def valid_session(url):
        p = urlsplit(url)
        if p.scheme != "https" or p.hostname != "www.googleapis.com" or p.port or p.username or not p.path.startswith("/upload/drive/"):
            raise ArchiveError("Drive returned an unexpected resumable upload URL.")
        return url

    @staticmethod
    def offset(headers):
        value = headers.get("range", "")
        if not value:
            return 0
        import re
        match = re.fullmatch(r"bytes=0-(\d+)", value)
        if not match:
            raise ArchiveError("Invalid resumable upload offset from Drive.")
        return int(match[1]) + 1

    def upload(self, path, item, parent, name):
        """Retry chunks/status probes; restart expired sessions using the SAME reserved ID."""
        if self.verified(item):
            return item["drive_file_id"]
        size = path.stat().st_size
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        deadline = time.monotonic() + 3000
        for session_attempt in range(3):
            if time.monotonic() > deadline:
                raise ArchiveError("Drive upload time budget exhausted; safe to retry next run.")
            metadata = {"id": item["drive_file_id"], "name": name, "parents": [parent],
                        "appProperties": {"youtube_video_id": item["video_id"]}}
            try:
                status, headers, body = self.raw("POST", "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id",
                    {"Content-Type": "application/json", "X-Upload-Content-Type": mime,
                     "X-Upload-Content-Length": str(size)}, json.dumps(metadata).encode())
            except HttpError:
                status, headers, body = 0, {}, b""
            if status == 409:
                if self.verified(item):
                    return item["drive_file_id"]
                raise ArchiveError("Drive ID conflict without a verifiable file.")
            if status not in {200, 201}:
                if transient(status, error_code(body)):
                    backoff(session_attempt + 1)
                    continue
                raise HttpError(status, error_code(body))
            session = self.valid_session(headers.get("location", ""))
            offset, failures, probe = 0, 0, False
            with path.open("rb") as handle:
                while time.monotonic() < deadline:
                    if probe:
                        chunk, content_range = b"", f"bytes */{size}"
                    else:
                        handle.seek(offset)
                        chunk = handle.read(8 * 1024 * 1024)
                        content_range = f"bytes {offset}-{offset + len(chunk) - 1}/{size}"
                    try:
                        status, headers, body = self.raw("PUT", session,
                            {"Content-Length": str(len(chunk)), "Content-Range": content_range, "Content-Type": mime}, chunk)
                    except HttpError:
                        status, headers, body = 0, {}, b""
                    if status in {200, 201}:
                        if self.verified(item):
                            return item["drive_file_id"]
                        raise ArchiveError("Drive upload finished but the file was not found for verification.")
                    if status == 308:
                        new_offset = self.offset(headers)
                        if new_offset < offset or new_offset >= size:
                            raise ArchiveError("Unexpected Drive upload progress; safe to retry next run.")
                        # A status probe can report no progress after an interrupted chunk.
                        failures = 0 if new_offset > offset else failures + 1
                        offset, probe = new_offset, False
                        if failures >= 5:
                            break
                        continue
                    if status in {404, 410}:
                        break  # Expired session; reserve no new file ID.
                    failures += 1
                    if not transient(status, error_code(body)) or failures >= 5:
                        # The last chunk may have succeeded despite a lost response.
                        if self.verified(item):
                            return item["drive_file_id"]
                        raise HttpError(status, error_code(body))
                    backoff(failures)
                    probe = True
            if self.verified(item):
                return item["drive_file_id"]
        raise ArchiveError("Drive upload retries exhausted; reserved file ID retained for recovery.")
