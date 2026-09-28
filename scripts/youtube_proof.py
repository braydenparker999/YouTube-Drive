"""Bounded real-YouTube download proof, without Drive or durable archive writes.

Invoke from the repository root: python -m scripts.youtube_proof
Only the sanitized proof JSON is suitable for public artifacts.
"""
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from src.common import ArchiveError, log
from src.downloader import Downloader, child_env

VIDEO_ID = 'jNQXAC9IVRw'  # 19-second public test fixture; never crawl a channel.


def verify_media(path, expected_duration):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,width,height',
         '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30, env=child_env())
    info = json.loads(result.stdout)
    streams = info.get('streams', [])
    duration = float(info.get('format', {}).get('duration', 0))
    if not {'video', 'audio'}.issubset({s.get('codec_type') for s in streams}):
        raise ArchiveError('Proof requires both audio and video streams.')
    if abs(duration - expected_duration) > max(2, expected_duration * 0.05):
        raise ArchiveError('Proof file duration does not match the complete video.')
    # Decode the entire small file; a valid header alone is not proof of a complete download.
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                   capture_output=True, check=True, timeout=120, env=child_env())
    return {'duration_seconds': duration, 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'full_decode': True,
            'streams': streams}


def main():
    report = {'video_id': VIDEO_ID, 'status': 'failed', 'stage': 'initialization',
              'commit': os.environ.get('GITHUB_SHA', ''), 'run_id': os.environ.get('GITHUB_RUN_ID', '')}
    try:
        with tempfile.TemporaryDirectory(prefix='youtube-proof-') as tmp:
            downloader = Downloader({'max_file_gib': 0.125, 'max_duration_seconds': 120}, tmp, diagnostics=True)
            report['yt_dlp_version'] = downloader.version
            report['stage'] = 'metadata'
            info = downloader.metadata(VIDEO_ID, 360)
            report['stage'] = 'download'
            media = downloader.download(VIDEO_ID, 360, info, tmp)
            report['stage'] = 'decode'
            report.update(verify_media(media, info['duration']))
            report.update(status='complete', stage='verified')
    except ArchiveError as exc:
        report['error'] = str(exc)
    except (OSError, subprocess.SubprocessError, ValueError):
        report['error'] = 'Proof runtime or full-media validation failed.'
    Path('youtube-proof.json').write_text(json.dumps(report, indent=2) + '\n')
    log('PROOF', **report)
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
