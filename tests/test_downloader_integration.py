"""Optional real yt-dlp/ffmpeg test against a tiny locally generated video."""
import contextlib
import functools
import hashlib
import http.server
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.downloader import Downloader


@unittest.skipUnless(os.environ.get('YTDRIVE_INTEGRATION') == '1', 'Set YTDRIVE_INTEGRATION=1 with yt-dlp and ffmpeg installed')
class DownloaderIntegration(unittest.TestCase):
    def test_real_download_from_local_generated_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.mp4'
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=10',
                            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.5', '-c:v', 'libx264',
                            '-c:a', 'aac', '-shortest', str(source)], check=True, timeout=30)
            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *_):
                    pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=tmp))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                directory = root / 'output'
                directory.mkdir()
                info = {'id': 'BaW_jenozKc', 'title': 'Generated test', 'url': f'http://127.0.0.1:{server.server_port}/source.mp4',
                        'ext': 'mp4', 'height': 64, 'width': 64, 'vcodec': 'h264', 'acodec': 'aac', 'format_id': 'test'}
                with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}):
                    downloader = Downloader({'max_file_gib': 1, 'max_duration_seconds': 7200}, directory)
                    result = downloader.download(info['id'], 1080, info, directory)
                self.assertEqual(hashlib.sha256(result.read_bytes()).digest(), hashlib.sha256(source.read_bytes()).digest())
                self.assertFalse((directory / 'metadata.json').exists())
                self.assertEqual(result.name, 'media.mp4')
            finally:
                server.shutdown()
                with contextlib.suppress(Exception):
                    server.server_close()
                thread.join(timeout=2)
