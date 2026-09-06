"""Read-only quota adapters. No model prompts, account switching, or token copying."""
import datetime as dt
import fcntl
import json
import math
import os
import platform
import pty
import re
import select
import selectors
import shutil
import signal
import socket
import struct
import subprocess
import termios
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from .profiles import read_json


class Unavailable(Exception):
    pass


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def stamp(value):
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError, TypeError):
        return None


def window(key, label, percentage, minutes=None, reset=None, **extras):
    return {'id': key, 'label': label, 'usedPercent': number(percentage),
            'durationMinutes': minutes, 'resetsAt': stamp(reset), **extras}


def duration_label(minutes):
    if minutes == 10080:
        return 'Weekly'
    if minutes == 1440:
        return 'Daily'
    if minutes is None:
        return 'Usage'
    if minutes >= 40320:
        return 'Monthly' if minutes <= 44640 else f'{minutes / 1440:g} days'
    return f'{minutes / 60:g} hours' if minutes % 60 == 0 else f'{minutes:g} minutes'


def executable(provider):
    known = {'codex': '.local/share/pnpm/codex', 'claude': '.local/bin/claude', 'kimi': '.kimi-code/bin/kimi'}
    result = shutil.which(provider) or str(Path.home() / known[provider])
    if not Path(result).is_file():
        raise Unavailable(f'{provider.title()} CLI is not installed.')
    return result


def clean_env(provider, home):
    env = os.environ.copy()
    for key in ('CLAUDECODE', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'OPENAI_API_KEY'):
        env.pop(key, None)
    env[{'codex': 'CODEX_HOME', 'claude': 'CLAUDE_CONFIG_DIR', 'kimi': 'KIMI_CODE_HOME'}[provider]] = str(home)
    env['TERM'] = 'xterm-256color'
    env['KIMI_CODE_NO_AUTO_UPDATE'] = '1'
    env['DISABLE_AUTOUPDATER'] = '1'
    if provider == 'claude' and Path(home) == Path.home() / '.claude':
        env.pop('CLAUDE_CONFIG_DIR', None)
    return env


def stop_process(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        except ProcessLookupError:
            pass


class CodexRPC:
    def __init__(self, home, cwd):
        self.proc = subprocess.Popen([executable('codex'), 'app-server', '--listen', 'stdio://'],
            cwd=cwd, env=clean_env('codex', home), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        self.buffer = b''
        self.counter = 0

    def send(self, payload):
        self.proc.stdin.write((json.dumps(payload) + '\n').encode())
        self.proc.stdin.flush()

    def call(self, method, params=None):
        self.counter += 1
        self.send({'id': self.counter, 'method': method, 'params': params or {}})
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get('id') == self.counter:
                    if 'error' in msg:
                        raise Unavailable('Codex could not read this account. Open its CLI and check /status.')
                    return msg.get('result', {})
            if self.selector.select(min(1, max(0, deadline-time.monotonic()))):
                data = os.read(self.proc.stdout.fileno(), 65536)
                if not data:
                    raise Unavailable('Codex exited before returning usage.')
                self.buffer += data
                if len(self.buffer) > 4_000_000:
                    raise Unavailable('Codex returned an oversized response.')
        raise Unavailable('Codex usage request timed out.')

    def close(self):
        stop_process(self.proc)
        self.selector.close()
        self.proc.stdin.close()
        self.proc.stdout.close()


def parse_codex(data):
    groups = data.get('rateLimitsByLimitId') or {'codex': data.get('rateLimits') or {}}
    windows = []
    extras = []
    for key, group in sorted(groups.items(), key=lambda item: (item[0] != 'codex', item[0])):
        name = group.get('limitName') or ('All models' if key == 'codex' else key)
        for slot in ('primary', 'secondary'):
            w = group.get(slot)
            if isinstance(w, dict):
                minutes = number(w.get('windowDurationMins'))
                windows.append(window(f'{key}:{slot}', duration_label(minutes), w.get('usedPercent'),
                    minutes, w.get('resetsAt'), group=name))
        credits = group.get('credits')
        if credits and key == 'codex':
            extras.append({'label': 'Credits', 'value': 'Unlimited' if credits.get('unlimited') else str(credits.get('balance') or '0')})
        individual = group.get('individualLimit')
        if isinstance(individual, dict) and number(individual.get('remainingPercent')) is not None:
            windows.append(window(f'{key}:monthly', 'Monthly spending limit', 100 - number(individual['remainingPercent']),
                None, individual.get('resetsAt'), group=name))
    resets = data.get('rateLimitResetCredits')
    if resets and number(resets.get('availableCount')) is not None:
        extras.append({'label': 'Available resets', 'value': str(resets['availableCount'])})
    return windows, extras


def codex(profile, cwd):
    rpc = CodexRPC(profile['home'], cwd)
    try:
        rpc.call('initialize', {'clientInfo': {'name': 'usage_atlas', 'version': '1.0.0'}, 'capabilities': {'experimentalApi': True}})
        rpc.send({'method': 'initialized'})
        account = rpc.call('account/read').get('account') or {}
        if account.get('type') != 'chatgpt':
            raise Unavailable('Sign in with a ChatGPT subscription in this Codex profile.')
        data = rpc.call('account/rateLimits/read')
        windows, extras = parse_codex(data)
        result = {'windows': windows, 'extras': extras, 'plan': account.get('planType'),
            'email': account.get('email'), 'source': 'Codex CLI · app-server', 'daily': [],
            'dailySource': 'Codex account activity · provider date buckets', 'dailyNote': None}
        try:
            usage = rpc.call('account/usage/read')
            result['daily'] = [{'date': b['startDate'], 'tokens': b['tokens']} for b in usage.get('dailyUsageBuckets', [])
                               if isinstance(b.get('startDate'), str) and number(b.get('tokens')) is not None]
            summary = usage.get('summary') or {}
            if summary.get('currentStreakDays') is not None:
                days = summary['currentStreakDays']
                extras.append({'label': 'Current streak', 'value': f"{days} {'day' if days == 1 else 'days'}"})
        except Unavailable:
            result['dailyNote'] = 'Daily activity is unavailable from this CLI version or account.'
        if not windows:
            raise Unavailable('Codex returned no quota windows for this account.')
        return result
    finally:
        rpc.close()


ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[78=>]')


def cli_panel(provider, home, cwd, timeout=35):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 80, 160, 0, 0))
    args = [executable(provider)]
    if provider == 'claude':
        args += ['--safe-mode', '--tools', '', '--strict-mcp-config', '--ax-screen-reader', '/usage']
    proc = None
    try:
        proc = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                                env=clean_env(provider, home), start_new_session=True)
        os.close(slave)
        slave = None
        raw = b''
        text = ''
        started = time.monotonic()
        sent = False
        complete_since = None
        while time.monotonic() - started < timeout:
            if select.select([master], [], [], .3)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                raw += chunk
                if len(raw) > 2_000_000:
                    raise Unavailable('CLI usage output exceeded the size limit.')
                text = ANSI.sub('', raw.decode(errors='replace')).replace('\r', '')
            # Only the CLI's built-in command is sent. Trust is never automated here.
            if 'Enter y/n:' in text or 'Trust this folder?' in text:
                raise Unavailable(f'One-time setup needed: open {provider} in the monitor probe folder and trust it. See Accounts.')
            if provider == 'kimi' and not sent and time.monotonic() - started > 6:
                os.write(master, b'/usage\r')
                sent = True
            complete = ('% used' in text and 'Resets' in text) if provider == 'claude' else ('Plan usage' in text and ('%' in text or 'reset' in text.lower()))
            if complete:
                complete_since = complete_since or time.monotonic()
                if time.monotonic() - complete_since > 3:
                    break
        return text
    finally:
        if proc:
            stop_process(proc)
        os.close(master)
        if slave is not None:
            os.close(slave)


def parse_claude_reset(text, now=None):
    """The CLI gives minute precision and a named timezone; preserve both."""
    tz_match = re.search(r'\(([^()]+)\)', text)
    try:
        zone = ZoneInfo(tz_match[1]) if tz_match else dt.datetime.now().astimezone().tzinfo
    except (KeyError, ValueError):
        return None
    now = dt.datetime.fromtimestamp(now or time.time(), zone)
    clean = re.sub(r'^Resets?\s+|\s*\([^()]+\)', '', text).strip()
    normalized = re.sub(r'(\d)(am|pm)', r'\1 \2', clean, flags=re.I).upper()
    for fmt in ('%b %d, %I:%M %p', '%b %d, %I %p', '%I:%M %p', '%I %p'):
        try:
            parsed = dt.datetime.strptime(normalized, fmt)
            dated = '%b' in fmt
            result = parsed.replace(year=now.year, month=parsed.month if dated else now.month,
                                    day=parsed.day if dated else now.day, tzinfo=zone)
            if not dated and result < now - dt.timedelta(minutes=2):
                result += dt.timedelta(days=1)
            if dated and result < now - dt.timedelta(days=180):
                result = result.replace(year=now.year + 1)
            return result.timestamp()
        except ValueError:
            continue
    return None


def parse_claude(text, now=None):
    windows = {}
    pattern = r'(Current session|Current week(?:\s*\([^\n]+\))?|Current month(?:\s*\([^\n]+\))?)(.*?)(?=Current session|Current week|Current month|What.s contributing|Usage credits|Extra usage|\Z)'
    for match in re.finditer(pattern, text, flags=re.S | re.I):
        title, body = match.groups()
        percentage = re.search(r'(\d+(?:\.\d+)?)%\s*(used|left)', body, re.I)
        if not percentage:
            continue
        value = float(percentage[1])
        if percentage[2].lower() == 'left':
            value = 100 - value
        minutes = 300 if 'session' in title.lower() else 10080 if 'week' in title.lower() else None
        group_match = re.search(r'\(([^)]+)\)', title)
        group = group_match[1] if group_match else 'All models'
        reset = re.search(r'Resets?\s+([^\n]+)', body)
        key = title.lower()
        windows[key] = window(key, 'Monthly' if 'month' in key else duration_label(minutes), value,
            minutes, parse_claude_reset(reset[0], now) if reset else None,
            group=group.capitalize() if group.lower() == 'all models' else group,
            resetText=reset[0] if reset else None, precision='CLI · minute precision')
    return list(windows.values())


def claude(profile, cwd):
    text = cli_panel('claude', profile['home'], cwd)
    windows = parse_claude(text)
    if not windows:
        raise Unavailable('Claude returned no usage panel. Open this profile and run /usage to check sign-in.')
    oauth = read_json(Path(profile['home']) / '.credentials.json').get('claudeAiOauth') or {}
    cache = read_json(Path(profile['home']) / 'stats-cache.json')
    daily = []
    for row in cache.get('dailyModelTokens', []):
        counts = [number(v) for v in (row.get('tokensByModel') or {}).values()]
        if counts and all(v is not None for v in counts):
            daily.append({'date': row['date'], 'tokens': sum(counts)})
    extras = []
    if 'Usage credits are off' in text:
        extras.append({'label': 'Usage credits', 'value': 'Off'})
    promo = re.search(r'\+\d+% weekly limits promo[^\n]+', text)
    if promo:
        extras.append({'label': 'Promotion', 'value': promo[0].split(' · ')[0]})
    return {'windows': windows, 'extras': extras, 'plan': oauth.get('subscriptionType'),
            'source': 'Claude Code CLI · /usage', 'daily': daily,
            'dailySource': 'Claude CLI stats cache · this machine only',
            'dailyNote': 'CLI cached activity; last computed ' + str(cache.get('lastComputedDate') or 'unknown') + '. May include prior sign-ins in this profile.'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def kimi_request(home):
    home = Path(home)
    credential = read_json(home / 'credentials/kimi-code.json')
    token = credential.get('access_token')
    if not token:
        raise Unavailable('Sign in with kimi login on this machine.')
    version = subprocess.run([executable('kimi'), '--version'], capture_output=True, text=True,
                             timeout=5, env=clean_env('kimi', home)).stdout.strip()
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'User-Agent': 'usage-atlas/1.0', 'X-Msh-Platform': 'kimi_cli',
               'X-Msh-Version': version if re.fullmatch(r'[\w.\-]+', version) else 'unknown', 'X-Msh-Device-Name': socket.gethostname(),
               'X-Msh-Device-Model': platform.machine(), 'X-Msh-Os-Version': platform.release()}
    if (home / 'device_id').is_file():
        headers['X-Msh-Device-Id'] = (home / 'device_id').read_text().strip()
    request = urllib.request.Request('https://api.kimi.com/coding/v1/usages', headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        return json.loads(response.read(1_000_000))


def parse_kimi(data):
    windows = []
    units = {'TIME_UNIT_MINUTE': 1, 'TIME_UNIT_HOUR': 60, 'TIME_UNIT_DAY': 1440, 'TIME_UNIT_WEEK': 10080}
    rows = [('weekly', data.get('usage'), 10080)]
    for i, row in enumerate(data.get('limits') or []):
        spec = row.get('window') or {}
        duration = number(spec.get('duration'))
        scale = units.get(spec.get('timeUnit'))
        rows.append((f'limit:{i}', row.get('detail'), duration * scale if duration is not None and scale else None))
    for key, detail, minutes in rows:
        if not isinstance(detail, dict):
            continue
        used, limit = number(detail.get('used')), number(detail.get('limit'))
        remaining = number(detail.get('remaining'))
        if used is None and limit is not None and remaining is not None:
            used = limit - remaining
        percentage = used / limit * 100 if used is not None and limit and limit > 0 else None
        windows.append(window(key, duration_label(minutes), percentage, minutes, detail.get('resetTime'),
                              group='Kimi Code', used=used, limit=limit, remaining=remaining))
    return windows


def kimi(profile, cwd):
    credential = read_json(Path(profile['home']) / 'credentials/kimi-code.json')
    if (number(credential.get('expires_at')) or 0) < time.time() + 60:
        cli_panel('kimi', profile['home'], cwd, timeout=25)
    try:
        data = kimi_request(profile['home'])
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise Unavailable('Kimi sign-in needs attention. Run kimi login on this machine.') from None
        raise Unavailable(f'Kimi usage endpoint returned HTTP {error.code}. Will retry automatically.') from None
    windows = parse_kimi(data)
    if not windows or not any(w['usedPercent'] is not None for w in windows):
        raise Unavailable('Kimi returned no measurable quota windows.')
    extras = []
    parallel = number((data.get('parallel') or {}).get('limit'))
    if parallel is not None:
        extras.append({'label': 'Parallel requests', 'value': f'{parallel:g}'})
    level = ((data.get('user') or {}).get('membership') or {}).get('level')
    plan = level.removeprefix('LEVEL_').replace('_', ' ').title() if isinstance(level, str) else 'Kimi Code'
    return {'windows': windows, 'extras': extras, 'plan': plan, 'source': 'Kimi official usage API',
            'daily': [], 'dailySource': 'Not exposed by the Kimi usage API',
            'dailyNote': 'Daily token history is unavailable. Quota history is recorded from the first poll.'}


ADAPTERS = {'codex': codex, 'claude': claude, 'kimi': kimi}
