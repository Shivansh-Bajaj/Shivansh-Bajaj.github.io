#!/usr/bin/env bash
# Tail Visheshak logs on the VM.
#   bash deploy/logs.sh            # agent log (default)
#   bash deploy/logs.sh web        # dashboard log
#   bash deploy/logs.sh journal    # structured decision journal
#   bash deploy/logs.sh all        # everything interleaved
set -euo pipefail
cd "$(dirname "$0")/.."

VM="${VM:-ubuntu@132.226.103.59}"
KEY="${KEY:-.ssh/visheshak.key}"

case "${1:-agent}" in
  agent)   files="visheshak/run.log" ;;
  web)     files="visheshak/web.log" ;;
  journal) files="visheshak/journal/journal.jsonl" ;;
  all)     files="visheshak/run.log visheshak/web.log visheshak/journal/journal.jsonl" ;;
  *) echo "usage: logs.sh [agent|web|journal|all]" >&2; exit 1 ;;
esac

exec ssh -i "$KEY" "$VM" "tail -n 50 -f $files"
