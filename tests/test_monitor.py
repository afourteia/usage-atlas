import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from atlas.profiles import discover
from atlas.providers import parse_codex, parse_claude, parse_claude_reset, parse_kimi, Unavailable
from atlas.store import Store
from server import Monitor, handler_for


class ProviderTests(unittest.TestCase):
    def test_codex_primary_is_not_assumed_to_be_five_hours(self):
        windows, extras = parse_codex({'rateLimits': {'primary': {'usedPercent': 37, 'windowDurationMins': 10080, 'resetsAt': 1800000000}}, 'rateLimitResetCredits': {'availableCount': 2}})
        self.assertEqual(windows[0]['label'], 'Weekly')
        self.assertEqual(windows[0]['usedPercent'], 37)
        self.assertEqual(extras[0]['value'], '2')

    def test_codex_model_windows_and_monthly_spend(self):
        windows, _ = parse_codex({'rateLimitsByLimitId': {'spark': {'limitName': 'Spark', 'primary': {'usedPercent': 0, 'windowDurationMins': 300}, 'individualLimit': {'remainingPercent': 75, 'resetsAt': 1800000000}}}})
        self.assertEqual([(w['label'], w['usedPercent']) for w in windows], [('5 hours', 0), ('Monthly spending limit', 25)])
        self.assertEqual(windows[0]['group'], 'Spark')

    def test_kimi_sparse_zero_used_and_unknown_values(self):
        windows = parse_kimi({'usage': {'used': '15', 'limit': '100'}, 'limits': [{'window': {'duration': 300, 'timeUnit': 'TIME_UNIT_MINUTE'}, 'detail': {'limit': '100', 'remaining': '100', 'resetTime': '2026-09-06T13:15:15Z'}}]})
        self.assertEqual([w['usedPercent'] for w in windows], [15, 0])
        self.assertEqual(windows[1]['label'], '5 hours')
        self.assertIsNotNone(windows[1]['resetsAt'])
        missing = parse_kimi({'usage': {'limit': '100'}})[0]
        self.assertIsNone(missing['usedPercent'])
        self.assertIsNone(parse_kimi({'usage': {'used': '0', 'limit': '0'}})[0]['usedPercent'])

    def test_claude_render_duplicates_and_scoped_limits(self):
        text = '''Current session
4% 4% used
Resets 2:09pm (Africa/Tripoli)
Current week (all models)
3% 3% used
Resets Sep 6, 10:59am (Africa/Tripoli)
Current week (Fable)
5% 5% used
Resets Sep 6, 10:59am (Africa/Tripoli)
What's contributing to your limits usage?
'''
        now = datetime(2026, 9, 6, 10, tzinfo=ZoneInfo('Africa/Tripoli')).timestamp()
        windows = parse_claude(text + text, now)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0]['usedPercent'], 4)
        self.assertEqual(windows[2]['group'], 'Fable')
        self.assertEqual(windows[0]['resetsAt'], datetime(2026, 9, 6, 14, 9, tzinfo=ZoneInfo('Africa/Tripoli')).timestamp())

    def test_reset_rollover_and_unparseable_preserved_as_unknown(self):
        now = datetime(2026, 12, 31, 23, tzinfo=ZoneInfo('UTC')).timestamp()
        self.assertEqual(parse_claude_reset('Resets 2am (UTC)', now), datetime(2027, 1, 1, 2, tzinfo=ZoneInfo('UTC')).timestamp())
        self.assertIsNone(parse_claude_reset('Resets whenever (Unknown/Zone)', now))

    def test_duplicate_codex_profiles_are_one_account(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            for folder, account in [('.codex', 'one'), ('.codex-personal', 'two'), ('.codex-shadow-personal', 'two')]:
                path = home / folder
                path.mkdir()
                (path / 'auth.json').write_text(json.dumps({'tokens': {'account_id': account}}))
            profiles = discover(home)
            self.assertEqual(len(profiles), 2)
            self.assertEqual(len(profiles[1]['profiles']), 2)
            self.assertNotIn('tokens', json.dumps(profiles))


class MonitorTests(unittest.TestCase):
    def test_failure_keeps_last_good_reading_and_restart_marks_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = {'id': 'test-account', 'provider': 'kimi', 'home': temp, 'label': 'Test', 'email': None, 'profiles': [temp]}
            with patch('server.discover', return_value=[profile]):
                monitor = Monitor(Path(temp)/'data', Path(temp)/'probe')
                with patch.dict('server.ADAPTERS', {'kimi': lambda *_: {'windows': [{'id': 'week', 'usedPercent': 25, 'resetsAt': time.time()+3600}], 'daily': []}}):
                    monitor.poll_one(profile)
                updated = monitor.accounts['test-account']['updatedAt']
                with patch.dict('server.ADAPTERS', {'kimi': lambda *_: (_ for _ in ()).throw(Unavailable('Unavailable'))}):
                    monitor.poll_one(profile)
                account = monitor.snapshot()['accounts'][0]
                self.assertEqual(account['windows'][0]['usedPercent'], 25)
                self.assertEqual(account['updatedAt'], updated)
                self.assertEqual(account['status'], 'stale')
                self.assertEqual(len(monitor.store.history('test-account', 'week')), 1)
                restored = Monitor(Path(temp)/'data', Path(temp)/'probe')
                self.assertEqual(restored.snapshot()['accounts'][0]['status'], 'stale')

    def test_http_does_not_serve_credentials_or_cross_origin_mutations(self):
        with tempfile.TemporaryDirectory() as temp, patch('server.discover', return_value=[]):
            monitor = Monitor(Path(temp)/'data', Path(temp)/'probe')
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(monitor))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urllib.request.urlopen(base+'/api/health') as response:
                    self.assertEqual(response.status, 200)
                for path in ['/../accounts.json', '/.data/usage.sqlite3', '/server.py']:
                    with self.assertRaises(urllib.error.HTTPError) as cm:
                        urllib.request.urlopen(base+path)
                    self.assertEqual(cm.exception.code, 404)
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(urllib.request.Request(base+'/api/refresh', method='POST'))
                self.assertEqual(cm.exception.code, 403)
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(urllib.request.Request(base+'/api/snapshot', headers={'Host': 'attacker.example'}))
                self.assertEqual(cm.exception.code, 403)
                req = urllib.request.Request(base+'/api/refresh', method='POST', headers={'X-Atlas-Request': '1'})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.status, 202)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == '__main__':
    unittest.main()
