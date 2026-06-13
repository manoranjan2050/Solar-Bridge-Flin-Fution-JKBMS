#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  cf_manage.sh — privileged Cloudflare Tunnel helper for the dashboard    ║
# ║  Called ONLY via: sudo /opt/solar-bridge/cf_manage.sh <action> [arg]    ║
# ║  Actions: status | restart | set-hostname <fqdn>                        ║
# ║  Strictly validates input so the web UI can never inject a command.     ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -euo pipefail

CFG=/etc/cloudflared/config.yml
ACTION="${1:-}"

emit() { echo "$1"; }   # one-line machine-readable result for the dashboard

case "$ACTION" in
  status)
    running=no
    systemctl is-active --quiet cloudflared && running=yes
    host=""
    tunnel=""
    if [[ -f "$CFG" ]]; then
      host=$(grep -E '^\s*-?\s*hostname:' "$CFG" | head -1 | sed 's/.*hostname:\s*//; s/\s*$//')
      tunnel=$(grep -E '^\s*tunnel:' "$CFG" | head -1 | sed 's/.*tunnel:\s*//; s/\s*$//')
    fi
    installed=no; command -v cloudflared >/dev/null 2>&1 && installed=yes
    configured=no; [[ -n "$host" ]] && configured=yes
    emit "installed=$installed running=$running configured=$configured host=$host tunnel=$tunnel"
    ;;

  restart)
    systemctl restart cloudflared
    emit "ok=restarted"
    ;;

  set-hostname)
    NEW="${2:-}"
    # strict FQDN validation: labels of letters/digits/hyphen, dot-separated, valid TLD
    if [[ ! "$NEW" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
      emit "error=invalid hostname"; exit 1
    fi
    [[ -f "$CFG" ]] || { emit "error=tunnel not configured yet"; exit 1; }
    TUNNEL_ID=$(grep -E '^\s*tunnel:' "$CFG" | head -1 | sed 's/.*tunnel:\s*//; s/\s*$//')
    CREDS=$(grep -E '^\s*credentials-file:' "$CFG" | head -1 | sed 's/.*credentials-file:\s*//; s/\s*$//')
    PORT=$(grep -oE 'localhost:[0-9]+' "$CFG" | head -1 | cut -d: -f2); PORT="${PORT:-8080}"
    [[ -n "$TUNNEL_ID" ]] || { emit "error=no tunnel id in config"; exit 1; }

    # rewrite config with the new hostname (keep http2 — QUIC/UDP is unreliable on many ISPs)
    cat > "$CFG" <<YAML
tunnel: $TUNNEL_ID
credentials-file: $CREDS
protocol: http2

ingress:
  - hostname: $NEW
    service: http://localhost:$PORT
  - service: http_status:404
YAML

    # create the DNS route (runs as the invoking user's cert if present, else root cert)
    OWNER=$(stat -c '%U' "$(dirname "$CREDS")" 2>/dev/null || echo root)
    if sudo -u "${SUDO_USER:-$OWNER}" cloudflared tunnel route dns "$TUNNEL_ID" "$NEW" >/dev/null 2>&1; then
      route=ok
    else
      route=exists_or_failed
    fi
    systemctl restart cloudflared
    emit "ok=set host=$NEW route=$route"
    ;;

  *)
    emit "error=unknown action"; exit 1
    ;;
esac
