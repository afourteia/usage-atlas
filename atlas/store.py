import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS latest (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples (
                    account TEXT NOT NULL, window TEXT NOT NULL, at REAL NOT NULL,
                    used REAL NOT NULL, reset REAL, PRIMARY KEY(account, window, at));
                CREATE INDEX IF NOT EXISTS samples_at ON samples(at);
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def load(self):
        with self.connect() as db:
            return {row[0]: json.loads(row[1]) for row in db.execute('SELECT id,data FROM latest')}

    def save(self, account):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO latest VALUES (?, ?)', (account['id'], json.dumps(account)))
            if account['status'] == 'ok':
                for w in account.get('windows', []):
                    if w.get('usedPercent') is not None:
                        db.execute('INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?)',
                            (account['id'], w['id'], account['updatedAt'], w['usedPercent'], w.get('resetsAt')))
            db.execute('DELETE FROM samples WHERE at < ?', (time.time() - 90 * 86400,))

    def history(self, account, window, days=7):
        # Hourly averages keep 90-day histories bounded for mobile clients.
        with self.connect() as db:
            return [{'at': row[0], 'usedPercent': row[1]} for row in db.execute(
                '''SELECT MAX(at), AVG(used) FROM samples WHERE account=? AND window=? AND at>=?
                   GROUP BY CAST(at/3600 AS INTEGER) ORDER BY MAX(at)''',
                (account, window, time.time() - days * 86400))]
