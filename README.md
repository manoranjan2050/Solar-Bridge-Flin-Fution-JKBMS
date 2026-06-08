<div align="center">

# ☀️ Solar Bridge

**A self-hosted, open-source replacement for Solar Assistant.**
Monitor & control a Voltronic/Axpert-style inverter + JK BMS from a beautiful web dashboard,
Home Assistant, and Telegram — all running locally on a Raspberry Pi.

<sub>Built by <a href="https://github.com/manoranjan2050">Manoranjan</a> · <a href="https://manoranjan.dev">manoranjan.dev</a></sub>

<br>

![Solar Bridge Dashboard](image/main_page.jpg)

<sub>Live overview with animated cross power-flow, energy totals & generation forecast</sub>

</div>

---

## ✨ Features

- 📊 **Web dashboard** — animated **cross power-flow** (solar/grid/inverter/load/battery), animated battery cells & SOC gauge, animated solar hero
- 📱 **Installable PWA** — add to your phone's home screen, runs full-screen like a native app
- 📈 **History & Details** — per-day/month/year energy, totals, self-sufficiency, "today vs typical" solar forecast
- 💰 **Cost & savings tracking** — set your ₹/kWh tariff → daily/monthly grid cost, solar savings, export earnings, net benefit
- 📤 **CSV export** — download day/month/year energy + cost data for spreadsheets
- 🏠 **Home Assistant** — full MQTT auto-discovery of every sensor **and** inverter controls
- 💬 **Telegram bot** — status (`/info`, `/status`, `/pv`, `/battery`, `/today`), **tappable control panel** (`/menu`) or text control (`/output`, `/charger`, `/maxcharge`, …), and an optional **daily summary**
- 🔔 **Smart alerts** — low/critical SOC, high temps, overload, over-voltage, cell imbalance, grid loss, faults, battery-full → Telegram / e-mail / Home Assistant
- 🤖 **Automation rules** — "if SOC < 30% → charge from grid", time-based schedules
- 🔐 **Optional login** — password-protect the dashboard (change credentials from the System page)
- 🌐 **Custom domain** — `http://solar.local` (mDNS) out of the box, or `https://solar.yourdomain.com` via the bundled nginx setup
- 💾 **Backup & restore** — one-click settings backup; restore on a new Pi
- ⚙️ **Works even if MQTT is down** — dashboard data **and** inverter control use a local file bridge, so a broker outage never blanks your dashboard
- 🛡️ **Appliance-ready** — auto-purges old history, caps log size, auto-starts & self-restarts on boot

---

## 📸 Screenshots

<table>
  <tr>
    <td width="50%" align="center">
      <img src="image/solar_pv.jpg" alt="Solar PV page"><br>
      <b>☀️ Solar PV</b><br><sub>Animated sun hero, today's generation curve, MPPT/AC/Grid detail</sub>
    </td>
    <td width="50%" align="center">
      <img src="image/Battery_page.jpg" alt="Battery page"><br>
      <b>🔋 Battery</b><br><sub>Animated SOC gauge + 32 live cell bars, dual-BMS detail</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="image/inverter_page.jpg" alt="Inverter page"><br>
      <b>⚡ Inverter</b><br><sub>All live readings + current settings</sub>
    </td>
    <td width="50%" align="center">
      <img src="image/History_page.jpg" alt="History page"><br>
      <b>📈 History</b><br><sub>6h–7d charts of power, SOC & temperatures</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="image/details_page.jpg" alt="Details page"><br>
      <b>📋 Details &amp; Cost</b><br><sub>Day/month/year energy, savings (₹/kWh) & CSV export</sub>
    </td>
    <td width="50%" align="center">
      <img src="image/alert_system.jpg" alt="Alerts page"><br>
      <b>🔔 Alerts</b><br><sub>Severity-coded alert history + test alert</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="image/log_page.jpg" alt="Logs page"><br>
      <b>📜 Live Logs</b><br><sub>Real-time bridge log (sensor reads, command ACKs, alerts)</sub>
    </td>
    <td width="50%" align="center">
      <img src="image/main_page.jpg" alt="Overview page"><br>
      <b>🎛️ Overview</b><br><sub>Animated cross power-flow + energy totals</sub>
    </td>
  </tr>
</table>

### 💬 Telegram Bot

<table>
  <tr>
    <td width="33%" align="center">
      <img src="image/telegram_menu.jpg" alt="Telegram control menu"><br>
      <b>Tap-button control</b><br><sub><code>/menu</code></sub>
    </td>
    <td width="33%" align="center">
      <img src="image/telegram_info.jpg" alt="Telegram status"><br>
      <b>Full status</b><br><sub><code>/info</code></sub>
    </td>
    <td width="33%" align="center">
      <img src="image/telegram_otherinfo.jpg" alt="Telegram commands"><br>
      <b>Quick queries</b><br><sub><code>/status</code> · <code>/pv</code> · <code>/today</code></sub>
    </td>
  </tr>
</table>

---

## 🚀 Quick Start (fresh Raspberry Pi)

```bash
# On the Pi (Raspberry Pi OS Lite, 64-bit recommended):
git clone https://github.com/manoranjan2050/Solar-Bridge-Flin-Fution-JKBMS.git
cd Solar-Bridge-Flin-Fution-JKBMS
bash install_all.sh
```

That's it. The installer is **fully self-contained** — it will:

1. Install all system + Python dependencies (python venv, pyserial, paho-mqtt, flask, flask-socketio, eventlet, requests, avahi, network-manager)
2. Set up USB device permissions (udev) and passwordless service control (sudoers)
3. Ask you for MQTT host/credentials, device ports, dashboard port, hostname, and an optional login password (a short wizard)
4. Install both **systemd services** so everything **auto-starts on boot**
5. Enable `http://<hostname>.local:8080` via mDNS

When it finishes:

```
📊 Dashboard:  http://solar.local:8080   (or http://<PI_IP>:8080)
```

> Re-running the installer **keeps your existing `config.ini`** (won't wipe MQTT creds). Delete `/opt/solar-bridge/config.ini` first for a clean reset.

---

## 🔌 Hardware

| Device | Connection | Port | USB ID |
|---|---|---|---|
| Inverter — Flin Fution (Voltronic/Axpert clone, 5kVA hybrid) | USB HID cable | `/dev/hidraw0` | `0665:5161` |
| Battery — JK BMS (new protocol, 16S LiFePO4) ×2 parallel | CH340 USB-RS485 | `/dev/ttyUSB0` | `1a86:7523` |
| Raspberry Pi 4 (Pi OS Lite) | LAN | — | — |

Works with most Voltronic / Axpert / MPP Solar / EASUN clones (PI30) and any JK BMS using the `55 AA EB 90` broadcast protocol.

---

## 🖥️ The Dashboard

| Page | Shows |
|---|---|
| **Overview** | Animated power-flow, stat cards, energy totals, **solar generation forecast** (today vs typical) |
| **Solar PV** | Animated sun hero, production bar, today/peak/total, today's curve, PV/MPPT + AC + Grid detail |
| **Battery** | Animated SOC gauge + **32 animated cell bars** (charging/discharging/low, colour by cell %), per-BMS detail |
| **Inverter** | All live readings + current settings |
| **History** | 6h/24h/3d/7d charts of power, SOC, temperatures + daily energy |
| **Details** | Lifetime totals + **day/month/year** breakdown chart & table, self-sufficiency |
| **Alerts** | Alert history + Send Test Alert |
| **Inverter Settings** | Change priorities, voltages, currents (applied straight to the inverter) |
| **Automation** | Build threshold/time rules |
| **Notifications** | Telegram, e-mail & alert-threshold config |
| **Network & MQTT / Bluetooth / System / Logs** | WiFi, MQTT, BLE scan, Pi stats, backup/restore, live logs |

### 🔐 Login
Set a password during install (or **Notifications**/`config.ini → [dashboard] password`). Blank = no login. When set, the dashboard and all control APIs require sign-in.

---

## 🌐 Custom Domain

**Local (zero config):** `http://<hostname>.local:8080` works immediately via mDNS/avahi (hostname chosen during install, e.g. `solar.local`).

**Your own domain, e.g. `https://solar.manoranjan.dev`:**

```bash
bash setup_domain.sh
```

This installs **nginx** as a reverse proxy (with WebSocket support) and optionally a **Let's Encrypt HTTPS** certificate.

**Before running it**, point the domain at your Pi:
- **LAN only** → DNS A record (or router/hosts) `solar.yourdomain.com` → Pi's LAN IP
- **Internet + HTTPS** → public DNS A record → your public IP, and forward ports **80 + 443** on your router to the Pi (required for Let's Encrypt)

---

## 🏠 Home Assistant

The bridge auto-publishes MQTT discovery — no YAML. In HA: **Settings → Devices & Services → MQTT** shows:
- **Flin Fution Inverter** (sensors + control entities)
- **JK BMS 1 / 2 / Battery Bank** (battery sensors)

Controls exposed: output/charger priority, max charge & grid-charge current, float/bulk/shutdown/recharge voltages. Alerts publish to `solar/alert`.

> HA depends on MQTT. If the broker login fails, the dashboard + Telegram still work (they don't need MQTT); the logs will show `MQTT CONNECTION REFUSED (rc=135, Not authorized)` so you know to fix the password.

---

## 💬 Telegram Bot

1. **@BotFather** → `/newbot` → copy the **token**
2. **@userinfobot** → copy your numeric **chat id**
3. Dashboard → **Notifications** → enable Telegram, paste both → **Save** → **Send Test Alert**

**Status commands**

| Command | Reply |
|---|---|
| `/info` | Full status: solar, load, grid, battery, per-BMS, cells, temps, today, settings |
| `/status` | Quick overview |
| `/pv` · `/battery` · `/today` | Focused views |

**Control commands** (drive the inverter; work even with MQTT down — the inverter ACKs each)

| Command | Action |
|---|---|
| **`/menu`** | **Tappable button panel** for output/charger priority (easiest) |
| `/output grid \| solar \| sbu` | Output source priority |
| `/charger grid \| solar \| solargrid \| solaronly` | Charger source priority |
| `/maxcharge 60` | Max charge current (A) |
| `/gridcharge 20` | Max grid charge current (A) |
| `/float 54.0` · `/bulk 55.1` | Battery voltages (V) |

**Daily summary:** enable it on the Notifications page to get a once-a-day digest (solar, load, grid, peak PV, lowest SOC, self-sufficiency) at a time you choose.

The bot only responds to your configured chat id.

---

## 🤖 Automation

Dashboard → **Automation** → *Add Rule*:
- **Threshold** — e.g. `Battery SOC < 30 → Charger = Solar+Grid`
- **Time** — e.g. `at 07:00 → Charger = Solar only`

Stored in `automation.json`; saving restarts the bridge.

---

## 🔔 Alerts

Edge-triggered (fire once, recover when normal, cooldown to avoid spam):
low/critical **SOC**, high **battery/inverter temperature**, **overload**, **battery over-voltage**, **cell over-voltage**, **cell imbalance**, **grid lost/restored**, **inverter fault**, **battery full**. All thresholds editable on the **Notifications** page; delivered to Telegram, e-mail, and Home Assistant.

---

## 💾 Backup & Restore

**System** page → Download Backup (config + automation + energy; optional history DB) / Restore from a `.zip` (restarts the bridge). Great before re-flashing or moving to a new Pi.

---

## ⚙️ Configuration Reference

`/opt/solar-bridge/config.ini` (a template is in `config.ini.example`):

```ini
[mqtt]
host = 192.168.1.82
port = 1883
username = youruser
password = yourpass

[inverter]
port = /dev/hidraw0     ; USB HID; /dev/ttyUSB0 for serial cables
protocol = PI30
poll_interval = 10

[jkbms]
port = /dev/ttyUSB0
baud = 115200
cell_count = 16

[dashboard]
username = admin
password =               ; blank = no login
hostname = solar         ; → http://solar.local:8080

[alerts]
enabled = true
low_soc = 20
critical_soc = 10
high_battery_temp = 50
high_inverter_temp = 75
high_cell_diff = 0.1
overload_pct = 90
high_battery_voltage = 57.0
cell_overvoltage = 3.65
notify_on_grid_loss = true
notify_on_fault = true
notify_on_overload = true
notify_on_full = true
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
password =               ; use an app password
from_addr =
to_addr =
```

Most settings are editable from the dashboard.

---

## 🏗️ Architecture

```
Raspberry Pi
└── /opt/solar-bridge/
    ├── solar_bridge.py     # collector: polls inverter (QPIGS/QPIRI/QMOD/QPIWS) + BMS,
    │                       #   publishes MQTT, accumulates energy, runs alerts + automation,
    │                       #   writes live_state.json, processes control_queue.json
    ├── solar_db.py         # SQLite history (readings, daily_energy, alerts)
    ├── notifier.py         # Telegram (alerts + command bot) / e-mail / HA alerts
    ├── automation.py       # threshold + time rule engine
    ├── config.ini          # settings        live_state.json   # live data for dashboard
    ├── energy.json         # kWh totals      control_queue.json# dashboard→bridge commands
    ├── solar_bridge.db     # history         automation.json   # rules
    ├── dashboard/app.py + templates/         # Flask + Socket.IO web UI
    └── venv/

systemd:  solar-bridge.service  ·  solar-dashboard.service   (both auto-start on boot)
optional: nginx (reverse proxy)  via setup_domain.sh
```

**Why it's resilient:** the dashboard reads `live_state.json` and sends control via `control_queue.json`, both written/read locally — so it keeps working even if the MQTT broker is unreachable. MQTT is only required for Home Assistant.

---

## 🔄 Deploying Code Changes (developers)

```bash
# Configure once: copy deploy_secrets.example.py → deploy_secrets.py (git-ignored) and fill in Pi creds,
# or set DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASS env vars.
python deploy.py            # uploads bridge + modules, installs deps, restarts services
```
`config.ini` is never uploaded by `deploy.py`, so live credentials are safe.

---

## 🛠️ Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| **Dashboard shows blank / `--` everywhere** | Bridge not running or no data file. `sudo systemctl status solar-bridge`; check Logs page. |
| **Home Assistant gets no data; Logs show `rc=135 Not authorized`** | Wrong MQTT password in `config.ini [mqtt]`. Fix it (HA Mosquitto uses HA user accounts), then `sudo systemctl restart solar-bridge`. Dashboard/Telegram work regardless. |
| **Inverter setting changes don't apply** | They go via the local command queue now — check Logs for `CMD … -> ACK`. `FAIL`/`NAK` = inverter rejected; `ACK` = applied. |
| **"Save & Restart" / Reboot button: `sudo: a password is required`** | Passwordless sudo rule missing. Re-run `bash install_all.sh` (installs `/etc/sudoers.d/solar-bridge`). |
| **No inverter data / NAK** | `ls /dev/hidraw*` exists? Unplug/replug; confirm protocol `PI30`. |
| **BMS: "no type-02 frames"** occasionally | Normal — it retries; the BMS broadcasts intermittently. |
| **Wrong battery SOC** | Fixed in this project (JK new-protocol offsets). If still off, capture a frame and check offsets in `solar_bridge.py`. |
| **`http://solar.local` not resolving** | Reboot the Pi (hostname change), ensure `avahi-daemon` running; use the IP meanwhile. |
| **Custom domain not loading** | DNS A record must point to the Pi; for HTTPS, ports 80/443 must reach it. See `setup_domain.sh` notes. |
| **Telegram silent** | Enable on Notifications, recheck token + chat id, ensure Pi has internet. |
| **USB permission errors** | `sudo usermod -aG dialout,plugdev <user>` then reboot. |

Handy commands:

```bash
sudo systemctl restart solar-bridge solar-dashboard
sudo journalctl -u solar-bridge -f          # live bridge logs (sensor reads, CMD ACKs, alerts)
sudo journalctl -u solar-dashboard -f       # web server logs
cat /opt/solar-bridge/energy.json           # energy totals
ls -la /dev/hidraw* /dev/ttyUSB*            # devices
```

---

## 📡 Protocol Notes

**Inverter (Voltronic PI30 / USB HID):** 9-byte writes (`0x00` + 8 data), 8 raw bytes per read packet, Voltronic table CRC-16, responses start `(`, set commands reply `(ACK`/`(NAK`.

**JK BMS (`55 AA EB 90`, type `0x02`):** broadcasts ~1/s. Offsets: cells `+6` (16×u16 mV), MOS temp `+144`, pack V `+150`, temp1/2 `+162/164`, **SOC `+173` (u8)**, remaining cap `+174`, design cap `+178`, cycles `+182`, SOH `+190`. Two packs distinguished by frame byte `+5` (`0x00`=BMS1, `0x05`=BMS2).

---

## ⚠️ Known Issues

- **BMS current reads 0 A** — current-field offset not yet confirmed for this firmware; power is derived from the inverter.
- **JK BMS is read-only** (passive monitoring). Charging behaviour is controlled via the inverter, not the BMS.

---

## 🙏 Credits

Built by **[Manoranjan](https://github.com/manoranjan2050)** · [manoranjan.dev](https://manoranjan.dev)

Use at your own risk. Always verify inverter setting changes against your battery/installer specifications.
