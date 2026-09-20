#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  cf_manage.sh — privileged Cloudflare Tunnel helper for the dashboard    ║
# ║  Called ONLY via: sudo /opt/solar-bridge/cf_manage.sh <action> [arg]    ║
# ║  Actions: status | restart | set-hostname <fqdn> | install-cloudflared  ║
# ║           | finish-setup <tunnel_id> <fqdn> <creds_path>                ║
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

  install-cloudflared)
    if command -v cloudflared >/dev/null 2>&1; then
      emit "ok=already_installed"
      exit 0
    fi
    ARCH="$(dpkg --print-architecture)"
    case "$ARCH" in
      arm64|armhf) ;;
      *) emit "error=unsupported arch $ARCH"; exit 1 ;;
    esac
    TMPDEB="$(mktemp --suffix=.deb)"
    if curl -fsSL -o "$TMPDEB" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"; then
      dpkg -i "$TMPDEB" >/dev/null 2>&1 || apt-get install -f -y >/dev/null 2>&1
      rm -f "$TMPDEB"
      if command -v cloudflared >/dev/null 2>&1; then
        emit "ok=installed"
      else
        emit "error=install failed"; exit 1
      fi
    else
      rm -f "$TMPDEB"
      emit "error=download failed"; exit 1
    fi
    ;;

  finish-setup)
    # Completes first-time setup after the unprivileged cf_setup.sh has
    # authorised, created the tunnel, and routed DNS (all per-user
    # cloudflared state — no root needed for those). This action only does
    # the parts that genuinely need root: writing /etc/cloudflared, and
    # installing/starting the system service.
    TUNNEL_ID="${2:-}"; NEW="${3:-}"; CREDS_SRC="${4:-}"

    [[ "$TUNNEL_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || \
      { emit "error=invalid tunnel id"; exit 1; }
    if [[ ! "$NEW" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
      emit "error=invalid hostname"; exit 1
    fi
    # Restrict the credentials source to a real user's own .cloudflared dir,
    # named after the exact validated tunnel ID above — never an arbitrary path.
    if [[ ! "$CREDS_SRC" =~ ^/home/[a-zA-Z0-9_-]+/\.cloudflared/${TUNNEL_ID}\.json$ ]]; then
      emit "error=invalid credentials path"; exit 1
    fi
    [[ -f "$CREDS_SRC" ]] || { emit "error=credentials file not found"; exit 1; }

    mkdir -p /etc/cloudflared
    cp "$CREDS_SRC" "/etc/cloudflared/$TUNNEL_ID.json"

    cat > "$CFG" <<YAML
tunnel: $TUNNEL_ID
credentials-file: /etc/cloudflared/$TUNNEL_ID.json
protocol: http2

ingress:
  - hostname: $NEW
    service: http://localhost:8080
  - service: http_status:404
YAML

    cloudflared service install >/dev/null 2>&1 || true
    systemctl enable cloudflared >/dev/null 2>&1 || true
    systemctl restart cloudflared
    sleep 2
    running=no
    systemctl is-active --quiet cloudflared && running=yes
    emit "ok=finished host=$NEW tunnel=$TUNNEL_ID running=$running"
    ;;

  *)
    emit "error=unknown action"; exit 1
    ;;
esac
