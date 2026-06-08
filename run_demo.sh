#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

host="${DEMO_HOST:-127.0.0.1}"
port="${DEMO_PORT:-8015}"

if ! command -v dolt >/dev/null 2>&1; then
  echo "Missing dolt. Install it with:" >&2
  echo "  curl -L --fail -o /tmp/dolt.tar.gz https://github.com/dolthub/dolt/releases/latest/download/dolt-linux-amd64.tar.gz" >&2
  exit 1
fi
if ! command -v redis-server >/dev/null 2>&1; then
  echo "Missing redis-server. Install it with: sudo apt-get install -y redis-server" >&2
  exit 1
fi
if [[ ! -x /users/alexxjk/Andy_StateFork/checkpoint-lite ]]; then
  echo "Missing /users/alexxjk/Andy_StateFork/checkpoint-lite" >&2
  exit 1
fi

python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt

export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
export DEMO_STATEFORK_ROOT="${DEMO_STATEFORK_ROOT:-/users/alexxjk/Andy_StateFork}"
export CHECKPOINT_SESSIONS_DIR="${CHECKPOINT_SESSIONS_DIR:-/tmp/checkpoint-sessions-db-branch-demo}"
export WAYPOINT_SESSIONS_DIR="${WAYPOINT_SESSIONS_DIR:-$CHECKPOINT_SESSIONS_DIR}"
export WAYPOINT_PRESERVE_SESSION_ON_CLEANUP="${WAYPOINT_PRESERVE_SESSION_ON_CLEANUP:-true}"

if sudo lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $port is already in use." >&2
  sudo lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
  exit 1
fi

cat <<EOF
Starting StateFork vs DB-branch demo on the VM.

URL:               http://${host}:${port}
Dolt arm Redis:    127.0.0.1:6396
StateFork Redis:   127.0.0.1:6397 inside the Waypoint session
StateFork root:    ${DEMO_STATEFORK_ROOT}
Sessions dir:      ${CHECKPOINT_SESSIONS_DIR}

Use SSH forwarding from your laptop if needed:
  ssh -N -L ${port}:127.0.0.1:${port} sf-exp

Stop the VM service:
  press Ctrl-C in this terminal, or if it is running in the demo tmux session:
  tmux kill-session -t statefork-db-demo

Stop the laptop tunnel:
  press Ctrl-C in the ssh forwarding terminal.
EOF

exec sudo -E "${PWD}/.venv/bin/uvicorn" statefork_demo.app:app --host "$host" --port "$port"
