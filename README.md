# Usage Atlas

A personal, mobile-friendly dashboard for Codex, Claude Code, and Kimi Code subscription usage. Python 3.11+ serves the UI and polls the installed CLIs or official usage API. SQLite keeps 90 days of quota history. There are no package dependencies or frontend build steps.

## Features

- Multiple Codex, Claude Code, and Kimi Code profiles, discovered automatically.
- Compact mobile layout with usage bars, reset countdowns, provider filters, and account details.
- Five-minute provider polling, manual refresh, and 90 days of quota history.
- Daily token charts where the provider or CLI exposes activity.
- Global and per-account hourly token heatmaps for 7, 14, 30, or 90 days, with tappable cells and keyboard navigation.
- Square-edged panels and dense quota cards that fit narrow phone screens.
- Per-account stale readings when a provider cannot be reached.
- Optional Tailscale access and systemd startup through Devslot and Caddy.

The app runs on Linux and requires Python 3.11 or newer plus the authenticated provider CLIs. It has no package dependencies. Node.js is only needed for the optional JavaScript checks and heatmap tests.

## Quick start

Clone the repository and start the server:

```sh
git clone https://github.com/afourteia/usage-atlas.git
cd usage-atlas
python3 server.py
```

Open `http://127.0.0.1:8080`. The collector discovers existing CLI profiles and starts polling in the background. Use the Accounts button for profile setup instructions.

For persistent background operation and Tailscale access, follow the Devslot setup under **Operate**. Devslot is an external host tool, not bundled with this repository. Its host configuration supplies the port reservations and Caddy integration. The standalone command above does not require it.

This is a personal observation tool for trusted devices. Keep it on loopback or a private network. It has no separate login; anyone allowed to reach it can view account names and usage.

## What the numbers mean

| Provider | Quota source | Daily activity |
| --- | --- | --- |
| Codex | Official `codex app-server` stdio RPC, `account/rateLimits/read` | `account/usage/read`, account-wide provider date buckets |
| Claude | Official CLI's interactive `/usage`, with tools and customizations disabled | CLI `stats-cache.json`, limited to cached activity on this machine |
| Kimi | Official `https://api.kimi.com/coding/v1/usages` endpoint | Not exposed by this quota endpoint |

The collector sends no inference prompts. Claude runs in safe mode. Kimi's own CLI refreshes its credential when needed by opening its built-in `/usage` panel. The collector does not implement token rotation, copy credentials, switch accounts, import browser cookies, or call private Claude web endpoints. Background probes disable automatic CLI updates.

Quota percentages are provider measurements. They are not inferred from tokens or money. Only reported windows appear, including weekly, short-duration, model-specific, and Codex monthly spending limits when available. The primary Codex window is not necessarily a five-hour window. Missing readings remain unknown. Failed polls preserve the last successful reading, its timestamp, and a stale status.

Claude reset times have minute precision. If a future CLI changes the date format, the original reset text is preserved instead of inventing a timestamp. All parsed reset timestamps display in the viewing browser's timezone. A countdown reaching zero waits for a new measurement.

Daily activity is not the same as subscription quota. Codex uses the dates returned by its account service and may not have a bucket for today yet. Claude's cache can lag and can include earlier sign-ins in the same profile. Missing dates are displayed as unreported, not zero. Kimi still gets quota history from collected samples. History charts show hourly averages over a seven-day timeline and keep gaps between readings.

Hourly heatmaps read timestamped token records from the installed CLIs' local logs. Codex uses native request records when present and cumulative token-count deltas for older logs. Claude assistant response records are deduplicated across content blocks. Kimi uses `usage.record` events. Repeated request IDs, copied legacy snapshots, and shared profile-directory symlinks count once. Counts include input, cached input, and output without adding already-included cache or reasoning tokens twice.

These heatmaps cover this machine's retained logs, attributed to their current profile. They can include earlier sign-ins and exclude activity on other devices. They are not subscription percentages and need not match the separate provider daily chart. Empty cells mean no recorded local activity. Rows and hours use UTC; tapping a cell shows its exact count. The account selector and each account's details show individual heatmaps. All accounts uses the sum of the account views, and each view scales its colors to its own busiest hour.

Token indexing runs independently of quota polling every five minutes. The first scan may take a minute or more for large log directories. Later scans reuse unchanged files. The index retains 90 days of timestamps, token counts, and hashed record identities; it does not store conversation text.

Additional information includes Codex reset-credit counts and streaks, Claude's reported promotion and usage-credit status, and Kimi's membership level and parallel-request limit. Atlas never spends credits or redeems a reset.

## Add an account

Use separate CLI profile directories. For example:

```sh
CODEX_HOME="$HOME/.codex-work" codex login
CLAUDE_CONFIG_DIR="$HOME/.claude-work" claude auth login
```

Atlas discovers directories matching `~/.codex*`, `~/.claude*`, and `~/.kimi-code*` when they contain the expected credentials. It rescans at each poll, so adding another profile does not require restarting the server.

For Claude and Kimi, open the CLI once in `/tmp/usage-atlas-probe` using the same profile environment, complete any onboarding, and trust that empty folder. The collector itself never accepts trust prompts. For example:

```sh
cd /tmp/usage-atlas-probe
CLAUDE_CONFIG_DIR="$HOME/.claude-work" claude --safe-mode
```

Keep the probe folder empty. It is private to the service user and recreated with mode `0700` after a reboot. Existing trust decisions are stored by each CLI. Authentication and onboarding require the account owner; the web UI only supplies terminal commands.

Custom paths and display names can be configured in `accounts.json`, which is ignored by Git:

```json
{
  "accounts": [
    {"provider": "codex", "home": "~/.codex-work", "label": "Codex work"},
    {"provider": "claude", "home": "~/.claude-work", "label": "Claude work"}
  ]
}
```

Codex deduplication uses its subscription account ID, preserving separate workspaces. Claude uses its profile account UUID when available, otherwise a one-way credential fingerprint. Copies sharing an OAuth token collapse; separately issued tokens without account metadata cannot reliably be recognized as the same Claude account. No credential values enter the dashboard payload or database.

## Operate

Run these from the repository:

```sh
devslot status atlas
devslot check atlas
devslot logs atlas
devslot restart atlas
devslot url atlas
systemctl --user status usage-atlas-boot.service
```

Disable automatic startup with `systemctl --user disable usage-atlas-boot.service`. Stop the current app with `devslot stop atlas`. Disable first if you want it to stay stopped after reboot.

For a new machine with Devslot configured:

```sh
devslot start atlas
devslot check atlas
devslot route add atlas
python3 scripts/install-service.py
systemctl --user start usage-atlas-boot.service
```

Enable user lingering with `loginctl enable-linger "$USER"` if necessary. Tailscale and Caddy must start at boot. The boot script uses the existing Devslot reservation, so the application and preview ports remain stable. Do not move the repository without reinstalling its boot unit and updating the Devslot reservation.

Environment options:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | App bind address |
| `PORT` | Devslot reservation, otherwise `8080` | App port |
| `ATLAS_POLL_SECONDS` | `300` | Provider polling interval, minimum 180 seconds |
| `ATLAS_DATA_DIR` | `.data` in the repository | Private SQLite storage |
| `ATLAS_PROBE_DIR` | `/tmp/usage-atlas-probe` | Empty, trusted CLI working directory |
| `ATLAS_ALLOWED_HOSTS` | Empty | Additional comma-separated Host header names |

Loopback, the machine hostname, and Devslot's configured Tailscale IP are allowed Host values. Add a MagicDNS name explicitly to `ATLAS_ALLOWED_HOSTS` before using it. The API rejects foreign hosts and cross-site browser requests. Refresh requires a custom same-origin header. Static serving uses an explicit file allowlist.

Data lives in `.data/usage.sqlite3` and `.data/activity.sqlite3` inside a private directory. Stop the app before copying these files for a simple backup, or use SQLite's backup API while running. History and local account configuration are excluded from Git.

## Validation

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q atlas server.py scripts
node --check public/app.js
node --check public/heatmap.mjs
node --test tests/heatmap.test.mjs
```

Tests cover hourly UTC bucketing, copied-log deduplication, cache-inclusive token arithmetic, index updates and removals, primary-window duration, scoped and monthly windows, sparse Kimi quota responses, Claude terminal redraws and timezone parsing, Codex profile deduplication, stale-cache persistence, static-file boundaries, Host validation, and refresh request protection. Live checks also covered Tailscale HTTP access, desktop and phone layouts, filtering, dialogs, daily activity selection, and the boot restore path.

## Sources

- [Official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server). Installed protocol schemas were generated with `codex app-server generate-ts --experimental`; the installed CLI accepts an empty rate-limit request, despite newer docs showing optional request fields.
- [Claude Code usage-limit documentation](https://code.claude.com/docs/en/errors) and [status-line documentation](https://code.claude.com/docs/en/statusline). The collector reads `/usage` directly and does not depend on an active inference session's status line.
- [Kimi's official usage implementation](https://github.com/MoonshotAI/kimi-code/blob/main/packages/oauth/src/managed-usage.ts) and [device identity implementation](https://github.com/MoonshotAI/kimi-code/blob/main/packages/oauth/src/identity.ts).
- [CodexBar's Claude notes](https://github.com/steipete/CodexBar/blob/main/docs/claude.md) and [Kimi notes](https://github.com/steipete/CodexBar/blob/main/docs/kimi.md) informed the source selection and stale-reading behavior. Atlas is an independent implementation.

Subscription usage interfaces can change. A provider failure is shown per account and does not take down the other cards.
