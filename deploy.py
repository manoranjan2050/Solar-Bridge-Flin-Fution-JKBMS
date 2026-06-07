#!/usr/bin/env python3
"""Deploy solar bridge to Raspberry Pi via SSH."""

import os
import sys
import time
import paramiko
from pathlib import Path

HOST = "192.168.1.32"
USER = "manoranjan"
PASS = "M@$ter@2050"
REMOTE_HOME = f"/home/{USER}"
INSTALL_DIR = "/opt/solar-bridge"

FILES = ["solar_bridge.py", "solar_db.py", "notifier.py", "automation.py",
         "config.ini", "setup.sh", "solar-bridge.service", "detect_devices.sh"]

BASE = Path(__file__).parent


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=15)
    return client


def run(ssh, cmd, sudo=False, desc=""):
    if sudo:
        cmd = f"echo '{PASS}' | sudo -S sh -c '{cmd}'"
    if desc:
        print(f"  {desc}...")
    stdin, stdout, stderr = ssh.exec_command(cmd, get_pty=True)
    out = stdout.read().decode(errors="ignore")
    err = stderr.read().decode(errors="ignore")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def run_verbose(ssh, cmd, sudo=False, desc=""):
    rc, out, err = run(ssh, cmd, sudo=sudo, desc=desc)
    combined = (out + err).strip()
    if combined:
        for line in combined.splitlines():
            line = line.strip()
            if line and not line.startswith("[sudo]") and "password" not in line.lower():
                print(f"    {line}")
    return rc


def upload(ssh, local_file, remote_path):
    sftp = ssh.open_sftp()
    sftp.put(str(BASE / local_file), remote_path)
    sftp.close()


def main():
    print(f"\n{'='*55}")
    print(f"  Solar Bridge — Deploying to {HOST}")
    print(f"{'='*55}\n")

    print("[+] Connecting to Pi...")
    try:
        ssh = connect()
        print(f"    Connected as {USER}@{HOST}\n")
    except Exception as e:
        print(f"    FAILED: {e}")
        sys.exit(1)

    # ── Step 1: System info ──────────────────────────────────────────────────
    print("[1] System check")
    rc, out, _ = run(ssh, "uname -a && cat /etc/os-release | head -3")
    for line in out.strip().splitlines():
        print(f"    {line}")
    print()

    # ── Step 2: Update & install packages ───────────────────────────────────
    print("[2] Updating package list...")
    run_verbose(ssh, "apt-get update -qq", sudo=True, desc="apt update")

    print("    Installing python3, venv, pip, usbutils...")
    rc = run_verbose(ssh,
        "DEBIAN_FRONTEND=noninteractive apt-get install -y "
        "python3 python3-pip python3-venv python3-dev "
        "libffi-dev usbutils --no-install-recommends -qq",
        sudo=True
    )
    print(f"    {'OK' if rc == 0 else 'WARN (check manually)'}\n")

    # ── Step 3: Groups ───────────────────────────────────────────────────────
    print("[3] Adding user to dialout + plugdev groups")
    run_verbose(ssh, f"usermod -aG dialout,plugdev {USER}", sudo=True)
    print("    OK\n")

    # ── Step 4: udev rules ───────────────────────────────────────────────────
    print("[4] Installing udev rules")
    udev_rules = r"""
KERNEL=="hidraw*", ATTRS{idVendor}=="0665", MODE="0666", GROUP="plugdev", SYMLINK+="solar-inverter"
KERNEL=="hidraw*", MODE="0666", GROUP="plugdev"
KERNEL=="ttyUSB*", MODE="0666", GROUP="dialout"
KERNEL=="ttyUSB*", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="jkbms"
KERNEL=="ttyUSB*", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="jkbms"
KERNEL=="ttyUSB*", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="jkbms"
""".strip()

    cmd = f"tee /etc/udev/rules.d/99-solar.rules > /dev/null << 'ENDRULES'\n{udev_rules}\nENDRULES"
    run(ssh, cmd, sudo=True)
    run(ssh, "udevadm control --reload-rules && udevadm trigger", sudo=True)
    print("    OK\n")

    # ── Step 5: Create install dir ───────────────────────────────────────────
    print("[5] Creating /opt/solar-bridge")
    run(ssh, f"mkdir -p {INSTALL_DIR} && chown {USER}:{USER} {INSTALL_DIR}", sudo=True)
    print("    OK\n")

    # ── Step 6: Upload files ─────────────────────────────────────────────────
    print("[6] Uploading files")
    sftp = ssh.open_sftp()
    for f in FILES:
        local = BASE / f
        if not local.exists():
            print(f"    SKIP {f} (not found locally)")
            continue
        remote = f"{INSTALL_DIR}/{f}"
        sftp.put(str(local), remote)
        print(f"    OK {f}")
    sftp.close()
    run(ssh, f"chown -R {USER}:{USER} {INSTALL_DIR} && chmod +x {INSTALL_DIR}/setup.sh {INSTALL_DIR}/detect_devices.sh", sudo=True)
    print()

    # ── Step 7: Python venv ──────────────────────────────────────────────────
    print("[7] Creating Python virtual environment")
    run_verbose(ssh, f"python3 -m venv {INSTALL_DIR}/venv", desc="venv create")
    run_verbose(ssh, f"{INSTALL_DIR}/venv/bin/pip install --upgrade pip -q", desc="pip upgrade")
    print("    Installing pyserial + paho-mqtt + requests...")
    rc = run_verbose(ssh, f"{INSTALL_DIR}/venv/bin/pip install pyserial paho-mqtt requests -q")
    print(f"    {'OK' if rc == 0 else 'FAILED'}\n")

    # ── Step 8: Systemd service ──────────────────────────────────────────────
    print("[8] Installing systemd service")
    run(ssh, f"sed -i 's/User=pi/User={USER}/' {INSTALL_DIR}/solar-bridge.service", sudo=True)
    run(ssh, f"cp {INSTALL_DIR}/solar-bridge.service /etc/systemd/system/", sudo=True)
    run(ssh, "systemctl daemon-reload && systemctl enable solar-bridge", sudo=True)
    print("    Service installed + enabled on boot\n")

    # ── Step 9: Detect USB devices ───────────────────────────────────────────
    print("[9] Detecting USB devices")
    rc, out, _ = run(ssh, "lsusb")
    print("    lsusb output:")
    for line in out.strip().splitlines():
        print(f"      {line}")
    print()

    rc, out, _ = run(ssh, "ls -la /dev/hidraw* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo 'none found'")
    print("    Device nodes:")
    for line in out.strip().splitlines():
        print(f"      {line}")
    print()

    # ── Step 10: Show current config ─────────────────────────────────────────
    print("[10] Current config.ini on Pi:")
    rc, out, _ = run(ssh, f"cat {INSTALL_DIR}/config.ini")
    for line in out.splitlines():
        print(f"    {line}")
    print()

    print("="*55)
    print("  DEPLOYMENT COMPLETE")
    print("="*55)
    print()
    print("  NEXT — configure your MQTT broker:")
    print(f"    ssh {USER}@{HOST}")
    print(f"    nano {INSTALL_DIR}/config.ini")
    print()
    print("  Then start the service:")
    print(f"    sudo systemctl start solar-bridge")
    print(f"    sudo journalctl -u solar-bridge -f")
    print()

    ssh.close()


if __name__ == "__main__":
    main()
