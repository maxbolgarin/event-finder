#!/usr/bin/env bash
# Run the NL concert tracker in its own virtualenv: ./tracker.sh <command> [args]
# (The cloud image's system Python ships a broken `cryptography`, so a plain
# `pip install -r requirements.txt` into the system interpreter doesn't work.)
set -euo pipefail
cd "$(dirname "$0")"
VENV="${TRACKER_VENV:-.venv}"
want="$(python3 -c 'import hashlib;print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$VENV/.requirements.sha" 2>/dev/null)" != "$want" ]; then
  python3 -m venv "$VENV" >/dev/null
  "$VENV/bin/pip" install -q --disable-pip-version-check -r requirements.txt
  echo "$want" > "$VENV/.requirements.sha"
fi
exec "$VENV/bin/python" -m tracker "$@"
