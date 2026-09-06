#!/usr/bin/env python3
"""Usage Atlas. Python 3.11+, no external packages required."""
import concurrent.futures
import copy
import json
import mimetypes
import os
import shlex
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from atlas.profiles import discover
from atlas.providers import ADAPTERS, Unavailable
from atlas.store import Store

ROOT = Path(__file__).resolve().parent


class Monitor:
    def __init__(self, data_dir=None, probe=None, interval=None):
        self.data_dir = Path(data_dir or os.environ.get('ATLAS_DATA_DIR', ROOT / '.data'))
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.probe = Path(probe or os.environ.get('ATLAS_PROBE_DIR', '/tmp/usage-atlas-probe'))
        if self.probe.is_symlink():
            raise RuntimeError('Probe directory must not be a symbolic link.')
        self.probe.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.probe.stat().st_uid != os.getuid():
            raise RuntimeError('Probe directory must belong to the service user.')
        os.chmod(self.probe, 0o700)
        self.interval = max(180, int(interval or os.environ.get('ATLAS_POLL_SECONDS', '300')))
        self.store = Store(self.data_dir / 'usage.sqlite3')
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.accounts = self.store.load()
        for account in self.accounts.values():
            account['status'] = 'stale' if account.get('updatedAt') else 'pending'
        self.polling = False
        self.next_poll = time.time()
        self.last_started = 0
        self.started = time.time()
        self.scan()

    def scan(self):
        profiles = discover(config=ROOT / 'accounts.json')
        with self.lock:
            previous = self.accounts
            self.profiles = profiles
            self.accounts = {}
            for p in profiles:
                old = previous.get(p['id'], {})
                self.accounts[p['id']] = {**old, **{k: p[k] for k in ('id', 'provider', 'label', 'email')},
                    'profileCount': len(p['profiles']), 'status': old.get('status', 'pending'),
                    'windows': old.get('windows', []), 'daily': old.get('daily', []), 'extras': old.get('extras', [])}

    def poll_one(self, profile):
        ident = profile['id']
        with self.lock:
            current = copy.deepcopy(self.accounts[ident])
        try:
            data = ADAPTERS[profile['provider']](profile, self.probe)
            current.update(data)
            current.update(status='ok', error=None, updatedAt=time.time(), checkedAt=time.time())
        except Exception as error:
            # Never send raw CLI output, credentials, paths, or HTTP bodies to the browser/journal.
            message = str(error) if isinstance(error, Unavailable) else 'The provider could not be reached. Will retry automatically.'
            current.update(status='stale' if current.get('updatedAt') else 'error', error=message, checkedAt=time.time())
        self.store.save(current)
        with self.lock:
            self.accounts[ident] = current
        print(f"poll {profile['provider']} {ident} {current['status']}", flush=True)

    def run(self):
        while not self.stop.is_set():
            with self.lock:
                self.polling = True
                self.last_started = time.time()
            try:
                self.scan()
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(self.poll_one, self.profiles))
            except Exception:
                print('poll cycle failed; retry scheduled', flush=True)
            finally:
                with self.lock:
                    self.polling = False
                    self.next_poll = time.time() + self.interval
            self.wake.wait(self.interval)
            self.wake.clear()

    def refresh(self):
        with self.lock:
            if self.polling:
                return 'already-running'
            if time.time() - self.last_started < 60:
                return 'cooldown'
            self.wake.set()
            return 'scheduled'

    def snapshot(self):
        with self.lock:
            accounts = copy.deepcopy(list(self.accounts.values()))
            for account in accounts:
                if account.get('updatedAt') and time.time() - account['updatedAt'] > self.interval * 2:
                    account['status'] = 'stale'
            return {'accounts': accounts, 'polling': self.polling, 'nextPollAt': self.next_poll,
                    'pollInterval': self.interval, 'serverTime': time.time(), 'startedAt': self.started,
                    'historyRetentionDays': 90}

    def setup(self):
        result = []
        for provider, env_key, folder in [('codex', 'CODEX_HOME', '.codex-work'),
                                           ('claude', 'CLAUDE_CONFIG_DIR', '.claude-work'),
                                           ('kimi', 'KIMI_CODE_HOME', '.kimi-code')]:
            command = f'{env_key}={shlex.quote(str(Path.home()/folder))} {provider} ' + ('auth login' if provider == 'claude' else 'login')
            result.append({'provider': provider, 'command': command})
        return {'accounts': result, 'probeDirectory': str(self.probe), 'configFile': str(ROOT / 'accounts.json')}


def handler_for(monitor):
    allowed_hosts = {'localhost', '127.0.0.1', '::1', socket.gethostname(),
                     os.environ.get('DEVSLOT_TAILSCALE_HOST', '')}
    allowed_hosts.update(os.environ.get('ATLAS_ALLOWED_HOSTS', '').split(','))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status, body, content_type='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {'error': 'Host not allowed.'})
            url = urlsplit(self.path)
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send(403, {'error': 'Cross-site requests are not allowed.'})
            if url.path == '/api/health':
                return self.send(200, {'status': 'ok', 'polling': monitor.polling})
            if url.path == '/api/snapshot':
                return self.send(200, monitor.snapshot())
            if url.path == '/api/setup':
                return self.send(200, monitor.setup())
            if url.path == '/api/history':
                query = parse_qs(url.query)
                try:
                    days = min(90, max(1, int(query.get('days', ['7'])[0])))
                except ValueError:
                    return self.send(400, {'error': 'Invalid history range.'})
                return self.send(200, monitor.store.history(query.get('account', [''])[0], query.get('window', [''])[0], days))
            routes = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/icon.svg': 'icon.svg', '/manifest.webmanifest': 'manifest.webmanifest'}
            filename = routes.get(url.path)
            if filename:
                path = ROOT / 'public' / filename
                return self.send(200, path.read_bytes(), mimetypes.guess_type(filename)[0] or 'application/octet-stream')
            self.send(404, {'error': 'Not found.'})

        def do_POST(self):
            if not self.valid_host():
                return self.send(403, {'error': 'Host not allowed.'})
            if self.path != '/api/refresh':
                return self.send(404, {'error': 'Not found.'})
            # Custom header forces a CORS preflight for hostile origins. No CORS is enabled.
            if self.headers.get('X-Atlas-Request') != '1' or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send(403, {'error': 'Same-origin request required.'})
            if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
                return self.send(400, {'error': 'No request body expected.'})
            self.send(202, {'status': monitor.refresh()})

        def valid_host(self):
            try:
                host = urlsplit('http://' + self.headers.get('Host', '')).hostname
                return host in allowed_hosts
            except ValueError:
                return False
    return Handler


def main():
    os.umask(0o077)
    monitor = Monitor()
    thread = threading.Thread(target=monitor.run, daemon=True)
    thread.start()
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8080'))), handler_for(monitor))
    server.daemon_threads = True
    def shutdown(signum, frame):
        monitor.stop.set()
        monitor.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f'Usage Atlas listening on {server.server_address[0]}:{server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
