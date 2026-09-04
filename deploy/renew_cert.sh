#!/usr/bin/env bash
# Certificate freshness check / nudge for the Caddy-fronted dashboard.
#
# NOTE: Caddy already renews Let's Encrypt certificates automatically (~30
# days before expiry) with no help needed. This script is a belt-and-
# suspenders monitor: it checks how long the live cert has left and, if it
# is inside the renewal window and Caddy somehow hasn't rotated it, reloads
# Caddy (which re-evaluates certificates) and logs the outcome.
#
# Run on the VM. Suggested cron (daily at 06:15):
#   15 6 * * * /home/ubuntu/visheshak/deploy/renew_cert.sh >> /home/ubuntu/cert_renew.log 2>&1
set -euo pipefail

DOMAIN="${DOMAIN:-visheshak.duckdns.org}"
WARN_DAYS="${WARN_DAYS:-21}"   # act if fewer days than this remain

not_after=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
  | openssl x509 -noout -enddate | cut -d= -f2)
if [ -z "$not_after" ]; then
  echo "$(date -Is) ERROR: could not read certificate for $DOMAIN"
  exit 1
fi

end_epoch=$(date -d "$not_after" +%s)
now_epoch=$(date +%s)
days_left=$(( (end_epoch - now_epoch) / 86400 ))
echo "$(date -Is) $DOMAIN cert expires: $not_after (${days_left}d left)"

if [ "$days_left" -lt "$WARN_DAYS" ]; then
  echo "$(date -Is) inside renewal window and not yet rotated -- reloading caddy"
  sudo systemctl reload caddy || sudo systemctl restart caddy
  sleep 20
  new_after=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
    | openssl x509 -noout -enddate | cut -d= -f2)
  if [ "$new_after" != "$not_after" ]; then
    echo "$(date -Is) renewed: now expires $new_after"
  else
    echo "$(date -Is) WARNING: still $not_after -- check 'journalctl -u caddy' (port 80 reachable? rate limits?)"
  fi
else
  echo "$(date -Is) OK: no action needed"
fi
