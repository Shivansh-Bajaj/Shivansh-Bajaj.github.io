#!/usr/bin/env bash
# Publish the project to the VM with rsync, then (re)start it there.
#
#   bash deploy/publish.sh                 # uses defaults below
#   VM=ubuntu@1.2.3.4 KEY=~/.ssh/foo.key bash deploy/publish.sh
#
# Never ships local secrets or runtime state: .env, state/, journal/ stay put
# on each side. First deploy: ssh in and create .env from .env.example.
set -euo pipefail
cd "$(dirname "$0")/.."

VM="${VM:-ubuntu@132.226.103.59}"
KEY="${KEY:-.ssh/visheshak.key}"   # project-local key (gitignored)
SSH="ssh -i $KEY"

rsync -av --delete \
  --exclude .venv --exclude __pycache__ --exclude .git \
  --exclude .env --exclude state --exclude journal \
  --exclude '*.log' --exclude .DS_Store --exclude .ssh --exclude presentation \
  -e "$SSH" ./ "$VM":~/visheshak/

# Ship the local .env too (kept out of the rsync above so --delete can
# never wipe it; sent separately over the same encrypted channel).
if [ -f .env ]; then
  scp -i "$KEY" .env "$VM":~/visheshak/.env
fi

$SSH "$VM" 'cd ~/visheshak && bash entrypoint.sh'
