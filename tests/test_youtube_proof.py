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
        self.assertIn({'event': 'provider_registry', 'bgutil_http': True}, events)
