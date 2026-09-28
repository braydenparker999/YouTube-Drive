"""Install the experimental browser provider with a bounded cold-start fix.

nodriver 0.50.3 waits only 2.75 seconds for Chrome. A hosted runner cold start
was observed to succeed after that deadline. Change only this wait to 30.25s;
keep the Chrome sandbox enabled and fail closed if upstream source differs.
"""
import importlib.metadata
import subprocess
import sys
from pathlib import Path


def main():
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'yt-dlp-getpot-wpc==1.1.2'],
                   check=True, timeout=180)
    if importlib.metadata.version('nodriver') != '0.50.3':
        raise SystemExit('Unexpected nodriver version; refusing to apply cold-start fix.')
    path = Path(importlib.metadata.distribution('nodriver').locate_file('nodriver/core/browser.py'))
    original = path.read_text()
    replacements = [
        ('await asyncio.sleep(0.25)\n        for _ in range(5):',
         'await asyncio.sleep(0.25)\n        for _ in range(60):'),
        ('if _ == 4:\n                    logger.debug("could not start", exc_info=True)',
         'if _ == 59:\n                    logger.debug("could not start", exc_info=True)'),
    ]
    modified = original
    for before, after in replacements:
        if modified.count(before) != 1:
            raise SystemExit('nodriver startup source changed; refusing an unchecked patch.')
        modified = modified.replace(before, after, 1)
    compile(modified, str(path), 'exec')
    path.write_text(modified)
    print('[SETUP] Browser provider ready; Chrome startup deadline is 30.25 seconds, sandbox enabled.')


if __name__ == '__main__':
    main()
