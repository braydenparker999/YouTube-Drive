"""Offline browser preflight: no YouTube requests, account, or cloud credentials."""
import asyncio
import json
import shutil
import subprocess

import nodriver
from nodriver.core.config import Config


async def main():
    config = Config(headless=False)
    print(json.dumps({'browser': config.browser_executable_path, 'chrome': shutil.which('google-chrome')}), flush=True)
    # Blank-page stderr can diagnose runtime/sandbox failures without visiting a service.
    result = subprocess.run([config.browser_executable_path, '--headless', '--dump-dom', 'about:blank'],
                            capture_output=True, text=True, timeout=25)
    print(json.dumps({'blank_page_exit': result.returncode, 'startup_error': result.stderr[:5000]}), flush=True)
    browser = None
    try:
        browser = await nodriver.start(config=config)
        print(json.dumps({'browser_start': True}), flush=True)
    except Exception as exc:
        print(json.dumps({'browser_start': False, 'error': str(exc)[:2000]}), flush=True)
        raise SystemExit(1) from None
    finally:
        if browser:
            browser.stop()


if __name__ == '__main__':
    asyncio.run(main())
