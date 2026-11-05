#!/bin/sh
# Run on the Pi as the account that owns this checkout (not with sudo).
set -eu
if [ "$(uname -s)" != Linux ]; then
  echo 'Run this installer on Raspberry Pi OS Linux.' >&2
  exit 1
fi
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$PROJECT_DIR"
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/verify_simulation.py
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$SERVICE_DIR"
# Generate quoted systemd paths with Python to support spaces in the checkout path.
.venv/bin/python - "$PROJECT_DIR" "$SERVICE_DIR" <<'PY'
from pathlib import Path
import sys
root, service_dir = map(Path, sys.argv[1:])
def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
service = '''[Unit]
Description=Fairway Fusion pre-alpha local demo
After=network.target

[Service]
Type=simple
WorkingDirectory={root}
ExecStart={python} -m fairway_fusion.demo.server --port 8765
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
'''.format(root=quote(root),python=quote(root/'.venv/bin/python'))
(service_dir/'fairway-fusion-demo.service').write_text(service)
PY
systemctl --user daemon-reload
echo 'Installed. Start with: systemctl --user start fairway-fusion-demo'
echo 'Open http://127.0.0.1:8765 on the Pi. See docs/simulation_pi.md for team integration.'
