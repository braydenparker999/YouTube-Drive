"""One conservative yt-dlp implementation for scheduled and manual jobs."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .common import ArchiveError, log


def child_env():
    # yt-dlp/JS/ffmpeg never receive Google/GitHub credentials.
    allowed = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "SSL_CERT_FILE",
               "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy", "PYTHONPATH"}
    return {k: v for k, v in os.environ.items() if k in allowed}


def classify_error(text):
    lowered = text.lower()
    cases = [("429", "YouTube HTTP 429: rate limited; leave pacing enabled and retry a later run."),
             ("403", "YouTube HTTP 403: extraction/egress rejected; see README PO-token guidance."),
             ("407", "Egress proxy authentication failed (HTTP 407)."),
             ("sign in", "YouTube requires sign-in or blocked this runner IP; no cookies configured."),
             ("not available", "Video/format unavailable in this region or at this time."),
             ("unavailable", "Video is unavailable; verify the URL still names a public video."),
             ("removed", "Video has been removed; verify the URL."),
             ("private", "Private video cannot be archived by this public-metadata system."),
             ("requested format", "No format matches the selected maximum quality."),
             ("javascript", "YouTube JavaScript extraction failed; check Deno and yt-dlp-ejs versions."),
             ("no space", "Runner disk full; reduce file size/quality limit."),
             ("timed out", "YouTube request timed out after bounded retries."),
             ("unsupported url", "yt-dlp rejected the video URL."),
             ("proxy", "Network/proxy connection failed; verify egress availability.")]
    for needle, message in cases:
        if needle in lowered:
            return message
    status = re.search(r"http (?:error )?(\d{3})", lowered)
    if status:
        return f"YouTube/network HTTP {status[1]}; inspect runner egress and upstream availability."
    for needle in ["certificate verify failed", "connection refused", "name resolution", "connection reset", "remote end closed", "no module named"]:
        if needle in lowered:
            return "yt-dlp environment/network failure: " + needle + "."
    return "yt-dlp extraction/download failed; inspect version, public availability, runtime and egress; see README."


class Downloader:
    def __init__(self, settings, workdir, env=None):
        self.settings, self.workdir = settings, Path(workdir)
        self.env = env if env is not None else os.environ
        self.metadata_cache = {}
        self.version = self.run(["--version"], timeout=30).strip()
        log("DOWNLOAD", yt_dlp_version=self.version)

    def run(self, args, timeout=3600):
        process = subprocess.Popen([sys.executable, "-m", "yt_dlp", *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=child_env(), start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ArchiveError("yt-dlp exceeded its time budget; retry on the next run.") from None
        finally:
            # Kill ffmpeg/JS children as well on timeout or cancellation (Linux runner).
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        if process.returncode:
            raise ArchiveError(classify_error(stderr))
        return stdout

    def options(self, height):
        opts = ["--ignore-config", "--no-playlist", "--no-progress", "--no-colors", "--no-warnings",
                "--no-overwrites", "--continue", "--force-ipv4", "--concurrent-fragments", "1",
                "--retries", "3", "--fragment-retries", "3", "--extractor-retries", "2",
                "--socket-timeout", "30", "--retry-sleep", "http:exp=2:20", "--retry-sleep", "fragment:exp=2:20",
                "--sleep-requests", "1", "--sleep-interval", "5", "--max-sleep-interval", "10",
                "--abort-on-unavailable-fragments", "--no-cache-dir", "--no-write-info-json",
                "--max-filesize", str(self.settings["max_file_gib"] * 1024**3),
                "--format", f"bv*[height<={height}]+ba/b[height<={height}]",
                "--merge-output-format", "mp4/mkv", "--js-runtimes", "deno"]
        # Trusted maintainer secret only; never supplied through workflow_dispatch.
        # This one extension point can enable mweb + an installed PO-token provider.
        if self.env.get("YTDLP_EXTRACTOR_ARGS"):
            opts += ["--extractor-args", self.env["YTDLP_EXTRACTOR_ARGS"]]
        if self.env.get("YTDLP_PROXY"):
            opts += ["--proxy", self.env["YTDLP_PROXY"]]
        return opts

    def metadata(self, vid, height):
        cached = getattr(self, "metadata_cache", {}).get((vid, height))
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        raw = self.run(self.options(height) + ["--skip-download", "--dump-single-json",
                                              "--", "https://www.youtube.com/watch?v=" + vid], timeout=240)
        try:
            info = json.loads(raw)
        except ValueError:
            raise ArchiveError("yt-dlp returned invalid metadata.") from None
        if info.get("id") != vid or info.get("_type", "video") != "video":
            raise ArchiveError("yt-dlp returned an unexpected video or playlist.")
        if info.get("availability") != "public":
            raise ArchiveError("Only confirmed public videos are supported; private/unlisted metadata must not enter this public repository.")
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            raise ArchiveError("Live/upcoming/processing video deferred until a finished public VOD is available.")
        duration = info.get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0 or duration > self.settings["max_duration_seconds"]:
            raise ArchiveError("Video duration is unknown or exceeds configured max_duration_seconds.")
        self.metadata_cache[(vid, height)] = (time.monotonic(), info)
        return info

    @staticmethod
    def public_fields(info):
        date = info.get("upload_date", "")
        try:
            published = datetime.strptime(date, "%Y%m%d").date().isoformat()
        except (ValueError, TypeError):
            published = None
        return {"title": str(info.get("title") or info["id"])[:500], "channel_id": info.get("channel_id"),
                "published_at": published}

    def download(self, vid, height, info, directory):
        directory = Path(directory).resolve()
        limit = self.settings["max_file_gib"] * 1024**3
        if shutil.disk_usage(directory).free < limit * 2 + 512 * 1024**2:
            raise ArchiveError("Insufficient runner disk for media + ffmpeg merge; lower max_file_gib.")
        meta = directory / "metadata.json"
        meta.write_text(json.dumps(info))
        meta.chmod(0o600)
        log("DOWNLOAD", video_id=vid, title=info.get("title"), format=info.get("format_id"), max_quality=height)
        try:
            self.run(self.options(height) + ["--load-info-json", str(meta), "--output", str(directory / "media.%(ext)s")])
        finally:
            meta.unlink(missing_ok=True)
        files = [p for p in directory.iterdir() if p.name in {"media.mp4", "media.mkv", "media.webm"}]
        if len(files) != 1 or not 0 < files[0].stat().st_size <= limit:
            raise ArchiveError("Download is incomplete, ambiguous, or exceeds the configured size limit.")
        try:
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                                     "-of", "json", str(files[0])], capture_output=True, text=True,
                                    timeout=30, env=child_env(), check=True)
            if not any(s.get("codec_type") == "video" for s in json.loads(result.stdout).get("streams", [])):
                raise ValueError
        except (ValueError, subprocess.SubprocessError, OSError):
            raise ArchiveError("Downloaded media failed ffprobe validation.") from None
        return files[0]
