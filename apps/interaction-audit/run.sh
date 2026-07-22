#!/usr/bin/env bash
# Launch the live Customer Interaction Audit backend.
# Requires: VPN + Okta SSO (first Snowflake query opens a browser login).
set -e
APPDIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8799}"

python3 -m pip install --quiet fastapi uvicorn requests >/dev/null 2>&1 || true

# Load a local .env (never committed) for SLACK_USER_TOKEN etc.
[ -f "$APPDIR/.env" ] && set -a && . "$APPDIR/.env" && set +a

cd "$APPDIR"
if [ -n "$SLACK_USER_TOKEN$SLACK_TOKEN" ]; then
  echo "  Slack: LIVE search enabled (token found)"
else
  echo "  Slack: deep-link search (set SLACK_USER_TOKEN for live auto-search)"
fi
echo "──────────────────────────────────────────────────────────────"
echo "  Customer Interaction Audit (LIVE)"
echo "  Local:      http://localhost:$PORT"
echo "  Shareable:  in another terminal →  cloudflared tunnel --url http://localhost:$PORT"
echo "              (or:  ngrok http $PORT )"
echo "  Type any flip token; first lookup triggers the Okta login."
echo "──────────────────────────────────────────────────────────────"
exec python3 -m uvicorn server:app --port "$PORT" --host 0.0.0.0
