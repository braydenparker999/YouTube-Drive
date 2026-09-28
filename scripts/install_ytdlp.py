"""Install one nightly per run, or an explicitly pinned rollback version."""
import os
import re
import subprocess
import sys

version = os.environ.get("YTDLP_VERSION", "").strip()
if version and not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,60}", version):
    raise SystemExit("YTDLP_VERSION must be a package version, not a pip option or URL.")
package = "yt-dlp[default]" + ("==" + version if version else "")
subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "--pre", package], check=True)
subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], check=True)
