"""Offline browser preflight: no YouTube requests, account, or cloud credentials."""
import asyncio
import json
import shutil
import subprocess

import nodriver
from nodriver.core.config import Config


async def main():
    config = Config(headless=False, browser_executable_path=shutil.which('google-chrome'))
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
        await asyncio.sleep(8)
        for instance in nodriver.core.util.get_registered_instances():
            process = instance._process
            if process and process.returncode is not None:
                stderr = await process.stderr.read()
                print(json.dumps({'browser_exit': process.returncode, 'startup_error': stderr.decode(errors='replace')[:5000]}), flush=True)
            elif process:
                try:
                    version = await instance._http.get('version')
                    print(json.dumps({'browser_ready_after_grace_period': bool(version)}), flush=True)
                except Exception:
                    print(json.dumps({'browser_ready_after_grace_period': False}), flush=True)
                instance.stop()
        raise SystemExit(1) from None
    finally:
        if browser:
            browser.stop()


if __name__ == '__main__':
    asyncio.run(main())
