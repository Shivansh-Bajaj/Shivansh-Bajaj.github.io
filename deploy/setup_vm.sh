#!/usr/bin/env bash
# One-shot setup on a fresh Oracle Ubuntu VM. Run as the ubuntu user from
# inside the uploaded project directory: bash deploy/setup_vm.sh
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo ">>> EDIT .env WITH REAL KEYS (nano .env), then re-run this script." >&2
  exit 1
fi

# Oracle Ubuntu images ship iptables rules that reject most inbound traffic;
# allow the dashboard port locally (the OCI Security List must ALSO allow it).
sudo iptables -I INPUT 5 -p tcp --dport 8501 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true

sudo cp deploy/visheshak-agent.service deploy/visheshak-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now visheshak-web
echo "Dashboard up. Start the AGENT only when you intend to trade:"
echo "  sudo systemctl enable --now visheshak-agent"
echo "  tail -f run.log"
