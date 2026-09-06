import json
from pathlib import Path
import tempfile
import time
import unittest
from atlas.activity import TokenIndex, parse_file, source_roots, total, timestamp


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.at = int(time.time() // 3600) * 3600 - 7200 + 10

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return path

    def codex(self, response='response1', tokens=100, at=None):
        return {'type': 'token_usage_record', 'timestamp': at or self.at,
                'payload': {'response_id': response, 'usage': {'total_tokens': tokens}}}

    def profile(self, name='account1', provider='codex'):
        home = self.root / name
        return {'id': name, 'provider': provider, 'label': name, 'home': str(home), 'profiles': [str(home)]}

    def test_codex_prefers_request_records_and_deduplicates(self):
        status = {'type': 'event_msg', 'timestamp': self.at, 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 100}}}}
        path = self.write('session.jsonl', [status, self.codex(), self.codex()])
        data = parse_file(path, 'codex', self.at - 100)
        self.assertEqual(len(data['events']), 1)
        self.assertEqual(data['events'][0][2], 100)

    def test_legacy_cumulative_deltas_resets_and_repeats(self):
        rows = [{'type': 'event_msg', 'timestamp': self.at + i, 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': n}}}} for i, n in enumerate([100, 100, 150, 20])]
        result = parse_file(self.write('legacy.jsonl', rows), 'codex', self.at - 100)
        self.assertEqual(sum(row[2] for row in result['events']), 170)

    def test_copied_legacy_sessions_count_once(self):
        profile = self.profile()
        rows = [{'type': 'event_msg', 'timestamp': self.at, 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 100}}}}]
        self.write('account1/sessions/original.jsonl', rows)
        self.write('account1/sessions/fork.jsonl', rows)
        result = TokenIndex(self.root/'index.sqlite3').scan([profile])
        self.assertEqual(sum(row['tokens'] for row in result['hours']), 100)

    def test_claude_content_blocks_do_not_multiply_tokens(self):
        def row(output, at):
            return {'type': 'assistant', 'timestamp': at, 'requestId': 'req1', 'message': {'id': 'msg1', 'content': 'private conversation', 'usage': {'input_tokens': 10, 'output_tokens': output, 'cache_read_input_tokens': 30, 'cache_creation_input_tokens': 40}}}
        result = parse_file(self.write('claude.jsonl', [row(20, self.at), row(25, self.at + 3600), row(20, self.at)]), 'claude', self.at-1)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0][1:], [self.at, 105])
        self.assertNotIn('private conversation', json.dumps(result))

    def test_kimi_counts_requests_not_status_totals(self):
        rows = [{'type': 'usage.record', 'time': self.at*1000, 'model': 'kimi', 'usageScope': 'turn', 'usage': {'inputOther': 10, 'output': 20, 'inputCacheRead': 30, 'inputCacheCreation': 40}},
                {'type': 'agent.status.updated', 'time': self.at*1000, 'usage': {'total': {'inputOther': 10000}}}]
        result = parse_file(self.write('sessions/test/agents/main/wire.jsonl', rows), 'kimi', self.at-1)
        self.assertEqual(result['events'][0][1:], [self.at, 100])

    def test_future_missing_negative_and_partial_records(self):
        path = self.write('bad.jsonl', [self.codex(tokens=-1), self.codex(response='future', at=time.time()+86400)])
        with path.open('a') as f:
            f.write(json.dumps(self.codex()))  # Incomplete final line must be retried.
        self.assertEqual(parse_file(path, 'codex', self.at-1)['events'], [])
        self.assertIsNone(total({}, 'claude'))
        self.assertIsNone(timestamp('2026-01-01T00:00:00'))
        self.assertEqual(total({'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 20, 'reasoning_output_tokens': 15}, 'codex'), 120)

    def test_symlink_shared_with_other_account_belongs_to_owner(self):
        first, second = self.profile(), self.profile('account2')
        source = self.root/'account1/sessions'
        source.mkdir(parents=True)
        other = self.root/'account2/sessions'
        other.parent.mkdir()
        other.symlink_to(source)
        roots = source_roots([second, first])
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0][0]['id'], first['id'])

    def test_persisted_index_append_rewrite_delete_and_global_dedup(self):
        one, two = self.profile(), self.profile('account2')
        p = self.write('account1/sessions/log.jsonl', [self.codex()])
        copy = self.write('account1/archived_sessions/log.jsonl', [self.codex()])
        self.write('account2/sessions/log.jsonl', [self.codex('other', 200)])
        index = TokenIndex(self.root/'index.sqlite3')
        result = index.scan([one, two])
        self.assertEqual(result['hours'][0]['tokens'], 300)
        with p.open('a') as f:
            f.write(json.dumps(self.codex('new', 50, self.at+3600))+'\n')
        # Use a past timestamp, since the current hour's future is intentionally excluded.
        p.write_text(json.dumps(self.codex('new', 50, self.at-3600))+'\n')
        copy.unlink()
        result = index.scan([one, two])
        self.assertEqual(sum(row['tokens'] for row in result['hours']), 250)
        restored = TokenIndex(self.root/'index.sqlite3').snapshot([one, two])
        self.assertEqual(restored['hours'], result['hours'])
        self.assertEqual(sum(row['tokens'] for a in restored['accounts'] for row in a['hours']), 250)
        self.assertNotIn('payload', json.dumps(restored))


if __name__ == '__main__':
    unittest.main()
