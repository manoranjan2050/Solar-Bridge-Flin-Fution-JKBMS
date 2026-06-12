#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║   Solar Bridge — Cloudflare Tunnel setup (public custom domain + HTTPS)  ║
# ║                                                                          ║
# ║   Makes the dashboard reachable at https://solar.yourdomain.com         ║
# ║   No port forwarding. Auto HTTPS. Add Cloudflare Access to protect it.   ║
# ║                                                                          ║
# ║   Run on the Pi:  bash setup_cloudflare.sh solar.yourdomain.com         ║
# ║   Prereq: the domain's DNS must be on Cloudflare (free plan is fine).    ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -e

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' N='\033[0m'
ok()   { echo -e "${G}✓ $*${N}"; }
info() { echo -e "${C}▶ $*${N}"; }
warn() { echo -e "${Y}⚠ $*${N}"; }
err()  { echo -e "${R}✗ $*${N}"; exit 1; }

HOSTNAME_FQDN="${1:-}"
DASH_PORT="${2:-8080}"
TUNNEL_NAME="${TUNNEL_NAME:-solar-bridge}"

[[ -z "$HOSTNAME_FQDN" ]] && err "Usage: bash setup_cloudflare.sh solar.yourdomain.com [dash_port]"
command -v cloudflared >/dev/null || err "cloudflared not installed"

echo ""
info "Setting up Cloudflare Tunnel for: https://$HOSTNAME_FQDN  →  localhost:$DASH_PORT"
echo ""

# ── 1. Authenticate (opens a browser link) ────────────────────────────────────
CERT="$HOME/.cloudflared/cert.pem"
if [[ ! -f "$CERT" ]]; then
    info "Step 1 — Authorise this Pi with your Cloudflare account."
    echo -e "   ${Y}A URL will appear below. Open it in ANY browser, log in,${N}"
    echo -e "   ${Y}and pick the domain (zone) for $HOSTNAME_FQDN.${N}"
    echo ""
    cloudflared tunnel login
    [[ -f "$CERT" ]] || err "Login did not complete (no cert.pem). Re-run the script."
    ok "Authorised"
else
    ok "Already authorised (cert.pem present)"
fi

# ── 2. Create the tunnel (idempotent) ─────────────────────────────────────────
if cloudflared tunnel list 2>/dev/null | grep -q "[[:space:]]$TUNNEL_NAME[[:space:]]"; then
    ok "Tunnel '$TUNNEL_NAME' already exists"
else
    info "Step 2 — Creating tunnel '$TUNNEL_NAME'..."
    cloudflared tunnel create "$TUNNEL_NAME"
    ok "Tunnel created"
fi

TUNNEL_ID="$(cloudflared tunnel list 2>/dev/null | awk -v n="$TUNNEL_NAME" '$2==n{print $1}')"
[[ -z "$TUNNEL_ID" ]] && err "Could not determine tunnel ID"
info "Tunnel ID: $TUNNEL_ID"

# ── 3. Write the config (maps the hostname → local dashboard) ─────────────────
info "Step 3 — Writing /etc/cloudflared/config.yml ..."
sudo mkdir -p /etc/cloudflared
sudo cp "$HOME/.cloudflared/$TUNNEL_ID.json" /etc/cloudflared/ 2>/dev/null || true
sudo tee /etc/cloudflared/config.yml > /dev/null << YAML
tunnel: $TUNNEL_ID
credentials-file: /etc/cloudflared/$TUNNEL_ID.json

ingress:
  - hostname: $HOSTNAME_FQDN
    service: http://localhost:$DASH_PORT
    originRequest:
      noTLSVerify: true
  - service: http_status:404
YAML
ok "Config written"

# ── 4. Route the DNS record (creates the CNAME in Cloudflare) ─────────────────
info "Step 4 — Pointing $HOSTNAME_FQDN at the tunnel (DNS)..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME_FQDN" || \
    warn "DNS route may already exist (that's fine)"
ok "DNS routed"

# ── 5. Install + start the service ────────────────────────────────────────────
info "Step 5 — Installing cloudflared as a boot service..."
sudo cloudflared service install 2>/dev/null || true
sudo systemctl enable cloudflared
sudo systemctl restart cloudflared
sleep 4
sudo systemctl is-active cloudflared >/dev/null && ok "cloudflared service running" || \
    warn "service not active — check: sudo journalctl -u cloudflared -n30"

echo ""
echo -e "${G}╔══════════════════════════════════════════════╗${N}"
echo -e "${G}║   ✓  TUNNEL LIVE                             ║${N}"
echo -e "${G}╚══════════════════════════════════════════════╝${N}"
echo ""
echo -e "  Your dashboard:  ${Y}https://$HOSTNAME_FQDN${N}"
echo -e "  (DNS may take 1–2 minutes the first time)"
echo ""
echo -e "  ${R}⚠ IMPORTANT — this URL is now PUBLIC.${N}"
echo -e "  Protect the inverter controls with Cloudflare Access (free):"
echo -e "  ${C}see CLOUDFLARE_TUNNEL.md → 'Step 6: Lock it down'.${N}"
echo ""
