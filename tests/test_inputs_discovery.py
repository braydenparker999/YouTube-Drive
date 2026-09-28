import json
import tempfile
import unittest
from pathlib import Path

from src.common import ArchiveError, SetupError, parse_inputs, safe_filename, video_id
from src.discover import load_config, parse_feed

VID = 'BaW_jenozKc'
CID = 'UC' + 'a' * 22


def feed_xml(vid=VID, cid=CID, count=1):
    entries = ''.join(f'<entry><yt:videoId>{vid}</yt:videoId><yt:channelId>{cid}</yt:channelId><title>Test &amp; Demo</title><published>2026-09-20T12:00:00Z</published></entry>' for _ in range(count))
    return f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><yt:channelId>{cid}</yt:channelId>{entries}</feed>'.encode()


class InputsTests(unittest.TestCase):
    def test_url_variants(self):
        for url in [f'https://youtube.com/watch?v={VID}&list=ignored', f'https://youtu.be/{VID}?t=10',
                    f'https://www.youtube.com/shorts/{VID}', f'https://m.youtube.com/live/{VID}', f'https://youtube.com/embed/{VID}']:
            self.assertEqual(video_id(url), VID)

    def test_reject_bad_or_injected_urls(self):
        for url in ['https://youtube.com.evil.test/watch?v=' + VID, 'https://youtube.com@evil.test/watch?v=' + VID,
                    'https://youtube.com:443/watch?v=' + VID, 'file:///etc/passwd', '--exec rm -rf /',
                    'https://youtube.com/playlist?list=hello', 'https://youtu.be/short', 'http://youtu.be/' + VID,
                    f'https://youtube.com/watch?v={VID}&v={VID}', f'https://youtu.be/{VID}/extra', None]:
            with self.subTest(url=url), self.assertRaises(SetupError):
                video_id(url)

    def test_input_json_newlines_deduplication(self):
        url = 'https://youtu.be/' + VID
        for raw in [url + '\n' + url, json.dumps([url, url])]:
            parsed = parse_inputs({'INPUT_URLS': raw, 'INPUT_REQUEST_ID': 'jarvis-1234'})
            self.assertEqual(parsed.ids, [VID])
            self.assertEqual((parsed.destination, parsed.max_quality), ('Requested', 1080))

    def test_input_rejections(self):
        for env in [{'INPUT_URLS': '[bad'}, {'INPUT_URLS': '[42]'}, {'INPUT_URLS': 'x' * 30001},
                    {'INPUT_DESTINATION': '$(touch /tmp/pwn)'}, {'INPUT_DESTINATION': '../root'},
                    {'INPUT_DESTINATION': 'a/b'}, {'INPUT_MAX_QUALITY': '1080; echo hacked'},
                    {'INPUT_REQUEST_ID': 'x\n::warning::evil'}, {'INPUT_DESTINATION': 'foo.'}]:
            with self.subTest(env=env), self.assertRaises(SetupError):
                parse_inputs(env)

    def test_filename_safe_and_bounded(self):
        name = safe_filename('../A<>:"/\\|?*\n\x00' + '🎸' * 100, VID, '2026-09-20T12:00:00Z', 'mp4')
        self.assertLess(len(name.encode()), 220)
        self.assertTrue(name.endswith(f'[{VID}].mp4'))
        self.assertFalse(any(c in name for c in '/\\\n\x00<>:"|?*'))
        self.assertTrue(safe_filename('', VID, None, 'mkv').startswith('unknown-date - Untitled'))

    def test_feed_parsing_deduplication(self):
        result = parse_feed(feed_xml(count=2), CID)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['title'], 'Test & Demo')
        self.assertEqual(result[0]['video_id'], VID)

    def test_invalid_feed_fails_closed(self):
        for content in [b'<html/>', feed_xml(cid='UC' + 'b' * 22), feed_xml(vid='bad'), b'<!DOCTYPE test><feed/>']:
            with self.assertRaises(ArchiveError):
                parse_feed(content, CID)

    def test_config_validates_enabled_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yml'
            path.write_text(f'channels:\n  - name: Creator\n    channel_id: {CID}\n')
            self.assertEqual(load_config(path)['settings']['max_quality'], 1080)
            path.write_text('channels:\n  - name: Creator\n    channel_id: @handle\n')
            with self.assertRaises(SetupError):
                load_config(path)

    def test_feed_window_latest_fifteen(self):
        body = feed_xml().decode()
        start, tail = body.split('<entry>', 1)
        entry = '<entry>' + tail.split('</feed>')[0]
        entries = ''.join(entry.replace(VID, f'video_{n:05d}').replace('2026-09-20', f'2026-09-{n+1:02d}') for n in range(20))
        result = parse_feed((start + entries + '</feed>').encode(), CID)
        self.assertEqual(len(result), 15)
        self.assertEqual(result[0]['video_id'], 'video_00019')
