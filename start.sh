#!/usr/bin/env bash
# Space entrypoint: agent loop in the background, dashboard in the foreground.
# The dashboard is the process the Space health-checks; if it dies, the Space
# restarts the container and recover.reconcile() rebuilds state from Alpaca.
set -u

if [ "${RUN_AGENT:-1}" = "1" ]; then
  echo "starting agent loop (RUN_AGENT=1)"
  python main.py run >> agent.log 2>&1 &
else
  echo "agent loop disabled (RUN_AGENT=${RUN_AGENT:-})"
fi

exec python webui.py
