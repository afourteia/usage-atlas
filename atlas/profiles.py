import base64
import hashlib
import json
from pathlib import Path


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def discover(home=None, config=None):
    home = Path(home or Path.home())
    candidates = []
    for provider, pattern, credential in [
        ('codex', '.codex*', 'auth.json'),
        ('claude', '.claude*', '.credentials.json'),
        ('kimi', '.kimi-code*', 'credentials/kimi-code.json'),
    ]:
        for path in sorted(home.glob(pattern), key=lambda p: (len(p.name), p.name)):
            if path.is_dir() and (path / credential).is_file():
                candidates.append({'provider': provider, 'home': str(path)})
    custom = read_json(config) if config else {}
    for item in custom.get('accounts', []):
        if item.get('provider') in ('codex', 'claude', 'kimi') and isinstance(item.get('home'), str):
            entry = {k: item[k] for k in ('provider', 'home', 'label') if k in item}
            entry['home'] = str(Path(entry['home']).expanduser().resolve())
            candidates.insert(0, entry)
    seen = {}
    for item in candidates:
        path = Path(item['home']).resolve()
        provider = item['provider']
        identity = str(path)
        email = None
        if provider == 'codex':
            auth = read_json(path / 'auth.json')
            tokens = auth.get('tokens') or {}
            identity = tokens.get('account_id') or identity
            try:
                payload = tokens.get('id_token', '').split('.')[1]
                email = json.loads(base64.urlsafe_b64decode(payload + '===' )).get('email')
            except (ValueError, IndexError):
                pass
        elif provider == 'claude':
            oauth = read_json(path / '.credentials.json').get('claudeAiOauth') or {}
            # A fingerprint deduplicates copied profiles without retaining the token.
            if oauth.get('accessToken'):
                identity = hashlib.sha256(oauth['accessToken'].encode()).hexdigest()
            account = read_json(path / '.claude.json').get('oauthAccount') or {}
            if path == home / '.claude':
                account = read_json(home / '.claude.json').get('oauthAccount') or account
            email = account.get('emailAddress')
            identity = account.get('accountUuid') or identity
        key = provider + ':' + identity
        if key in seen:
            if str(path) not in seen[key]['profiles']:
                seen[key]['profiles'].append(str(path))
            continue
        seen[key] = {
            'id': provider + '-' + hashlib.sha256(key.encode()).hexdigest()[:12],
            'provider': provider, 'home': str(path), 'profiles': [str(path)],
            'label': item.get('label') or email or ('Kimi personal' if provider == 'kimi' else path.name.lstrip('.')),
            'email': email,
        }
    return list(seen.values())
