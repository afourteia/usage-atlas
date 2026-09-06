"""Hourly token counts from official CLI logs. Never persist conversation content."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

RETENTION = 90 * 86400
VERSION = 2


def timestamp(value):
    try:
        if isinstance(value, (int, float)):
            result = value / 1000 if value > 10**11 else value
        else:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                return None
            result = parsed.timestamp()
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if math.isfinite(value) and value >= 0 else None


def total(usage, provider):
    if not isinstance(usage, dict):
        return None
    if provider == 'codex':
        explicit = count(usage.get('total_tokens'))
        if explicit is not None:
            return explicit
        keys = ('input_tokens', 'output_tokens')
    elif provider == 'claude':
        keys = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
    else:
        keys = ('inputOther', 'output', 'inputCacheRead', 'inputCacheCreation')
    if not any(k in usage for k in keys):
        return None
    values = [count(usage.get(k, 0)) for k in keys]
    return sum(values) if all(v is not None for v in values) else None


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def parse_file(path, provider, cutoff):
    """Prefer native request records over cumulative status snapshots."""
    native, fallback = {}, {}
    session = path.stem
    if provider == 'kimi':
        # Modern layout: <session>/agents/<agent>/wire.jsonl.
        parts = path.parts
        session = parts[parts.index('sessions') + 1] if 'sessions' in parts else str(path.parent)
    previous = 0
    native_seen = False
    supported = False
    malformed = 0

    def add(target, key, at, tokens):
        if at is None or tokens is None or at < cutoff or at > time.time() + 300:
            return
        key = digest(key)
        old = target.get(key)
        # Claude repeats a response for individual content blocks. Keep the largest
        # cumulative usage and the earliest timestamp instead of summing redraws.
        target[key] = [min(at, old[0]) if old else at, max(tokens, old[1]) if old else tokens]

    with path.open('rb') as stream:
        for line in stream:
            if not line.endswith(b'\n'):
                break  # A writer is still appending this record; reread next scan.
            if provider == 'codex':
                relevant = b'"token_usage_record"' in line or b'"token_count"' in line
            elif provider == 'claude':
                relevant = b'"usage"' in line and b'"assistant"' in line
            else:
                relevant = b'"usage.record"' in line or b'"StatusUpdate"' in line
            if not relevant or len(line) > 2_000_000:
                continue
            try:
                row = json.loads(line)
                kind = row.get('type')
                payload = row.get('payload') or {}
                at = timestamp(row.get('timestamp', row.get('time')))
                if provider == 'codex' and kind == 'token_usage_record':
                    supported = native_seen = True
                    key = payload.get('response_id')
                    if not key:
                        key = json.dumps([payload.get('thread_id', session), at, payload.get('usage')], sort_keys=True)
                    add(native, 'request:' + key, at, total(payload.get('usage'), provider))
                elif provider == 'codex' and kind == 'event_msg' and payload.get('type') == 'token_count':
                    info = payload.get('info') or {}
                    cumulative = total(info.get('total_token_usage'), provider)
                    if cumulative is None:
                        continue
                    supported = True
                    delta = cumulative - previous if cumulative >= previous else cumulative
                    previous = cumulative
                    if delta:
                        # Forked logs retain timestamps and usage snapshots, even when
                        # the destination session has a different filename.
                        key = json.dumps([at, info.get('total_token_usage'), info.get('last_token_usage')], sort_keys=True)
                        add(fallback, 'legacy:' + key, at, delta)
                elif provider == 'claude' and kind == 'assistant':
                    message = row.get('message') or {}
                    if message.get('usage') is None:
                        continue
                    supported = True
                    identity = message.get('id')
                    if not identity:
                        continue  # No stable response identity; don't invent one.
                    add(native, f"{identity}:{row.get('requestId', '')}", at, total(message.get('usage'), provider))
                elif provider == 'kimi' and kind == 'usage.record':
                    supported = native_seen = True
                    # UsageRecord is emitted once per LLM response, for both turn
                    # and session requests. Status totals must not be added again.
                    key = json.dumps([session, row.get('agentId'), row.get('time'), row.get('model'), row.get('usage')], sort_keys=True)
                    add(native, key, at, total(row.get('usage'), provider))
                elif provider == 'kimi':
                    message = row.get('message') or row
                    if message.get('type') != 'StatusUpdate':
                        continue
                    data = message.get('payload') or message
                    usage = data.get('token_usage') or {}
                    mapped = {out: usage.get(source, 0) for out, source in (
                        ('inputOther', 'input_other'), ('output', 'output'),
                        ('inputCacheRead', 'input_cache_read'), ('inputCacheCreation', 'input_cache_creation'))}
                    if not usage or not data.get('message_id'):
                        continue
                    supported = True
                    add(fallback, f"{session}:{data['message_id']}", at, total(mapped, provider))
            except (ValueError, TypeError, AttributeError, KeyError):
                malformed += 1
    records = native if provider == 'claude' or native_seen else fallback
    return {'events': [[key, *value] for key, value in records.items()],
            'supported': supported, 'malformed': malformed}


def source_roots(profiles):
    """A symlink to another account's log directory is not its own activity."""
    candidates = []
    for profile in profiles:
        names = ('projects',) if profile['provider'] == 'claude' else ('sessions', 'archived_sessions') if profile['provider'] == 'codex' else ('sessions',)
        for index, home in enumerate(profile.get('profiles', [profile['home']])):
            for name in names:
                path = Path(home) / name
                if path.is_dir():
                    resolved = path.resolve()
                    candidates.append((path != resolved, index, str(resolved), profile, resolved))
    seen = set()
    result = []
    for _, _, key, profile, path in sorted(candidates, key=lambda row: row[:3]):
        if key not in seen:
            result.append((profile, path))
            seen.add(key)
    return result


class TokenIndex:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS token_files (
                    path TEXT PRIMARY KEY, account TEXT, provider TEXT,
                    signature TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS token_meta (key TEXT PRIMARY KEY, value TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def scan(self, profiles):
        cutoff = time.time() - RETENTION
        seen = set()
        errors = defaultdict(int)
        with self.connect() as db:
            cached = {path: signature for path, signature in db.execute('SELECT path,signature FROM token_files')}
        for profile, root in source_roots(profiles):
            try:
                files = sorted(root.rglob('*.jsonl'))
            except OSError:
                errors[profile['id']] += 1
                continue
            for path in files:
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                try:
                    stat = path.stat()
                    if stat.st_mtime < cutoff:
                        seen.discard(key)
                        continue
                    signature = f'{VERSION}:{profile["id"]}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}'
                    if cached.get(key) == signature:
                        continue
                    data = parse_file(path, profile['provider'], cutoff)
                    with self.connect() as db:
                        db.execute('INSERT OR REPLACE INTO token_files VALUES (?,?,?,?,?)',
                            (key, profile['id'], profile['provider'], signature, json.dumps(data)))
                except OSError:
                    errors[profile['id']] += 1
        with self.connect() as db:
            # Removed files no longer contribute. Recent records in unchanged files
            # are filtered at aggregation time, so retention doesn't require rescans.
            db.executemany('DELETE FROM token_files WHERE path=?', [(p,) for p in cached if p not in seen])
            db.execute('INSERT OR REPLACE INTO token_meta VALUES (?,?)', ('indexedAt', str(time.time())))
            db.execute('INSERT OR REPLACE INTO token_meta VALUES (?,?)', ('errors', json.dumps(errors)))
        return self.snapshot(profiles)

    def snapshot(self, profiles):
        cutoff = time.time() - RETENTION
        active = {p['id']: p for p in profiles}
        accounts = {p['id']: {'id': p['id'], 'provider': p['provider'], 'label': p['label'],
                    'hours': [], 'fileCount': 0, 'recordCount': 0, 'status': 'empty'} for p in profiles}
        events = {}
        supported = defaultdict(bool)
        with self.connect() as db:
            meta = dict(db.execute('SELECT key,value FROM token_meta'))
            for path, account, provider, raw in db.execute('SELECT path,account,provider,data FROM token_files ORDER BY path'):
                if account not in active:
                    continue
                data = json.loads(raw)
                accounts[account]['fileCount'] += 1
                supported[account] |= data['supported']
                if data.get('malformed'):
                    accounts[account]['status'] = 'partial'
                for key, at, tokens in data['events']:
                    if at < cutoff:
                        continue
                    identity = (provider, key)
                    old = events.get(identity)
                    if old:
                        # Copied/resumed sessions can share request IDs. Count each
                        # once globally, even if a second profile also contains it.
                        events[identity] = (old[0], min(at, old[1]), max(tokens, old[2]))
                    else:
                        events[identity] = (account, at, tokens)
        hours = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for account, at, tokens in events.values():
            hour = int(at // 3600) * 3600
            hours[account][hour][0] += tokens
            hours[account][hour][1] += 1
            accounts[account]['recordCount'] += 1
        errors = json.loads(meta.get('errors', '{}'))
        for account in accounts.values():
            ident = account['id']
            account['hours'] = [{'at': at, 'tokens': values[0], 'records': values[1]} for at, values in sorted(hours[ident].items())]
            if errors.get(ident):
                account['status'] = 'partial'
            elif account['status'] != 'partial':
                account['status'] = 'ok' if supported[ident] else 'empty'
        global_hours = defaultdict(lambda: [0, 0])
        for account in accounts.values():
            for row in account['hours']:
                global_hours[row['at']][0] += row['tokens']
                global_hours[row['at']][1] += row['records']
        return {'accounts': list(accounts.values()),
                'hours': [{'at': at, 'tokens': values[0], 'records': values[1]} for at, values in sorted(global_hours.items())],
                'indexedAt': float(meta.get('indexedAt', '0')) or None,
                'retentionDays': 90, 'source': 'Local CLI session logs', 'timezone': 'UTC',
                'note': 'Includes cached input tokens. Covers this machine only. Account attribution follows profile log directories; earlier sign-ins in a profile may be included.'}
