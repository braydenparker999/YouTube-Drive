"""Shared pipeline. Workflow inputs are read as data, never shell commands."""
import argparse
import hashlib
import os
import signal
import tempfile
import time
from pathlib import Path

from .common import DRIVE_ID, ArchiveError, SetupError, StateError, log, parse_inputs, safe_filename
from .discover import discover, load_config
from .downloader import Downloader
from .drive import Drive
from .state import GitHubState, now, save_local

REQUIRED = ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN", "GDRIVE_ROOT_FOLDER_ID")


def check_setup(env):
    missing = [key for key in REQUIRED if not env.get(key, "").strip()]
    if missing:
        raise SetupError("Missing repository secrets: " + ", ".join(missing) + ". Complete README 'One-time setup' before running the archive.")
    if not DRIVE_ID.fullmatch(env["GDRIVE_ROOT_FOLDER_ID"]):
        raise SetupError("GDRIVE_ROOT_FOLDER_ID must be the folder ID, not a URL.")
    if not env.get("GITHUB_TOKEN") or not env.get("GITHUB_REPOSITORY"):
        raise SetupError("Run through GitHub Actions: its automatic GITHUB_TOKEN needs contents:write.")
    branch = env.get("ARCHIVE_BRANCH", "main")
    if env.get("GITHUB_REF") != "refs/heads/" + branch or env.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}:
        raise SetupError("Archive writes are allowed only for schedule/workflow_dispatch on the default branch.")


def checksum(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "md5").hexdigest()


def discover_queue(state, config, inputs, feed=discover):
    errors = []
    if inputs.ids:
        # New manual entries stay in memory until public metadata is verified.
        queue = [state.data["videos"].get(vid, {"video_id": vid, "source": "manual",
                 "destination": inputs.destination, "max_quality": inputs.max_quality}) for vid in inputs.ids]
        return queue, errors
    enabled = {c["channel_id"]: c for c in config["channels"] if c.get("enabled", True)}
    for ch in enabled.values():
        try:
            found = feed(ch["channel_id"])
            complete = sum(state.complete(v["video_id"]) for v in found)
            new = sum(v["video_id"] not in state.data["videos"] for v in found)
            for item in found:
                state.add({**item, "source": "channel", "destination": ch.get("drive_folder", ch["name"]),
                           "creator_folder_id": ch.get("drive_folder_id"),
                           "max_quality": config["settings"]["max_quality"]})
            state.save()  # Persist the entire discovered window, even if this run reaches its budget.
            log("DISCOVERY", creator=ch["name"], found=len(found), already_archived=complete, new=new)
        except StateError:
            raise
        except ArchiveError as exc:
            errors.append({"channel_id": ch["channel_id"], "error": str(exc)})
            log("DISCOVERY", creator=ch["name"], error=str(exc))
    queue = [v for v in state.data["videos"].values() if v["status"] != "complete" and
             (v.get("source") == "manual" or v.get("channel_id") in enabled)]
    # First attempts before repeatedly failing records, then oldest first.
    queue.sort(key=lambda v: (v.get("attempts", 0), v.get("published_at") or "", v["video_id"]))
    return queue, errors


def process_video(state, drive, downloader, item, workdir):
    vid = item["video_id"]
    if state.complete(vid):
        return {"video_id": vid, "status": "complete", "skipped": True,
                "drive_file_id": state.data["videos"][vid]["drive_file_id"]}
    # Covers a runner killed after final upload but before a completion checkpoint.
    if item.get("drive_file_id") and drive.verified(item):
        state.update(vid, status="complete", completed_at=now(), error=None)
        return {"video_id": vid, "status": "complete", "recovered": True, "drive_file_id": item["drive_file_id"]}
    info = downloader.metadata(vid, item["max_quality"])
    fields = downloader.public_fields(info)
    if item.get("source") == "channel" and fields.get("channel_id") != item.get("channel_id"):
        raise ArchiveError("Video channel disagrees with the creator feed; refusing to archive.")
    if vid not in state.data["videos"]:
        state.add({**item, **fields})
    item = state.data["videos"][vid]
    state.update(vid, **fields, status="downloading", attempts=item.get("attempts", 0) + 1, error=None)
    with tempfile.TemporaryDirectory(prefix=vid + "-", dir=workdir) as directory:
        path = downloader.download(vid, item["max_quality"], info, directory)
        state.update(vid, status="downloaded", size=path.stat().st_size, md5=checksum(path))
        parent = drive.folder(drive.root, item["destination"], item.get("creator_folder_id"))
        if item.get("source") == "channel":
            year = (item.get("published_at") or "unknown")[:4]
            parent = drive.folder(parent, year)
        if not item.get("drive_file_id"):
            state.update(vid, drive_file_id=drive.new_id())
        state.update(vid, status="uploading")
        name = safe_filename(item["title"], vid, item.get("published_at"), path.suffix.lstrip("."))
        log("UPLOAD", video_id=vid, destination=item["destination"], drive_file_id=item["drive_file_id"])
        drive.upload(path, item, parent, name)
        # Independently confirm at the pipeline boundary as well.
        if not drive.verified(item):
            raise ArchiveError("Upload did not yield a verifiable Drive file.")
        state.update(vid, status="complete", completed_at=now(), error=None)
        path.unlink()  # Only after Drive confirmation + successful durable completion.
        log("UPLOAD", video_id=vid, result="complete", drive_file_id=item["drive_file_id"])
    return {"video_id": vid, "status": "complete", "drive_file_id": item["drive_file_id"]}


def prepare_manual(state, downloader, queue, summary, checkpoint=lambda: None):
    """Durably queue confirmed public requests before spending the run on media."""
    ready = []
    for item in queue:
        vid = item["video_id"]
        if vid not in state.data["videos"]:
            try:
                info = downloader.metadata(vid, item["max_quality"])
                state.add({**item, **downloader.public_fields(info)})
                state.save()
            except StateError:
                raise
            except ArchiveError as exc:
                summary["videos"].append({"video_id": vid, "status": "failed", "error": str(exc)})
                checkpoint()
                continue
            time.sleep(5)
        ready.append(state.data["videos"][vid])
    return ready


def run_queue(state, drive, downloader, queue, settings, summary, workdir, checkpoint=lambda: None, started_at=None):
    start = time.monotonic() if started_at is None else started_at
    handled = 0
    for item in queue:
        vid = item["video_id"]
        if not state.complete(vid) and (handled >= settings["max_videos_per_run"] or
                time.monotonic() - start >= settings["run_budget_minutes"] * 60):
            summary["videos"].append({"video_id": vid, "status": "deferred"})
            continue
        try:
            result = process_video(state, drive, downloader, item, workdir)
        except StateError:
            raise  # A lost checkpoint is fatal; no further side effects.
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ArchiveError) else "Unexpected internal failure; inspect tests/code (" + type(exc).__name__ + ")."
            if vid in state.data["videos"]:
                state.update(vid, status="failed", error=reason)
            result = {"video_id": vid, "status": "failed", "error": reason}
            log("DOWNLOAD", video_id=vid, error=reason)
        summary["videos"].append(result)
        if not result.get("skipped"):
            handled += 1
        checkpoint()
        if handled and item is not queue[-1]:
            time.sleep(5)


def finish_summary(summary):
    videos = summary["videos"]
    summary["successful"] = sum(v["status"] == "complete" and not v.get("skipped") for v in videos)
    summary["skipped"] = sum(bool(v.get("skipped")) for v in videos)
    summary["failed"] = sum(v["status"] == "failed" for v in videos)
    summary["deferred"] = sum(v["status"] == "deferred" for v in videos)
    if summary["status"] not in {"setup_required", "fatal", "interrupted"}:
        summary["status"] = "partial" if summary["failed"] or summary["discovery_errors"] or summary["deferred"] else "complete"
    save_local("run-summary.json", summary)


def main(argv=None, env=None):
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-setup", action="store_true")
    parser.add_argument("--metadata-only", metavar="YOUTUBE_URL")
    args = parser.parse_args(argv)
    summary = {"request_id": "", "status": "running", "videos": [], "discovery_errors": []}
    code = 0
    write_summary = True
    started_at = time.monotonic()
    try:
        inputs = parse_inputs(env)
        summary["request_id"] = inputs.request_id
        config = load_config("config/channels.yml")
        if args.metadata_only:
            from .common import video_id
            downloader = Downloader(config["settings"], ".work", env)
            vid = video_id(args.metadata_only)
            info = downloader.metadata(vid, inputs.max_quality)
            log("METADATA", video_id=vid, **downloader.public_fields(info), format=info.get("format_id"))
            return 0
        check_setup(env)
        if args.check_setup:
            log("SETUP", status="required secrets present; no network calls made")
            write_summary = False
            return 0
        state = GitHubState(env["GITHUB_REPOSITORY"], env.get("ARCHIVE_BRANCH", "main"), env["GITHUB_TOKEN"])
        drive = Drive(env, state)
        drive.preflight()
        state.save()  # Prove write access BEFORE any download/upload.
        queue, summary["discovery_errors"] = discover_queue(state, config, inputs)
        if queue:
            Path(".work").mkdir(exist_ok=True)
            downloader = Downloader(config["settings"], ".work", env)
            def checkpoint():
                finish_summary(summary)
            if inputs.ids:
                queue = prepare_manual(state, downloader, queue, summary, checkpoint)
            run_queue(state, drive, downloader, queue, config["settings"], summary, ".work", checkpoint, started_at)
        else:
            log("DISCOVERY", queued=0, message="No pending videos; add enabled creators or supply manual URLs.")
    except SetupError as exc:
        summary.update(status="setup_required", error=str(exc))
        log("SETUP", error=str(exc))
        code = 2
    except KeyboardInterrupt:
        summary.update(status="interrupted", error="Run interrupted; durable checkpoints will be recovered on the next run.")
        code = 130
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ArchiveError) else "Unexpected internal failure (" + type(exc).__name__ + ")."
        summary.update(status="fatal", error=reason)
        log("ERROR", error=reason)
        code = 1
    finally:
        if write_summary:
            finish_summary(summary)
            log("SUMMARY", **summary)
        if write_summary and env.get("GITHUB_STEP_SUMMARY"):
            # Only fixed labels and numeric counts; no untrusted Markdown.
            with open(env["GITHUB_STEP_SUMMARY"], "a") as handle:
                handle.write("## YouTube archive\n\nStatus: " + summary["status"] + "\n\n")
                handle.write(" | ".join(f"{k}: {summary[k]}" for k in ["successful", "skipped", "failed", "deferred"]) + "\n")
    return code or (1 if summary["status"] == "partial" else 0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
