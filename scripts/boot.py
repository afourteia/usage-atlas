#!/usr/bin/env python3
"""Restore the reserved Devslot application after a reboot."""
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[1]
devslot = str(Path.home() / '.local/bin/devslot')
os.chdir(root)
for attempt in range(12):
    started = subprocess.run([devslot, 'start', 'atlas'], check=False)
    if started.returncode == 0:
        raise SystemExit(0)
    time.sleep(5)
raise SystemExit('Could not restore Usage Atlas through Devslot.')
