import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.common import ArchiveError, SetupError, log
from src.downloader import Downloader, child_env
from src.main import check_setup, main

ROOT = Path(__file__).resolve().parents[1]


class SecurityTests(unittest.TestCase):
    def test_secrets_absent_from_downloader_environment(self):
        with patch.dict(os.environ, {'GDRIVE_REFRESH_TOKEN': 'unit-test', 'GITHUB_TOKEN': 'unit-test', 'YTDLP_PROXY': 'unit-test'}):
            env = child_env()
            self.assertNotIn('GDRIVE_REFRESH_TOKEN', env)
            self.assertNotIn('GITHUB_TOKEN', env)
            self.assertNotIn('YTDLP_PROXY', env)

    def test_untrusted_titles_cannot_inject_actions_commands(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            log('DOWNLOAD', title='hello\n::add-mask::fake\n::error::evil')
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_public_only_and_live_deferred(self):
        downloader = object.__new__(Downloader)
        downloader.settings = {'max_duration_seconds': 7200, 'max_file_gib': 4}
        downloader.env = {}
        for change in [{'availability': 'unlisted'}, {'availability': 'private'}, {'is_live': True}, {'live_status': 'is_upcoming'}, {'duration': 999999}]:
            obj = {'id': 'BaW_jenozKc', 'availability': 'public', 'duration': 10, **change}
            downloader.run = lambda *a, data=obj, **kw: json.dumps(data)
            with self.assertRaises(ArchiveError):
                downloader.metadata('BaW_jenozKc', 1080)

    def test_setup_error_is_clear_no_traceback(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            original = Path.cwd()
            try:
                os.chdir(tmp)
                Path('config').mkdir()
                Path('config/channels.yml').write_text('channels: []\n')
                with contextlib.redirect_stdout(out):
                    code = main(['--check-setup'], {})
                summary = json.loads(Path('run-summary.json').read_text())
            finally:
                os.chdir(original)
        self.assertEqual(code, 2)
        self.assertEqual(summary['status'], 'setup_required')
        self.assertIn('Missing repository secrets:', out.getvalue())
        self.assertNotIn('Traceback', out.getvalue())

    def test_setup_rejects_pr_or_nondefault_branch(self):
        env = {'GDRIVE_CLIENT_ID': 'unit-test', 'GDRIVE_CLIENT_SECRET': 'unit-test', 'GDRIVE_REFRESH_TOKEN': 'unit-test',
               'GDRIVE_ROOT_FOLDER_ID': 'root_folder_id', 'GITHUB_TOKEN': 'unit-test', 'GITHUB_REPOSITORY': 'owner/repo',
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch'}
        check_setup(env)
        for changes in [{'GITHUB_EVENT_NAME': 'pull_request'}, {'GITHUB_REF': 'refs/heads/untrusted'}]:
            with self.assertRaises(SetupError):
                check_setup({**env, **changes})

    def test_workflows_are_valid_data_and_safe(self):
        archive = yaml.load((ROOT / '.github/workflows/youtube-archive.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(archive['on']), {'schedule', 'workflow_dispatch'})
        self.assertEqual(archive['on']['schedule'][0]['cron'], '23 8 * * *')
        self.assertEqual(set(archive['on']['workflow_dispatch']['inputs']), {'urls', 'destination', 'max_quality', 'request_id'})
        self.assertEqual(archive['concurrency']['group'], 'youtube-archive')
        self.assertEqual(archive['concurrency']['cancel-in-progress'], 'false')
        self.assertEqual(archive['concurrency']['queue'], 'max')
        self.assertIn('default_branch', archive['jobs']['archive']['if'])
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            self.assertNotIn('pull_request_target', workflow['on'])
            for job in workflow['jobs'].values():
                for step in job['steps']:
                    self.assertNotIn('${{', step.get('run', ''))
                    if 'uses' in step:
                        self.assertRegex(step['uses'], r'@[0-9a-f]{40}$')
        tests = (ROOT / '.github/workflows/tests.yml').read_text()
        self.assertNotIn('secrets.', tests)
        self.assertNotIn('contents: write', tests)
