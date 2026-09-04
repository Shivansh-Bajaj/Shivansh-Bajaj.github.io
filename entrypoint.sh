#!/usr/bin/env bash
# Start (or restart) Visheshak on the VM: dashboard + agent loop.
# Idempotent: safe to run after every publish. RUN_AGENT=0 skips the agent.
set -euo pipefail
cd "$(dirname "$0")"

# First run: make sure venv support exists (fresh Ubuntu lacks ensurepip),
# then build the venv.
if [ ! -d .venv ]; then
  if ! python3 -m venv --help >/dev/null 2>&1 || ! python3 -c "import ensurepip" 2>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-venv python3-pip
  fi
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip -q
fi
.venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
  echo "!! no .env -- copy .env.example and fill in keys, then rerun" >&2
  exit 1
fi

# Oracle Ubuntu ships iptables that reject all but SSH; ensure 8501 is open
# (idempotent: only inserts if the rule isn't already present).
if command -v iptables >/dev/null && ! sudo iptables -C INPUT -p tcp --dport 8501 -m state --state NEW -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 5 -p tcp --dport 8501 -m state --state NEW -j ACCEPT || true
fi

# Restart cleanly: kill previous instances of ours only.
pkill -f "python main.py run" 2>/dev/null && echo "stopped old agent" || true
pkill -f "python webui.py" 2>/dev/null && echo "stopped old dashboard" || true
sleep 1

HOST=0.0.0.0 PORT=8501 nohup .venv/bin/python webui.py >> web.log 2>&1 &
echo "dashboard started (web.log) -> http://$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'):8501"

if [ "${RUN_AGENT:-1}" = "1" ]; then
  nohup .venv/bin/python main.py run >> run.log 2>&1 &
  echo "agent loop started (run.log)"
else
  echo "agent loop NOT started (RUN_AGENT=0)"
fi
