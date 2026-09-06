#!/usr/bin/env python3
"""Install the user boot unit. Run as the account that owns the CLI profiles."""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
if any(c in str(root) for c in ('\n', '"', '%')):
    raise SystemExit('Unsupported characters in repository path.')
unit_dir = Path.home() / '.config/systemd/user'
unit_dir.mkdir(parents=True, exist_ok=True)
unit = unit_dir / 'usage-atlas-boot.service'
unit.write_text(f'''[Unit]
Description=Restore Usage Atlas personal dashboard

[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart=/usr/bin/python3 "{root}/scripts/boot.py"
RemainAfterExit=yes
TimeoutStartSec=150

[Install]
WantedBy=default.target
''')
subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
subprocess.run(['systemctl', '--user', 'enable', 'usage-atlas-boot.service'], check=True)
print(f'Installed {unit}')
print('The Devslot application must already be started and routed. User lingering must be enabled for boot without login.')
