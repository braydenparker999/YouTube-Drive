import json
import unittest

from src.downloader import diagnostic_events


class DiagnosticTests(unittest.TestCase):
    def test_no_raw_debug_values_escape(self):
        raw = '\n'.join([
            '[debug] Proxy map: secret-proxy',
            '[debug] Command-line config: secret-config',
            '[debug] [youtube] [pot] PO Token Providers: bgutil:http-2.0.0',
            '[debug] JS runtimes: deno-2.9.6',
            '[debug] [youtube] test: mweb player response playability status: LOGIN_REQUIRED',
            '[debug] [youtube] [pot] Generated POT: secret-token',
            'ERROR: Sign in to confirm you’re not a bot. https://secret-url',
        ])
        events = diagnostic_events(raw)
        self.assertNotIn('secret-', json.dumps(events))
        self.assertIn({'event': 'player_response', 'client': 'mweb', 'status': 'LOGIN_REQUIRED'}, events)
        self.assertIn({'event': 'token_generated'}, events)
        self.assertIn({'event': 'bot_check'}, events)
        self.assertIn({'event': 'provider_registry', 'bgutil_http': True, 'wpc': False}, events)

    def test_failed_proof_writes_report_and_exits_nonzero(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from scripts.youtube_proof import main
        from src.common import ArchiveError

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'youtube-proof.json'
            with patch('scripts.youtube_proof.Path', return_value=target), \
                    patch('scripts.youtube_proof.Downloader', side_effect=ArchiveError('Controlled failure')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 1)
            self.assertEqual(json.loads(target.read_text())['status'], 'failed')
            self.assertIn('[PROOF]', output.getvalue())
