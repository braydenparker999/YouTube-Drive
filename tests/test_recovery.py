import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.common import ArchiveError, Inputs, StateError
from src.main import discover_queue, prepare_manual, process_video, run_queue
from src.state import State

VID = 'BaW_jenozKc'
OTHER = 'abcdefghijk'
CID = 'UC' + 'a' * 22
SETTINGS = {'max_videos_per_run': 25, 'run_budget_minutes': 270, 'max_quality': 1080}


class FakeDownloader:
    def __init__(self):
        self.downloads = 0
        self.metadata_calls = 0
        self.fail = set()

    def metadata(self, vid, quality):
        self.metadata_calls += 1
        if vid in self.fail:
            raise ArchiveError('Video unavailable.')
        return {'id': vid, 'title': 'Test', 'channel_id': CID, 'upload_date': '20260920'}

    def public_fields(self, info):
        return {'title': info['title'], 'channel_id': CID, 'published_at': '2026-09-20'}

    def download(self, vid, quality, info, directory):
        self.downloads += 1
        path = Path(directory) / 'media.mp4'
        path.write_bytes(b'test media bytes')
        return path


class FakeDrive:
    root = 'root_folder_id'

    def __init__(self):
        self.files = {}
        self.uploads = 0
        self.upload_failure = False
        self.reserved = 0

    def new_id(self):
        self.reserved += 1
        return f'drive_file_{self.reserved:04}'

    def folder(self, *args):
        return 'creator_folder_id'

    def verified(self, item):
        return self.files.get(item.get('drive_file_id'))

    def upload(self, path, item, *args):
        self.uploads += 1
        if self.upload_failure:
            raise ArchiveError('Drive upload unavailable.')
        self.files[item['drive_file_id']] = {'id': item['drive_file_id'], 'md5': item['md5']}
        return item['drive_file_id']


def new_item(vid=VID):
    return {'video_id': vid, 'source': 'manual', 'destination': 'Requested', 'max_quality': 1080}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state, self.drive, self.down = State(), FakeDrive(), FakeDownloader()

    def test_complete_duplicate_skips_all_network_downloading(self):
        item = new_item()
        first = process_video(self.state, self.drive, self.down, item, self.tmp.name)
        second = process_video(self.state, self.drive, self.down, item, self.tmp.name)
        self.assertTrue(second['skipped'])
        self.assertEqual(first['drive_file_id'], second['drive_file_id'])
        self.assertEqual((self.down.downloads, self.down.metadata_calls, self.drive.uploads), (1, 1, 1))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_upload_success_state_commit_lost_recovers_without_download(self):
        durable = {}
        def save(data):
            if data['videos'].get(VID, {}).get('status') == 'complete':
                raise StateError('Simulated checkpoint outage.')
            durable.clear()
            durable.update(copy.deepcopy(data))
        self.state.saver = save
        with self.assertRaises(StateError):
            process_video(self.state, self.drive, self.down, new_item(), self.tmp.name)
        self.assertEqual(durable['videos'][VID]['status'], 'uploading')
        resumed = State(durable)
        result = process_video(resumed, self.drive, self.down, resumed.data['videos'][VID], self.tmp.name)
        self.assertTrue(result['recovered'])
        self.assertTrue(resumed.complete(VID))
        self.assertEqual((self.down.downloads, self.drive.uploads), (1, 1))

    def test_id_checkpoint_failure_prevents_upload(self):
        def save(data):
            if data['videos'].get(VID, {}).get('drive_file_id'):
                raise StateError('State unavailable.')
        self.state.saver = save
        with self.assertRaises(StateError):
            process_video(self.state, self.drive, self.down, new_item(), self.tmp.name)
        self.assertEqual(self.drive.uploads, 0)

    @patch('src.main.time.sleep')
    def test_upload_failure_not_complete_and_retry_reuses_id(self, sleep):
        summary = {'videos': []}
        self.drive.upload_failure = True
        run_queue(self.state, self.drive, self.down, [new_item()], SETTINGS, summary, self.tmp.name)
        record = self.state.data['videos'][VID]
        reserved = record['drive_file_id']
        self.assertEqual(record['status'], 'failed')
        self.assertIsNone(record['completed_at'])
        self.drive.upload_failure = False
        process_video(self.state, self.drive, self.down, record, self.tmp.name)
        self.assertEqual(record['drive_file_id'], reserved)
        self.assertEqual(self.drive.reserved, 1)
        self.assertTrue(self.state.complete(VID))

    @patch('src.main.time.sleep')
    def test_one_video_failure_does_not_abort_next(self, sleep):
        self.down.fail.add(VID)
        summary = {'videos': []}
        run_queue(self.state, self.drive, self.down, [new_item(), new_item(OTHER)], SETTINGS, summary, self.tmp.name)
        self.assertEqual([v['status'] for v in summary['videos']], ['failed', 'complete'])

    def test_failed_creator_does_not_abort_next_and_old_queue_survives(self):
        bad = 'UC' + 'b' * 22
        config = {'settings': SETTINGS, 'channels': [{'name': 'Broken', 'channel_id': bad}, {'name': 'Good', 'channel_id': CID}]}
        self.state.add({**new_item(OTHER), 'source': 'channel', 'channel_id': CID})
        def feed(cid):
            if cid == bad:
                raise ArchiveError('Feed unavailable.')
            return [{'video_id': VID, 'channel_id': CID, 'title': 'New', 'published_at': '2026-09-20'}]
        queue, errors = discover_queue(self.state, config, Inputs([], 'Requested', 1080, ''), feed)
        self.assertEqual({v['video_id'] for v in queue}, {VID, OTHER})
        self.assertEqual(len(errors), 1)

    def test_budget_defers_without_losing_queue(self):
        for vid in [VID, OTHER]:
            self.state.add(new_item(vid))
        summary = {'videos': []}
        settings = {**SETTINGS, 'max_videos_per_run': 0}
        run_queue(self.state, self.drive, self.down, list(self.state.data['videos'].values()), settings, summary, self.tmp.name)
        self.assertEqual(len(summary['videos']), 2)
        self.assertTrue(all(v['status'] == 'deferred' for v in summary['videos']))
        self.assertEqual(len(self.state.data['videos']), 2)

    def test_state_records_checksum_only_after_download(self):
        process_video(self.state, self.drive, self.down, new_item(), self.tmp.name)
        record = self.state.data['videos'][VID]
        self.assertEqual(record['md5'], hashlib.md5(b'test media bytes').hexdigest())
        self.assertTrue(record['completed_at'])


class ManualQueueTests(unittest.TestCase):
    @patch('src.main.time.sleep')
    def test_batch_is_persistent_before_downloading(self, sleep):
        state, down = State(), FakeDownloader()
        summary = {'videos': []}
        ready = prepare_manual(state, down, [new_item(), new_item(OTHER)], summary)
        self.assertEqual(len(ready), 2)
        self.assertEqual(down.downloads, 0)
        self.assertEqual(set(state.data['videos']), {VID, OTHER})

    @patch('src.main.time.sleep')
    def test_unverified_failure_is_not_persisted_and_next_request_survives(self, sleep):
        state, down = State(), FakeDownloader()
        down.fail.add(VID)
        summary = {'videos': []}
        ready = prepare_manual(state, down, [new_item(), new_item(OTHER)], summary)
        self.assertEqual([v['video_id'] for v in ready], [OTHER])
        self.assertEqual(summary['videos'][0]['status'], 'failed')
        self.assertNotIn(VID, state.data['videos'])


class RetryBudgetTests(unittest.TestCase):
    @patch('src.main.time.sleep')
    def test_metadata_failure_increments_attempt_count(self, sleep):
        state, down = State(), FakeDownloader()
        state.add(new_item())
        down.fail.add(VID)
        with tempfile.TemporaryDirectory() as tmp:
            run_queue(state, FakeDrive(), down, [state.data['videos'][VID]], SETTINGS, {'videos': []}, tmp)
        self.assertEqual(state.data['videos'][VID]['attempts'], 1)

    def test_exhausted_record_pauses_daily_but_manual_can_retry(self):
        state = State()
        state.add(new_item())
        state.update(VID, status='failed', attempts=5)
        config = {'settings': {**SETTINGS, 'max_attempts': 5}, 'channels': []}
        daily, _ = discover_queue(state, config, Inputs([], 'Requested', 1080, ''))
        manual, _ = discover_queue(state, config, Inputs([VID], 'Requested', 1080, ''))
        self.assertEqual(daily, [])
        self.assertEqual([v['video_id'] for v in manual], [VID])
