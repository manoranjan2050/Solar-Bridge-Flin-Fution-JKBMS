# ☀️ Solar Bridge

**Self-hosted replacement for Solar Assistant.** Runs on a Raspberry Pi, reads a
Voltronic/Axpert-style inverter (Flin Fution) and JK BMS battery packs over USB,
and gives you:

- 📡 **Full MQTT + Home Assistant auto-discovery** (sensors *and* controls)
- 📊 **A built-in web dashboard** with live power-flow, history charts and settings
- 🔔 **Alerts** (low SOC, high temp, grid loss, inverter faults) over **Telegram / e-mail / Home Assistant**
- 🤖 **Automation rules** (e.g. "switch to grid charging when SOC < 30%")
- 📈 **History database** (SQLite) with daily energy that resets at midnight
- 💬 **Telegram bot** — query your system with `/status`, `/pv`, `/battery`, `/today`

No cloud, no subscription. Everything runs locally on the Pi.

---

## Table of Contents
- [Hardware](#hardware)
- [Quick Install](#quick-install)
- [What You Get](#what-you-get)
- [The Dashboard](#the-dashboard)
- [Home Assistant](#home-assistant)
- [Telegram Bot](#telegram-bot)
- [Automation](#automation)
- [Alerts & Notifications](#alerts--notifications)
- [Configuration Reference](#configuration-reference)
- [Architecture](#architecture)
- [Deploying Changes](#deploying-changes)
- [Troubleshooting](#troubleshooting)
- [Protocol Notes](#protocol-notes)
- [Known Issues](#known-issues)

---

## Hardware

| Device | Connection | Port | USB ID |
|---|---|---|---|
| Inverter — Flin Fution (Voltronic/Axpert clone, 5kVA hybrid) | USB HID cable | `/dev/hidraw0` | `0665:5161` |
| Battery BMS — JK BMS (new protocol, 16S LiFePO4) ×2 in parallel | CH340 USB-RS485 | `/dev/ttyUSB0` | `1a86:7523` |
| Raspberry Pi 4 (Pi OS Lite, 64-bit) | LAN | — | — |

> Works with most Voltronic / Axpert / MPP Solar / EASUN clones on PI30, and any
> JK BMS using the `55 AA EB 90` broadcast protocol. Other devices may need
> tweaks to the field offsets.

---

## Quick Install

On a **fresh Raspberry Pi OS Lite**:

```bash
# 1. Copy the project to the Pi (from your PC):
scp -r soalrpi/ pi@<PI_IP>:~/

# 2. SSH in and run the installer:
ssh pi@<PI_IP>
cd soalrpi
bash install_all.sh
```

The installer asks for MQTT host/credentials, device ports, dashboard port,
custom hostname and an optional dashboard password — then it:

- installs system + Python dependencies (incl. `avahi` for the custom domain),
- sets up USB permissions (udev) and a passwordless `sudo` rule for service restarts,
- copies all source files into `/opt/solar-bridge/`,
- creates and enables two systemd services (auto-start on boot),
- configures `http://<hostname>.local` access.

When it finishes:

```
📊 Dashboard:  http://solar.local:8080   (or http://<PI_IP>:8080)
```

> **Re-running the installer keeps your existing `config.ini`** so you won't lose
> MQTT credentials. Delete `/opt/solar-bridge/config.ini` first if you want a clean one.

---

## What You Get

### Services (auto-start on boot)

```bash
sudo systemctl status solar-bridge      # data collector (inverter + BMS → MQTT + DB)
sudo systemctl status solar-dashboard   # web UI on port 8080
sudo journalctl -u solar-bridge -f      # live logs
```

### Sensors published (MQTT + HA)

**Inverter:** PV voltage/current/power, AC output (V/Hz/W/VA/%), grid (V/Hz/W),
battery (V/A/W/%), bus voltage, heatsink temp, device mode, **fault/warning status**,
lifetime energy totals **and today's energy** (PV / load / grid / battery in-out),
plus all rated settings (float/bulk/cutoff voltages, charge currents, priorities).

**Battery:** per-pack and combined "Battery Bank" — voltage, SOC, 16 cell voltages
each, min/max/avg/diff, temps (T1/T2/MOS), cycle count, remaining/design capacity, SOH.

---

## The Dashboard

Open `http://solar.local:8080` (or `http://<PI_IP>:8080`).

| Page | What it shows |
|---|---|
| **Overview** | Power-flow diagram (Solar→Load, Battery↕, Grid), stat cards, energy totals |
| **Solar PV** | PV voltage/current/power, AC output, grid details |
| **Battery** | SOC gauge, BMS 1 + BMS 2 details, 32 cell-voltage bars |
| **Inverter** | All live readings + current settings |
| **History** | 6h/24h/3d/7d charts of power, SOC, temperatures + daily-energy bars + today's totals |
| **Alerts** | Alert history with severity; "Send Test Alert" button |
| **Inverter Settings** | Change priorities, voltages and currents — sent straight to the inverter |
| **Automation** | Build threshold/time rules that change inverter settings automatically |
| **Notifications** | Configure Telegram, e-mail and alert thresholds |
| **Network & MQTT** | WiFi scan/connect, MQTT broker config |
| **Bluetooth** | Scan for JK BMS over BLE |
| **System** | CPU temp, RAM, disk, uptime, service restart buttons |
| **Logs** | Live `solar-bridge` log output |

### Optional login

Set a password on the **Notifications**/config (or `config.ini` → `[dashboard] password`)
to require a login before anyone can view data or change inverter settings.
Leave it blank to disable login (open on the LAN).

---

## Home Assistant

The bridge publishes MQTT discovery automatically — no YAML needed. In HA go to
**Settings → Devices & Services → MQTT** and you'll see:

- **Flin Fution Inverter** — all sensors + control entities
- **JK BMS 1 / JK BMS 2 / Battery Bank** — all battery sensors

### Controls exposed to HA

| Control | Type | Inverter command |
|---|---|---|
| Output Source Priority | Select (Grid / Solar / SBU) | `POPCD` |
| Charger Source Priority | Select (Grid / Solar / Solar+Grid / Solar only) | `PPCP` |
| Max Charge Current | Number | `MUCHGC` |
| Max Grid Charge Current | Number | `MCHGC` |
| Battery Float Voltage | Number | `PBFT` |
| Battery Bulk Voltage | Number | `PBCV` |
| Battery Shutdown Voltage | Number | `PSDV` |
| Battery Recharge Voltage | Number | `PBDV` |

Alerts are also published to the MQTT topic `solar/alert` (JSON: level, message, ts)
so you can trigger HA automations / notifications from them.

---

## Telegram Bot

1. Message **@BotFather** → `/newbot` → copy the **token**.
2. Message **@userinfobot** → copy your numeric **chat id**.
3. Dashboard → **Notifications** → enable Telegram, paste token + chat id → **Save**.

Then message your bot:

| Command | Reply |
|---|---|
| `/status` | PV, load, grid, battery, SOC, mode |
| `/pv` | Solar production detail |
| `/battery` | SOC, voltage, current, temp, cell imbalance |
| `/today` | Today's energy totals |

Alerts are also pushed to this chat automatically. The bot only responds to your
configured chat id.

---

## Automation

Dashboard → **Automation** → *Add Rule*. Two rule types:

- **Threshold** — when a sensor crosses a value, set an inverter control.
  *Example:* `Battery SOC < 30 → Charger Priority = Solar+Grid`
- **Time** — once a day at `HH:MM`, set an inverter control.
  *Example:* `at 07:00 → Charger Priority = Solar only`

Rules run on the bridge every poll cycle (threshold rules have a configurable
cooldown). Saving rules restarts the bridge so they take effect. Rules are stored
in `/opt/solar-bridge/automation.json`.

---

## Alerts & Notifications

Edge-triggered alerts (fire once when crossed, recover when normal, with a cooldown
to prevent spam):

| Alert | Default trigger |
|---|---|
| Low battery (warning) | SOC ≤ 20% |
| Critical battery | SOC ≤ 10% |
| High battery temperature | ≥ 50 °C |
| High inverter temperature | ≥ 75 °C |
| Cell imbalance | diff ≥ 0.1 V |
| Grid lost / restored | AC input voltage < 50 V |
| Inverter fault | real QPIWS fault flags (warnings like "Line fail" are shown but don't alert) |

All thresholds are editable on the **Notifications** page. Alerts go to every
enabled channel (Telegram, e-mail, Home Assistant via MQTT) and are stored in the
history DB for the Alerts page.

> **Note for off-grid / solar-primary setups:** if grid is normally absent during
> the day, turn off *Grid loss/restore* on the Notifications page to avoid daily
> warnings.

---

## Configuration Reference

`/opt/solar-bridge/config.ini`:

```ini
[mqtt]
host = 192.168.1.82        # your MQTT broker / Home Assistant IP
port = 1883
username = youruser
password = yourpass
topic_prefix = solar

[inverter]
port = /dev/hidraw0        # USB HID; use /dev/ttyUSB0 for serial cables
protocol = PI30
poll_interval = 10

[jkbms]
port = /dev/ttyUSB0
baud = 115200
poll_interval = 10
cell_count = 16

[dashboard]
username = admin
password =                 # blank = no login
hostname = solar           # → http://solar.local:8080

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
password =                 # use an app password, not your login password
from_addr =
to_addr =                  # comma-separated recipients
```

Most of these can be edited from the dashboard instead of by hand.

---

## Architecture

```
Raspberry Pi
└── /opt/solar-bridge/
    ├── solar_bridge.py        # main collector: polls inverter (QPIGS/QPIRI/QMOD/QPIWS)
    │                          #   + BMS, publishes MQTT, accumulates energy,
    │                          #   runs alerts + automation
    ├── solar_db.py            # SQLite history (readings, daily_energy, alerts)
    ├── notifier.py            # Telegram / e-mail / HA alerts + Telegram command bot
    ├── automation.py          # rule engine (threshold + time)
    ├── config.ini             # all settings
    ├── energy.json            # persistent kWh totals + daily baseline
    ├── solar_bridge.db        # SQLite history database
    ├── automation.json        # automation rules
    ├── dashboard/
    │   ├── app.py             # Flask + Socket.IO backend
    │   └── templates/         # index.html, login.html
    └── venv/                  # pyserial, paho-mqtt, flask, flask-socketio,
                               #   eventlet, requests

systemd:
  solar-bridge.service     → solar_bridge.py
  solar-dashboard.service  → dashboard/app.py (port 8080)
```

Poll cadence: QPIGS 10s · QMOD 15s · QPIWS 30s · QPIRI 60s · BMS 10s ·
history snapshot 60s · daily-energy rollover at midnight.

---

## Deploying Changes

Edit files on your PC, then push to the Pi.

**Using the bundled deploy script** (configure credentials first — see below):

```bash
python deploy.py            # uploads bridge + modules, installs deps, restarts
```

**Manually:**

```bash
scp solar_bridge.py solar_db.py notifier.py automation.py \
    youruser@<PI_IP>:/opt/solar-bridge/
scp dashboard/app.py youruser@<PI_IP>:/opt/solar-bridge/dashboard/
scp dashboard/templates/*.html youruser@<PI_IP>:/opt/solar-bridge/dashboard/templates/
ssh youruser@<PI_IP> "sudo systemctl restart solar-bridge solar-dashboard"
```

> **Credentials:** `deploy.py` reads the Pi host/user/password from environment
> variables (`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PASS`) or a **git-ignored**
> `deploy_secrets.py`. Copy `deploy_secrets.example.py` → `deploy_secrets.py` and
> fill in your details. Never commit real credentials.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No inverter data / NAK | Check `/dev/hidraw0` exists (`ls /dev/hidraw*`); try unplug/replug; verify protocol PI30 |
| BMS "no type-02 frames" occasionally | Normal — it retries; the BMS broadcasts intermittently |
| Dashboard "Save & Restart" does nothing | The installer adds a sudoers rule; if you skipped it, run the installer again |
| Telegram not sending | Enable it on Notifications, double-check token + chat id, ensure Pi has internet |
| `http://solar.local` not resolving | Reboot the Pi (hostname change), ensure `avahi-daemon` is running; use the IP meanwhile |
| Permission errors on USB | `sudo usermod -aG dialout,plugdev <user>` then re-login/reboot |

Useful commands:

```bash
sudo systemctl restart solar-bridge solar-dashboard
sudo journalctl -u solar-bridge -f
cat /opt/solar-bridge/energy.json
ls -la /dev/hidraw* /dev/ttyUSB*
```

---

## Protocol Notes

**Inverter (Voltronic PI30 over USB HID):** 9-byte writes (`0x00` + 8 data),
8 raw bytes per read packet (no count prefix on this `0665:5161` device),
Voltronic table CRC-16, responses start with `(`, set commands reply `(ACK`/`(NAK`.

**JK BMS (new protocol `55 AA EB 90`):** broadcasts passively every ~1s; type-`0x02`
frame carries cell info. Key offsets: cells `+6` (16×uint16 LE mV), MOS temp `+144`,
pack voltage `+150`, remaining capacity `+154`, temps `+162/+164`, SOC `+182`,
design capacity `+186`, cycles `+190`. Two packs are told apart by frame byte `+5`
(`0x00` = BMS1, `0x05` = BMS2).

---

## Known Issues

- **BMS current reads 0 A** — the current field offset isn't confirmed for this
  firmware variant. Power/energy are derived from the inverter instead.
- **BMS SOC can differ between packs (e.g. 46% vs 36%)** — packs report independently;
  the Bank value averages them.
- **Remaining capacity (Ah) occasionally spikes** — frame offset can shift between
  frame variants; capped for sanity.

---

## License

Personal project — use at your own risk. Always double-check inverter setting
changes against your battery/installer specifications.
