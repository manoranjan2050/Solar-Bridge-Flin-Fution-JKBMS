#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║          SOLAR BRIDGE — All-in-One Installer for Raspberry Pi           ║
# ║   Installs: monitoring bridge + web dashboard + systemd services        ║
# ║   Supports: Flin Fution (Voltronic/Axpert) + JKBMS (dual, RS485)       ║
# ║   Features: history charts, daily energy, alerts, Telegram, automation  ║
# ║   Run as: bash install_all.sh   (from the copied source directory)      ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -e

# ── Colours ──────────────────────────────────────────────────────────────────
R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' B='\033[0;34m' C='\033[0;36m' N='\033[0m'
ok()   { echo -e "${G}✓ $*${N}"; }
info() { echo -e "${C}▶ $*${N}"; }
warn() { echo -e "${Y}⚠ $*${N}"; }
err()  { echo -e "${R}✗ $*${N}"; exit 1; }
hr()   { echo -e "${B}$(printf '─%.0s' {1..60})${N}"; }

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${Y}"
cat << 'BANNER'
  ____  ___  _      _   ____    ____  ____  ___ ____   ____ _____
 / ___|/ _ \| |    / \ |  _ \  | __ )|  _ \|_ _|  _ \ / ___| ____|
 \___ \ | | | |   / _ \| |_) | |  _ \| |_) || || | | | |  _|  _|
  ___) | |_| | |__/ ___ \  _ <  | |_) |  _ < | || |_| | |_| | |___
 |____/ \___/|____/_/   \_\_| \_\|____/|_| \_\___|____/ \____|_____|

      Flin Fution Inverter + JK BMS → MQTT → Home Assistant
BANNER
echo -e "${N}"
hr

# ── Checks ─────────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] && err "Do NOT run as root. Run as your normal user (e.g. pi or manoranjan)."
USER_NAME=$(whoami)
INSTALL_DIR=/opt/solar-bridge
DASH_DIR=$INSTALL_DIR/dashboard
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# All source files must sit next to this installer
for f in solar_bridge.py solar_db.py notifier.py automation.py \
         dashboard/app.py dashboard/templates/index.html dashboard/templates/login.html; do
    [[ -f "$SRC_DIR/$f" ]] || err "Missing source file: $f (run installer from the copied source dir)"
done

# ── Configuration wizard ──────────────────────────────────────────────────────
hr
info "Configuration — press Enter to accept defaults"
hr
echo ""

read -p "  Home Assistant / MQTT broker IP  [192.168.1.82]: " MQTT_HOST
MQTT_HOST=${MQTT_HOST:-192.168.1.82}

read -p "  MQTT port                         [1883]: " MQTT_PORT
MQTT_PORT=${MQTT_PORT:-1883}

read -p "  MQTT username                     [manoranjan]: " MQTT_USER
MQTT_USER=${MQTT_USER:-manoranjan}

read -s -p "  MQTT password: " MQTT_PASS
echo ""

read -p "  Inverter USB port                 [/dev/hidraw0]: " INV_PORT
INV_PORT=${INV_PORT:-/dev/hidraw0}

read -p "  BMS RS485 port                    [/dev/ttyUSB0]: " BMS_PORT
BMS_PORT=${BMS_PORT:-/dev/ttyUSB0}

read -p "  Number of BMS units               [2]: " BMS_COUNT
BMS_COUNT=${BMS_COUNT:-2}

read -p "  Cells per BMS pack                [16]: " BMS_CELLS
BMS_CELLS=${BMS_CELLS:-16}

read -p "  Dashboard web port                [8080]: " DASH_PORT
DASH_PORT=${DASH_PORT:-8080}

read -p "  Custom hostname (http://NAME.local) [solar]: " DASH_HOST
DASH_HOST=${DASH_HOST:-solar}

read -p "  Dashboard login password (blank = no login): " DASH_PASS

echo ""
hr
info "Installing with these settings:"
echo "   MQTT:      $MQTT_USER@$MQTT_HOST:$MQTT_PORT"
echo "   Inverter:  $INV_PORT"
echo "   BMS:       $BMS_PORT  (${BMS_COUNT}× packs, ${BMS_CELLS} cells each)"
echo "   Dashboard: http://$DASH_HOST.local:$DASH_PORT  (and http://$(hostname -I | awk '{print $1}'):$DASH_PORT)"
echo "   Login:     $([[ -n "$DASH_PASS" ]] && echo "enabled (user: admin)" || echo "disabled")"
hr
read -p "  Continue? [Y/n]: " CONFIRM
[[ "${CONFIRM,,}" == "n" ]] && echo "Aborted." && exit 0
echo ""

# ── Step 1: System packages ───────────────────────────────────────────────────
info "Step 1/7 — Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    libffi-dev usbutils network-manager avahi-daemon \
    --no-install-recommends -qq
ok "System packages installed"

# ── Step 2: Groups & udev ─────────────────────────────────────────────────────
info "Step 2/7 — Configuring USB device access..."
sudo usermod -aG dialout,plugdev "$USER_NAME"

sudo tee /etc/udev/rules.d/99-solar.rules > /dev/null << 'UDEV'
KERNEL=="hidraw*",  MODE="0666", GROUP="plugdev"
KERNEL=="ttyUSB*",  MODE="0666", GROUP="dialout"
KERNEL=="ttyACM*",  MODE="0666", GROUP="dialout"
UDEV

sudo udevadm control --reload-rules
sudo udevadm trigger
ok "USB device access configured"

# ── Step 3: Install directories ───────────────────────────────────────────────
info "Step 3/7 — Creating directory structure..."
sudo mkdir -p "$DASH_DIR/templates" "$DASH_DIR/static"
sudo chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
ok "Directories created at $INSTALL_DIR"

# ── Step 4: Copy application files ─────────────────────────────────────────────
info "Step 4/7 — Installing application files..."

# config.ini (preserve existing one if present, so we don't wipe live settings)
if [[ -f "$INSTALL_DIR/config.ini" ]]; then
    warn "Existing config.ini kept (delete it first to regenerate)"
else
cat > "$INSTALL_DIR/config.ini" << CONF
[mqtt]
host = $MQTT_HOST
port = $MQTT_PORT
username = $MQTT_USER
password = $MQTT_PASS
topic_prefix = solar

[inverter]
port = $INV_PORT
protocol = PI30
poll_interval = 10

[jkbms]
port = $BMS_PORT
baud = 115200
poll_interval = 10
cell_count = $BMS_CELLS

[dashboard]
username = admin
password = $DASH_PASS
hostname = $DASH_HOST

[alerts]
enabled = true
low_soc = 20
critical_soc = 10
high_battery_temp = 50
high_inverter_temp = 75
high_cell_diff = 0.1
notify_on_grid_loss = true
notify_on_fault = true
cooldown = 1800

[telegram]
enabled = false
token =
chat_id =

[email]
enabled = false
smtp_host = smtp.gmail.com
smtp_port = 587
username =
password =
from_addr =
to_addr =
CONF
    ok "config.ini written"
fi

# Bridge + add-on modules
cp "$SRC_DIR/solar_bridge.py" "$INSTALL_DIR/"
cp "$SRC_DIR/solar_db.py"     "$INSTALL_DIR/"
cp "$SRC_DIR/notifier.py"     "$INSTALL_DIR/"
cp "$SRC_DIR/automation.py"   "$INSTALL_DIR/"
[[ -f "$SRC_DIR/automation.json" ]] && cp "$SRC_DIR/automation.json" "$INSTALL_DIR/"
ok "Bridge + modules copied"

# Dashboard
cp "$SRC_DIR/dashboard/app.py"                    "$DASH_DIR/"
cp "$SRC_DIR/dashboard/templates/index.html"      "$DASH_DIR/templates/"
cp "$SRC_DIR/dashboard/templates/login.html"      "$DASH_DIR/templates/"
ok "Dashboard copied"

# ── Step 5: Python venv + packages ────────────────────────────────────────────
info "Step 5/7 — Installing Python packages..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install \
    pyserial \
    "paho-mqtt>=2.0.0" \
    "flask>=3.0.0" \
    "flask-socketio>=5.3.0" \
    "eventlet>=0.35.0" \
    "requests>=2.31.0" \
    -q
ok "Python packages installed"

# ── Step 6: Custom domain (mDNS / avahi) ──────────────────────────────────────
info "Step 6/7 — Configuring custom domain http://$DASH_HOST.local ..."
if [[ "$(hostname)" != "$DASH_HOST" ]]; then
    sudo hostnamectl set-hostname "$DASH_HOST" 2>/dev/null || \
        echo "$DASH_HOST" | sudo tee /etc/hostname > /dev/null
    # Keep /etc/hosts in sync so sudo doesn't complain
    sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$DASH_HOST/" /etc/hosts 2>/dev/null || \
        echo -e "127.0.1.1\t$DASH_HOST" | sudo tee -a /etc/hosts > /dev/null
fi
sudo systemctl enable avahi-daemon 2>/dev/null || true
sudo systemctl restart avahi-daemon 2>/dev/null || true
ok "Reachable at http://$DASH_HOST.local:$DASH_PORT after reboot"

# ── Step 7: Systemd services ──────────────────────────────────────────────────
info "Step 7/7 — Installing systemd services..."

sudo tee /etc/systemd/system/solar-bridge.service > /dev/null << SVCEOF
[Unit]
Description=Solar Bridge — Inverter + JKBMS to MQTT
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/solar_bridge.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal
SupplementaryGroups=dialout plugdev

[Install]
WantedBy=multi-user.target
SVCEOF

sudo tee /etc/systemd/system/solar-dashboard.service > /dev/null << DSVCEOF
[Unit]
Description=Solar Bridge Dashboard (Web UI on port $DASH_PORT)
After=network-online.target solar-bridge.service
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DASH_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $DASH_DIR/app.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=DASH_PORT=$DASH_PORT

[Install]
WantedBy=multi-user.target
DSVCEOF

# Allow the dashboard user to manage the services + read logs without a password
# (needed for "Save & Restart" buttons and the Logs / System pages)
sudo tee /etc/sudoers.d/solar-bridge > /dev/null << SUDOEOF
$USER_NAME ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart solar-bridge, /usr/bin/systemctl start solar-bridge, /usr/bin/systemctl stop solar-bridge, /usr/bin/systemctl status solar-bridge, /usr/bin/systemctl restart solar-dashboard, /usr/bin/systemctl start solar-dashboard, /usr/bin/systemctl stop solar-dashboard, /usr/bin/systemctl status solar-dashboard, /bin/systemctl restart solar-bridge, /bin/systemctl start solar-bridge, /bin/systemctl stop solar-bridge, /bin/systemctl status solar-bridge, /bin/systemctl restart solar-dashboard, /bin/systemctl start solar-dashboard, /bin/systemctl stop solar-dashboard, /bin/systemctl status solar-dashboard, /usr/bin/journalctl, /bin/journalctl
SUDOEOF
sudo chmod 0440 /etc/sudoers.d/solar-bridge
sudo visudo -cf /etc/sudoers.d/solar-bridge >/dev/null 2>&1 || { warn "sudoers syntax check failed — removing"; sudo rm -f /etc/sudoers.d/solar-bridge; }

sudo chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
sudo systemctl daemon-reload
sudo systemctl enable solar-bridge solar-dashboard
sudo systemctl restart solar-bridge
sleep 3
sudo systemctl restart solar-dashboard
ok "Services installed, enabled and started"

# ── Final summary ─────────────────────────────────────────────────────────────
PI_IP=$(hostname -I | awk '{print $1}')
hr
echo ""
echo -e "${G}╔══════════════════════════════════════════════╗${N}"
echo -e "${G}║       ✓  INSTALLATION COMPLETE!              ║${N}"
echo -e "${G}╚══════════════════════════════════════════════╝${N}"
echo ""
echo -e "  ${Y}📊 Dashboard:${N}   http://$DASH_HOST.local:${DASH_PORT}"
echo -e "                  http://${PI_IP}:${DASH_PORT}"
echo -e "  ${Y}🔌 MQTT:${N}       ${MQTT_USER}@${MQTT_HOST}:${MQTT_PORT}"
echo -e "  ${Y}⚡ Inverter:${N}   ${INV_PORT}"
echo -e "  ${Y}🔋 BMS:${N}        ${BMS_PORT}  (${BMS_COUNT}× packs)"
echo -e "  ${Y}🔔 Alerts:${N}     configure on the Notifications page"
echo ""
echo -e "  ${C}Service commands:${N}"
echo -e "    sudo systemctl status solar-bridge"
echo -e "    sudo systemctl status solar-dashboard"
echo -e "    sudo journalctl -u solar-bridge -f"
echo ""
echo -e "  ${Y}⚠  Log out / reboot for USB group + hostname changes to take effect${N}"
echo ""
hr

# Detect USB devices
echo -e "\n${C}USB devices connected right now:${N}"
lsusb
echo ""
ls -la /dev/hidraw* /dev/ttyUSB* 2>/dev/null && echo "" || echo -e "${Y}(No USB devices found — plug in inverter & BMS then check: ls /dev/hidraw* /dev/ttyUSB*)${N}\n"
