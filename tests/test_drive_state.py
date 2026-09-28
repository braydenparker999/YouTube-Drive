import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.common import ArchiveError, HttpError, StateError
from src.drive import Drive
from src.state import GitHubState, State, empty_state, save_local, validate

VID = 'BaW_jenozKc'
FID = 'drive_file_0001'
SESSION = 'https://www.googleapis.com/upload/drive/v3/files?upload_id=test-session'


def drive():
    return Drive({'GDRIVE_CLIENT_ID': 'unit-test', 'GDRIVE_CLIENT_SECRET': 'unit-test',
                  'GDRIVE_REFRESH_TOKEN': 'unit-test', 'GDRIVE_ROOT_FOLDER_ID': 'root_folder_id'}, State())


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'video.mp4'
        self.path.write_bytes(b'0123456789')
        self.item = {'video_id': VID, 'drive_file_id': FID, 'size': 10,
                     'md5': hashlib.md5(b'0123456789').hexdigest()}
        self.drive = drive()

    def test_verification_requires_matching_id_size_checksum(self):
        obj = {'id': FID, 'size': '10', 'md5Checksum': self.item['md5'],
               'appProperties': {'youtube_video_id': VID}, 'trashed': False}
        self.drive.get = Mock(return_value=obj)
        self.assertEqual(self.drive.verified(self.item), obj)
        for change in [{'size': '9'}, {'md5Checksum': 'bad'}, {'trashed': True}, {'appProperties': {}}]:
            self.drive.get.return_value = {**obj, **change}
            with self.assertRaises(ArchiveError):
                self.drive.verified(self.item)

    @patch('src.drive.backoff')
    def test_chunk_timeout_probes_offset_without_redownload(self, sleep):
        self.drive.verified = Mock(side_effect=[None, {'id': FID}])
        self.drive.raw = Mock(side_effect=[(200, {'location': SESSION}, b''), HttpError(0),
                                          (308, {'range': 'bytes=0-4'}, b''), (200, {}, b'{}')])
        self.assertEqual(self.drive.upload(self.path, self.item, 'folder_id', 'video.mp4'), FID)
        calls = self.drive.raw.call_args_list
        self.assertEqual(calls[2].args[2]['Content-Range'], 'bytes */10')
        self.assertEqual(calls[3].args[2]['Content-Range'], 'bytes 5-9/10')
        self.assertEqual(calls[3].args[3], b'56789')

    def test_expired_session_restarts_with_same_reserved_id(self):
        self.drive.verified = Mock(side_effect=[None, None, {'id': FID}])
        self.drive.raw = Mock(side_effect=[(200, {'location': SESSION}, b''), (404, {}, b''),
                                          (200, {'location': SESSION}, b''), (200, {}, b'{}')])
        self.drive.upload(self.path, self.item, 'folder_id', 'video.mp4')
        posts = [c for c in self.drive.raw.call_args_list if c.args[0] == 'POST']
        self.assertEqual([json.loads(c.args[3])['id'] for c in posts], [FID, FID])

    def test_lost_final_response_conflict_verifies_existing(self):
        self.drive.verified = Mock(side_effect=[None, {'id': FID}])
        self.drive.raw = Mock(return_value=(409, {}, b''))
        self.assertEqual(self.drive.upload(self.path, self.item, 'folder_id', 'video.mp4'), FID)
        self.assertEqual(self.drive.raw.call_count, 1)

    def test_unsafe_upload_session_rejected(self):
        for url in ['https://evil.test/upload/drive/x', 'http://www.googleapis.com/upload/drive/x',
                    'https://www.googleapis.com@evil.test/upload/drive/x', 'https://www.googleapis.com/token']:
            with self.assertRaises(ArchiveError):
                self.drive.valid_session(url)

    def test_folder_id_saved_before_creation(self):
        snapshots = []
        self.drive.state.saver = lambda data: snapshots.append(data)
        self.drive.new_id = Mock(return_value='folder_reserved_id')
        self.drive.get = Mock(side_effect=[None, {'id': 'folder_reserved_id', 'mimeType': 'application/vnd.google-apps.folder', 'parents': ['root_folder_id']}])
        def api(method, path, data=None):
            if method == 'GET':
                return {'files': []}
            self.assertEqual(list(snapshots[-1]['folders'].values()), ['folder_reserved_id'])
            return {'id': 'folder_reserved_id'}
        self.drive.api = Mock(side_effect=api)
        self.assertEqual(self.drive.folder('root_folder_id', 'Creator'), 'folder_reserved_id')

    def test_failed_folder_checkpoint_stops_creation(self):
        self.drive.state.saver = Mock(side_effect=StateError('Offline'))
        self.drive.new_id = Mock(return_value='folder_reserved_id')
        self.drive.api = Mock(return_value={'files': []})
        with self.assertRaises(StateError):
            self.drive.folder('root_folder_id', 'Creator')
        self.assertTrue(all(c.args[0] == 'GET' for c in self.drive.api.call_args_list))

    @patch('src.drive.raw_http')
    def test_expired_access_token_refreshes_unattended(self, http):
        http.side_effect = [(200, {}, b'{"access_token":"unit-test-access","expires_in":3600}'), (200, {}, b'{}')]
        self.drive.raw('GET', 'https://www.googleapis.com/drive/v3/files')
        self.assertIn(b'grant_type=refresh_token', http.call_args_list[0].kwargs['body'])
        self.assertEqual(http.call_args_list[1].kwargs['headers']['Authorization'], 'Bearer unit-test-access')

    @patch('src.drive.raw_http')
    def test_oauth_failure_does_not_leak_response(self, http):
        http.return_value = (400, {}, b'{"error":"invalid_grant","error_description":"secret-looking-test-canary"}')
        with self.assertRaises(ArchiveError) as ctx:
            self.drive.refresh()
        self.assertIn('invalid_grant', str(ctx.exception))
        self.assertNotIn('canary', str(ctx.exception))


def remote(data, sha='state-sha'):
    return {'sha': sha, 'content': base64.b64encode(json.dumps(data).encode()).decode()}


class StateTests(unittest.TestCase):
    def test_invalid_state_not_silently_reset(self):
        for data in [{}, {'version': 2, 'videos': {}, 'folders': {}},
                     {'version': 1, 'videos': {VID: {'video_id': VID, 'status': 'complete'}}, 'folders': {}}]:
            with self.assertRaises(StateError):
                validate(data)

    def test_atomic_local_state_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'archive.json'
            save_local(path, empty_state())
            self.assertEqual(json.loads(path.read_text()), empty_state())
            self.assertFalse(path.with_suffix('.tmp').exists())

    @patch('src.state.raw_http')
    def test_github_lost_response_reconciles_committed_state(self, http):
        initial, updated = empty_state(), empty_state()
        updated['root_fingerprint'] = 'test'
        http.side_effect = [(200, {}, json.dumps(remote(initial)).encode()), HttpError(0),
                            (200, {}, json.dumps(remote(updated, 'new-sha')).encode())]
        state = GitHubState('owner/repo', 'main', 'unit-test')
        state.data = updated
        state.save()
        self.assertEqual(state.sha, 'new-sha')
        self.assertEqual(http.call_count, 3)

    @patch('src.state.raw_http')
    def test_github_conflicting_writer_stops(self, http):
        other = copy.deepcopy(empty_state())
        other['root_fingerprint'] = 'other-writer'
        http.side_effect = [(200, {}, json.dumps(remote(empty_state())).encode()), (409, {}, b'{}'),
                            (200, {}, json.dumps(remote(other, 'different-sha')).encode())]
        state = GitHubState('owner/repo', 'main', 'unit-test')
        state.data['root_fingerprint'] = 'ours'
        with self.assertRaisesRegex(StateError, 'Concurrent'):
            state.save()


class LargeStateTests(unittest.TestCase):
    @patch('src.state.raw_http')
    def test_large_state_reads_immutable_blob(self, http):
        obj = {'sha': 'large-state-sha', 'encoding': 'none', 'content': ''}
        http.side_effect = [(200, {}, json.dumps(obj).encode()), (200, {}, json.dumps(empty_state()).encode())]
        state = GitHubState('owner/repo', 'main', 'unit-test')
        self.assertEqual(state.data, empty_state())
        self.assertTrue(http.call_args_list[1].args[1].endswith('/git/blobs/large-state-sha'))
