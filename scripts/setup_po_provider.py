"""Start a pinned PO-token provider on runner loopback only, with no cloud secrets."""
import json
import subprocess
import sys
import time
from urllib import error, request

VERSION = '2.0.0'
subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                'bgutil-ytdlp-pot-provider==' + VERSION], check=True, timeout=180)
subprocess.run(['docker', 'run', '--name', 'youtube-archive-pot', '--detach', '--init',
                '--publish', '127.0.0.1:4416:4416',
                'brainicism/bgutil-ytdlp-pot-provider@sha256:ed86b6fdd5e430ddd7c8ce1adb55e1ab54db7c7dbc1bcbf3a82454a85b971164'], check=True, timeout=300)
for _attempt in range(40):
    try:
        with request.urlopen('http://127.0.0.1:4416/ping', timeout=1) as response:
            result = json.load(response)
        if result.get('version') != VERSION:
            raise SystemExit('PO-token provider returned an unexpected version.')
        print('[SETUP] PO-token provider ready on loopback; version=' + VERSION)
        break
    except (error.URLError, TimeoutError, OSError):
        time.sleep(1)
else:
    raise SystemExit('PO-token provider did not become healthy within 40 seconds.')
