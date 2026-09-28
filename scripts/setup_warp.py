"""Anonymous WARP local proxy on a disposable GitHub runner.

No paid plan, user account, organization enrollment, or exported device keys.
Only explicitly proxied traffic uses WARP. Do not run on a personal workstation.
"""
import json
import os
import subprocess
import time


def command(args, timeout=30):
    return subprocess.run(args, check=True, timeout=timeout, capture_output=True, text=True).stdout


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('WARP setup is restricted to disposable GitHub Actions runners.')
    try:
        print('[EGRESS] ' + command(['warp-cli', '--version']).strip(), flush=True)
        command(['warp-cli', '--accept-tos', 'registration', 'new'])
        command(['warp-cli', '--accept-tos', 'mode', 'proxy'])
        command(['warp-cli', '--accept-tos', 'proxy', 'port', '40000'])
        command(['warp-cli', '--accept-tos', 'connect'])
        for _ in range(12):
            try:
                trace = command(['curl', '--fail', '--silent', '--show-error', '--max-time', '5',
                                 '--proxy', 'http://127.0.0.1:40000',
                                 'https://www.cloudflare.com/cdn-cgi/trace'], timeout=10)
                if 'warp=on' in trace.splitlines():
                    print('[EGRESS] ' + json.dumps({'warp': True, 'mode': 'local_proxy'}), flush=True)
                    return
            except (subprocess.SubprocessError, OSError):
                pass
            time.sleep(2)
    except (subprocess.SubprocessError, OSError):
        raise SystemExit('WARP local proxy setup failed; no YouTube request will be made.') from None
    raise SystemExit('WARP did not confirm an active tunnel within the bounded startup period.')


if __name__ == '__main__':
    main()
