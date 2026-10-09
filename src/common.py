"""Safe inputs, filenames, logging, and bounded HTTP primitives."""
import json
import re
import socket
import time
import unicodedata
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import parse_qs, urlsplit

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")


class ArchiveError(Exception):
    """Only fixed, credential-free messages may enter public logs/state."""


class SetupError(ArchiveError):
    def __init__(self, message, *, code="setup_required", missing_fields=(), invalid_fields=(), owner_actions=()):
        super().__init__(message)
        self.details = {"code": code, "missing_fields": list(missing_fields),
                        "invalid_fields": list(invalid_fields), "owner_actions": list(owner_actions)}


class StateError(ArchiveError):
    pass


class HttpError(ArchiveError):
    def __init__(self, status, code=""):
        self.status, self.code = status, code
        super().__init__(f"HTTP {status}" + (f" ({code})" if code else ""))


def log(stage, **values):
    # JSON escaping also prevents feed titles from injecting Actions commands/newlines.
    print(f"[{stage}] " + json.dumps(values, ensure_ascii=True), flush=True)


def video_id(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise SetupError("URLs must be YouTube video URLs, at most 2048 characters each.")
    try:
        p = urlsplit(url.strip())
        if p.scheme != "https" or p.username or p.password or p.port:
            raise ValueError
        parts = p.path.strip("/").split("/")
        if p.hostname == "youtu.be" and len(parts) == 1:
            result = parts[0]
        elif p.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if p.path == "/watch":
                values = parse_qs(p.query).get("v", [])
                if len(values) != 1:
                    raise ValueError
                result = values[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                result = parts[1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not VIDEO_ID.fullmatch(result):
            raise ValueError
        return result
    except ValueError:
        raise SetupError("Only HTTPS YouTube watch, shorts, live, embed, or youtu.be video URLs are accepted.") from None


def destination_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\w][\w .()-]{0,79}", value, re.UNICODE):
        raise SetupError("Destination must be one folder name (1–80 letters, numbers, spaces, .()-_); no paths.")
    if value.endswith((" ", ".")) or ".." in value:
        raise SetupError("Destination cannot contain '..' or end in a space/dot.")
    return value


def quality(value):
    if str(value) not in {"360", "480", "720", "1080", "1440", "2160"}:
        raise SetupError("max_quality must be 360, 480, 720, 1080, 1440, or 2160.")
    return int(value)


@dataclass
class Inputs:
    ids: list
    destination: str
    max_quality: int
    request_id: str


def parse_inputs(env):
    raw = env.get("INPUT_URLS", "").strip()
    if len(raw) > 30000:
        raise SetupError("URL input is too large (maximum 30,000 characters).")
    if raw.startswith("["):
        try:
            urls = json.loads(raw)
        except ValueError:
            raise SetupError("URLs JSON must be an array of URL strings.") from None
        if not isinstance(urls, list):
            raise SetupError("URLs JSON must be an array.")
    else:
        urls = raw.split()
    if len(urls) > 25:
        raise SetupError("At most 25 URLs may be requested per run.")
    ids = list(dict.fromkeys(video_id(u) for u in urls))
    req = env.get("INPUT_REQUEST_ID", "").strip()
    if req and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", req):
        raise SetupError("request_id must be 1–80 letters, digits, dots, underscores, or hyphens.")
    return Inputs(ids, destination_name(env.get("INPUT_DESTINATION") or "Requested"),
                  quality(env.get("INPUT_MAX_QUALITY") or "480"), req)


def safe_filename(title, vid, published, extension):
    if not VIDEO_ID.fullmatch(vid) or extension not in {"mp4", "mkv", "webm"}:
        raise ArchiveError("Invalid media filename fields.")
    title = unicodedata.normalize("NFC", str(title))
    title = "".join(" " if unicodedata.category(c).startswith("C") or c in '<>:"/\\|?*' else c for c in title)
    title = " ".join(title.split()).strip(" .") or "Untitled"
    title = title.encode("utf-8")[:160].decode("utf-8", "ignore").rstrip(" .")
    date = published[:10] if published and re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", published) else "unknown-date"
    return f"{date} - {title} [{vid}].{extension}"


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def raw_http(method, url, *, headers=None, body=None, timeout=60, limit=20_000_000):
    """Never follow credential-bearing redirects or expose response bodies in errors."""
    req = request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
            data = response.read(limit + 1)
            status, hdrs = response.status, dict(response.headers)
    except error.HTTPError as exc:
        data, status, hdrs = exc.read(limit + 1), exc.code, dict(exc.headers)
    except (error.URLError, TimeoutError, socket.timeout, OSError):
        raise HttpError(0, "network_or_timeout") from None
    if len(data) > limit:
        raise ArchiveError("HTTP response exceeded the allowed size.")
    return status, {k.lower(): v for k, v in hdrs.items()}, data


def error_code(body):
    # Return only known enum values, never server-provided free text.
    try:
        obj = json.loads(body)
        err = obj.get("error", {})
        codes = [err] if isinstance(err, str) else [x.get("reason") for x in err.get("errors", [])]
    except (ValueError, AttributeError, TypeError):
        return ""
    allowed = {"invalid_grant", "invalid_client", "rateLimitExceeded", "userRateLimitExceeded",
               "storageQuotaExceeded", "dailyLimitExceeded", "insufficientPermissions", "notFound"}
    return next((c for c in codes if c in allowed), "")


def transient(status, code=""):
    return status in {0, 408, 429, 500, 502, 503, 504} or code in {"rateLimitExceeded", "userRateLimitExceeded"}


def backoff(attempt):
    time.sleep(min(2 ** attempt, 32))
