#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  cf_setup.sh — unprivileged first-time Cloudflare Tunnel setup, driven   ║
# ║  by the dashboard's Network page (a step-by-step wizard, not a script    ║
# ║  you run by hand — see CLOUDFLARE_TUNNEL.md for that manual path).       ║
# ║                                                                          ║
# ║  Runs AS the dashboard's own user (solar-dashboard.service's User=),     ║
# ║  never via sudo — login/tunnel-create/DNS-route are per-user cloudflared ║
# ║  state that must land in THIS user's $HOME, not root's. Only the final  ║
# ║  step needs root (writing /etc/cloudflared, installing the service),    ║
# ║  which is delegated to the existing privileged cf_manage.sh (sudo,      ║
# ║  already whitelisted in /etc/sudoers.d/solar-bridge).                   ║
# ║                                                                          ║
# ║  Actions: login-start | login-poll | provision <fqdn> [tunnel_name]     ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -euo pipefail

ACTION="${1:-}"
CF_DIR="$HOME/.cloudflared"
LOGFILE="$CF_DIR/web_login.log"
CERT="$CF_DIR/cert.pem"
CF_MANAGE="/opt/solar-bridge/cf_manage.sh"

emit() { echo "$1"; }

case "$ACTION" in
  login-start)
    command -v cloudflared >/dev/null 2>&1 || { emit "error=cloudflared not installed"; exit 1; }
    mkdir -p "$CF_DIR"
    rm -f "$LOGFILE"
    # setsid + nohup + disown: detach fully from this request's process tree
    # so it keeps running (waiting on the browser callback) after Flask
    # returns the response.
    setsid nohup cloudflared tunnel login > "$LOGFILE" 2>&1 < /dev/null &
    disown
    emit "ok=started"
    ;;

  login-poll)
    if [[ -f "$CERT" ]]; then
      emit "authorized=yes"
    elif [[ -f "$LOGFILE" ]] && grep -qoE 'https://dash\.cloudflare\.com/[a-zA-Z0-9/_.?=&-]+' "$LOGFILE"; then
      URL="$(grep -oE 'https://dash\.cloudflare\.com/[a-zA-Z0-9/_.?=&-]+' "$LOGFILE" | head -1)"
      emit "url=$URL"
    else
      emit "pending=yes"
    fi
    ;;

  provision)
    HOSTNAME_FQDN="${2:-}"
    TUNNEL_NAME="${3:-solar-bridge}"
    [[ -z "$HOSTNAME_FQDN" ]] && { emit "error=missing hostname"; exit 1; }
    [[ -f "$CERT" ]] || { emit "error=not authorized yet"; exit 1; }
    command -v cloudflared >/dev/null 2>&1 || { emit "error=cloudflared not installed"; exit 1; }

    if cloudflared tunnel list 2>/dev/null | grep -q "[[:space:]]${TUNNEL_NAME}[[:space:]]"; then
      : # tunnel already exists — reuse it (idempotent)
    else
      cloudflared tunnel create "$TUNNEL_NAME" >/dev/null
    fi

    TUNNEL_ID="$(cloudflared tunnel list 2>/dev/null | awk -v n="$TUNNEL_NAME" '$2==n{print $1}')"
    [[ -z "$TUNNEL_ID" ]] && { emit "error=could not determine tunnel id"; exit 1; }

    cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME_FQDN" >/dev/null 2>&1 || true

    CREDS_SRC="$CF_DIR/$TUNNEL_ID.json"
    [[ -f "$CREDS_SRC" ]] || { emit "error=credentials file missing"; exit 1; }

    # Hand off to the privileged helper for the /etc/cloudflared + service bits.
    RESULT="$(sudo "$CF_MANAGE" finish-setup "$TUNNEL_ID" "$HOSTNAME_FQDN" "$CREDS_SRC")"
    emit "$RESULT"
    ;;

  *)
    emit "error=unknown action"; exit 1
    ;;
esac
