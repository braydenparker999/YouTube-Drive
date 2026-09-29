"""RSS discovery only: no yt-dlp channel scans and no YouTube API key."""
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from .common import (
    CHANNEL_ID,
    DRIVE_ID,
    VIDEO_ID,
    ArchiveError,
    SetupError,
    backoff,
    destination_name,
    quality,
    raw_http,
    transient,
)

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def load_config(path):
    try:
        config = yaml.safe_load(Path(path).read_text())
        if not isinstance(config, dict) or not isinstance(config.get("channels"), list):
            raise ValueError
        options = config.get("settings", {})
        defaults = {"max_quality": 480, "max_videos_per_run": 25, "max_file_gib": 4,
                    "max_duration_seconds": 7200, "run_budget_minutes": 210, "max_attempts": 5}
        if not isinstance(options, dict) or set(options) - set(defaults):
            raise ValueError
        defaults.update(options)
        quality(defaults["max_quality"])
        for key, maximum in [("max_videos_per_run", 100), ("max_file_gib", 8),
                             ("max_duration_seconds", 14400), ("run_budget_minutes", 210), ("max_attempts", 50)]:
            if type(defaults[key]) is not int or not 1 <= defaults[key] <= maximum:
                raise ValueError
        seen = set()
        for ch in config["channels"]:
            if not isinstance(ch, dict) or type(ch.get("enabled", True)) is not bool:
                raise ValueError
            if not ch.get("enabled", True):
                continue
            if not CHANNEL_ID.fullmatch(ch.get("channel_id", "")) or ch["channel_id"] in seen:
                raise ValueError
            seen.add(ch["channel_id"])
            destination_name(ch["name"])
            destination_name(ch.get("drive_folder", ch["name"]))
            if ch.get("drive_folder_id") and not DRIVE_ID.fullmatch(ch["drive_folder_id"]):
                raise ValueError
        config["settings"] = defaults
        return config
    except (ValueError, TypeError, KeyError, OSError, yaml.YAMLError):
        raise SetupError("Invalid config/channels.yml: use unique stable UC channel IDs and the documented settings.") from None


def parse_feed(data, channel_id):
    if len(data) > 2_000_000 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ArchiveError("Invalid or oversized creator feed.")
    try:
        root = ET.fromstring(data)
        if root.tag != f"{{{NS['a']}}}feed" or root.findtext("yt:channelId", namespaces=NS) != channel_id:
            raise ValueError
        result = {}
        for entry in root.findall("a:entry", NS):
            vid = entry.findtext("yt:videoId", namespaces=NS) or ""
            owner = entry.findtext("yt:channelId", namespaces=NS)
            published = entry.findtext("a:published", namespaces=NS) or ""
            if not VIDEO_ID.fullmatch(vid) or owner != channel_id:
                raise ValueError
            datetime.fromisoformat(published.replace("Z", "+00:00"))
            result[vid] = {"video_id": vid, "channel_id": channel_id,
                           "title": (entry.findtext("a:title", namespaces=NS) or vid)[:500],
                           "published_at": published}
        return sorted(result.values(), key=lambda v: v["published_at"], reverse=True)[:15]
    except (ET.ParseError, ValueError):
        raise ArchiveError("Malformed creator feed or channel ID mismatch.") from None


def discover(channel_id):
    for attempt in range(3):
        try:
            status, _, body = raw_http("GET", "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id,
                                       timeout=30, limit=2_000_000)
        except ArchiveError:
            if attempt == 2:
                raise
            backoff(attempt + 1)
            continue
        if status == 200:
            return parse_feed(body, channel_id)
        if not transient(status) or attempt == 2:
            raise ArchiveError(f"Creator feed HTTP {status}; check channel ID or retry next run.")
        backoff(attempt + 1)
    return []
