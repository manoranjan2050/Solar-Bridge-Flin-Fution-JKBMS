#!/bin/bash
# Solar Bridge Setup Script for Raspberry Pi OS Lite
# Run as: bash setup.sh
set -e

INSTALL_DIR=/opt/solar-bridge
SERVICE_USER=pi   # change if your user is different

echo "=== Solar Bridge Installer ==="
echo ""

# ── System packages ──────────────────────────────────────────────────────────
echo "[1/6] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    python3-dev \
    libffi-dev libssl-dev \
    usbutils \
    --no-install-recommends

# ── USB/serial group membership ───────────────────────────────────────────────
echo "[2/6] Adding $SERVICE_USER to dialout and plugdev groups..."
sudo usermod -aG dialout "$SERVICE_USER"
sudo usermod -aG plugdev "$SERVICE_USER"

# ── udev rules ───────────────────────────────────────────────────────────────
echo "[3/6] Installing udev rules for inverter and JKBMS..."
sudo tee /etc/udev/rules.d/99-solar.rules > /dev/null << 'UDEV'
# Voltronic/Axpert USB HID inverter (USB VID:PID varies; make all hidraw accessible)
KERNEL=="hidraw*", ATTRS{idVendor}=="0665", MODE="0666", GROUP="plugdev", SYMLINK+="solar-inverter"

# Generic fallback for all hidraw devices (safe on a dedicated Pi)
KERNEL=="hidraw*", MODE="0666", GROUP="plugdev"

# USB-RS485 adapter for JKBMS (CH340/CP2102/FTDI)
KERNEL=="ttyUSB*", MODE="0666", GROUP="dialout", SYMLINK+="jkbms"

# CP2102 specifically (common RS485 adapter)
KERNEL=="ttyUSB*", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="jkbms", MODE="0666"

# CH340 (another common adapter)
KERNEL=="ttyUSB*", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="jkbms", MODE="0666"
UDEV

sudo udevadm control --reload-rules
sudo udevadm trigger

# ── Install application ───────────────────────────────────────────────────────
echo "[4/6] Installing Solar Bridge to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp solar_bridge.py "$INSTALL_DIR/"
sudo cp config.ini "$INSTALL_DIR/"
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Python venv + deps
sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install \
    pyserial \
    paho-mqtt \
    -q

echo "[5/6] Installing systemd service..."
sudo cp solar-bridge.service /etc/systemd/system/
sudo sed -i "s/User=pi/User=$SERVICE_USER/" /etc/systemd/system/solar-bridge.service
sudo systemctl daemon-reload
sudo systemctl enable solar-bridge.service

echo ""
echo "=== Installation complete! ==="
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Edit the config file with your MQTT broker details:"
echo "   sudo nano $INSTALL_DIR/config.ini"
echo ""
echo "   Change: host = 192.168.1.100  ← your HA IP address"
echo "   Change: port = 1883           ← your MQTT port"
echo "   Add username/password if your broker requires auth"
echo ""
echo "2. Check which device your inverter is on:"
echo "   ls -la /dev/hidraw* /dev/ttyUSB* 2>/dev/null"
echo "   Update 'port =' in config.ini accordingly"
echo ""
echo "3. Check which device your JKBMS RS485 adapter is on:"
echo "   ls -la /dev/ttyUSB*"
echo "   Update [jkbms] port = in config.ini"
echo ""
echo "4. Start the service:"
echo "   sudo systemctl start solar-bridge"
echo ""
echo "5. Watch the logs:"
echo "   sudo journalctl -u solar-bridge -f"
echo ""
echo "6. In Home Assistant → Settings → Devices & Services → MQTT"
echo "   your sensors will appear automatically."
echo ""

# ── Detect connected devices right now ───────────────────────────────────────
echo "=== USB devices currently connected ==="
lsusb
echo ""
echo "=== Serial/HID device nodes ==="
ls -la /dev/hidraw* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo "(none found yet — plug in devices and re-run: ls /dev/hidraw* /dev/ttyUSB*)"
echo ""
