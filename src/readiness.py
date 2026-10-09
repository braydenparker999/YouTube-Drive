"""Credential-free setup diagnostics; online checks never checkpoint or upload."""
import re

from .common import DRIVE_ID, ArchiveError, SetupError

REQUIRED = ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN", "GDRIVE_ROOT_FOLDER_ID")
CONFIGURE = "Owner: supply the existing authorized Google OAuth client, refresh token, and My Drive folder via repository secrets (README One-time setup)."


def check_configuration(env):
    missing, invalid = [], []
    for key in REQUIRED:
        value = env.get(key, "")
        if not value or isinstance(value, str) and not value.strip():
            missing.append(key)
        elif not isinstance(value, str) or re.search(r"\s", value):
            invalid.append(key)
    if "GDRIVE_ROOT_FOLDER_ID" not in missing + invalid and not DRIVE_ID.fullmatch(env["GDRIVE_ROOT_FOLDER_ID"]):
        invalid.append("GDRIVE_ROOT_FOLDER_ID")
    if missing or invalid:
        message = "Missing repository secrets: " + ", ".join(missing) + ". " if missing else ""
        if invalid:
            message += "Invalid repository fields: " + ", ".join(invalid) + "; use exact credentials and a folder ID, not a URL. "
        raise SetupError(message + "Complete README 'One-time setup' before running the archive.",
                         code="configuration_required", missing_fields=missing, invalid_fields=invalid,
                         owner_actions=[CONFIGURE])


def readiness(env, config, *, online=False):
    report = {"version": 1, "mode": "read_only_online" if online else "offline",
              "status": "configuration_present", "missing_fields": [], "invalid_fields": [],
              "owner_actions": [], "checks": {"configuration": "not_checked", "google_folder": "not_checked",
              "archive_state_read": "not_checked", "checkpoint_write": "not_checked",
              "first_upload": "not_checked", "duplicate_run": "not_checked"},
              "enabled_creators": sum(c.get("enabled", True) for c in config["channels"])}
    try:
        report["checks"]["configuration"] = "checking"
        check_configuration(env)
        report["checks"]["configuration"] = "passed"
        if online:
            # Only GETs to GitHub/Drive and a refresh of EXISTING OAuth authorization.
            # Do not save state, generate IDs, create folders, or initialize a downloader.
            from .drive import Drive
            from .state import GitHubState
            missing = [k for k in ("GITHUB_TOKEN", "GITHUB_REPOSITORY") if not env.get(k)]
            if missing:
                raise SetupError("Online readiness needs existing GitHub read access to archive state.",
                                 code="state_read_configuration_required", missing_fields=missing,
                                 owner_actions=["Owner: run the read-only check with existing authorized repository read access."])
            report["checks"]["archive_state_read"] = "checking"
            state = GitHubState(env["GITHUB_REPOSITORY"], env.get("ARCHIVE_BRANCH", "main"), env["GITHUB_TOKEN"])
            report["checks"]["archive_state_read"] = "passed"
            report["completed_records"] = sum(v["status"] == "complete" for v in state.data["videos"].values())
            report["checks"]["google_folder"] = "checking"
            Drive(env, state).preflight(record_root=False)
            report["checks"]["google_folder"] = "passed"
            report["status"] = "ready_for_authorized_test"
        report["owner_actions"] = ["Owner: review and explicitly authorize one short public-video upload and the identical duplicate request to establish end-to-end acceptance."]
        if not report["enabled_creators"]:
            report["owner_actions"].append("Owner: choose actual creator channel IDs before scheduled creator acceptance; the configured creator list is empty.")
        return report, 0
    except SetupError as exc:
        report["checks"] = {k: "failed" if v == "checking" else v for k, v in report["checks"].items()}
        report.update(status="setup_required", error=str(exc), **exc.details)
        return report, 2
    except ArchiveError as exc:
        report["checks"] = {k: "failed" if v == "checking" else v for k, v in report["checks"].items()}
        report.update(status="blocked", error=str(exc), code="readiness_check_failed",
                      owner_actions=["Owner: review the failed read-only check and existing access/upstream availability before retrying. No write or upload was tested."])
        return report, 1
