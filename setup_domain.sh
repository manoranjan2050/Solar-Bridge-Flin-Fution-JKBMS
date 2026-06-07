#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║   Solar Bridge — Custom Domain Setup (nginx reverse proxy + HTTPS)        ║
# ║   Makes the dashboard reachable at e.g. https://solar.manoranjan.dev      ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# BEFORE running this, point your domain at this Pi:
#   • LAN only      → add a DNS A record (or router/hosts entry) for
#                     solar.yourdomain.com → <Pi LAN IP>
#   • Internet/HTTPS→ add a public DNS A record for solar.yourdomain.com →
#                     your public IP, and forward ports 80 + 443 on your router
#                     to this Pi. (Required for a Let's Encrypt certificate.)
#
set -e
R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' N='\033[0m'
ok(){ echo -e "${G}✓ $*${N}"; }; info(){ echo -e "${C}▶ $*${N}"; }; err(){ echo -e "${R}✗ $*${N}"; exit 1; }

[[ $EUID -eq 0 ]] && err "Run as your normal user (not root); it will sudo when needed."

read -p "  Full domain for the dashboard [solar.manoranjan.dev]: " DOMAIN
DOMAIN=${DOMAIN:-solar.manoranjan.dev}
read -p "  Dashboard port [8080]: " PORT
PORT=${PORT:-8080}
read -p "  Enable HTTPS with Let's Encrypt? (needs public DNS + ports 80/443) [y/N]: " HTTPS
read -p "  E-mail for Let's Encrypt (for renewal notices): " LE_EMAIL

info "Installing nginx..."
sudo apt-get update -qq
sudo apt-get install -y nginx --no-install-recommends -qq
ok "nginx installed"

info "Writing reverse-proxy config for $DOMAIN → 127.0.0.1:$PORT"
sudo tee /etc/nginx/sites-available/solar-bridge > /dev/null << NGINX
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket (Socket.IO live updates)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/solar-bridge /etc/nginx/sites-enabled/solar-bridge
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t || err "nginx config test failed"
sudo systemctl restart nginx
sudo systemctl enable nginx >/dev/null 2>&1 || true
ok "nginx serving http://$DOMAIN"

if [[ "${HTTPS,,}" == "y" ]]; then
    info "Installing certbot + requesting certificate..."
    sudo apt-get install -y certbot python3-certbot-nginx --no-install-recommends -qq
    if [[ -n "$LE_EMAIL" ]]; then
        sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LE_EMAIL" --redirect \
            && ok "HTTPS enabled — https://$DOMAIN" \
            || echo -e "${Y}⚠ certbot failed — check DNS points here and ports 80/443 are open${N}"
    else
        echo -e "${Y}⚠ No e-mail given; run manually: sudo certbot --nginx -d $DOMAIN${N}"
    fi
fi

echo ""
echo -e "${G}Done.${N}  Dashboard now at:  http://$DOMAIN  ${HTTPS:+(and https once certbot succeeds)}"
echo -e "${Y}Reminder:${N} a DNS A record for $DOMAIN must point to this Pi"
echo -e "         (LAN IP for local use, or public IP + port-forward 80/443 for internet/HTTPS)."
