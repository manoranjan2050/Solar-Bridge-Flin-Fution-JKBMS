# 🗺️ Solar Bridge — Roadmap & TODO

Last updated: 2026-06-08 · Repo: https://github.com/manoranjan2050/Solar-Bridge-Flin-Fution-JKBMS

This file tracks **what's left to build**. The project itself is fully working and deployed
on the Pi (192.168.1.32). See `PROJECT_CONTEXT.txt` for the full current state.

---

## 🎯 NEXT UP: Standalone "Solar Bridge OS" (flashable SD-card image)

Goal: a downloadable `.img` users flash with Raspberry Pi Imager, boot, and configure from
their phone — exactly like Solar Assistant OS. **Chosen approach: pi-gen / CustomPiOS.**

### Step 1 — pi-gen build setup (the core image builder)
- [ ] Add a **pi-gen custom stage** (`stage-solar`) OR use **CustomPiOS** wrapper
  - CustomPiOS is the easier path (designed exactly for "bundle my app into an image", used by OctoPi)
  - Repo layout: a `os/` folder with the build module that copies the repo into the image and runs `install_all.sh` at build time (non-interactive mode needed — see below)
- [ ] Make `install_all.sh` support a **non-interactive / defaults mode** (env vars instead of prompts) so it can run inside the image build. Add e.g. `SOLAR_NONINTERACTIVE=1` that uses sensible defaults and skips `read`.
- [ ] Base image: Raspberry Pi OS Lite 64-bit (Bookworm)
- [ ] Bake in: python venv + all deps, services enabled, udev rules, avahi, sudoers
- [ ] Output: `solar-bridge-os-<version>.img.xz`

### Step 2 — First-boot WiFi setup wizard (plug-and-play, no SSH)
- [ ] On first boot with no WiFi, start a **hotspot + captive portal** so the user picks WiFi
  + enters MQTT details + dashboard password from their phone
- [ ] Tools to evaluate: `comitup` (simplest), balena `wifi-connect`, or RaspAP
- [ ] After WiFi configured, drop the hotspot and start normally
- [ ] Reuse the existing `/api/wifi/*` and `/api/config` endpoints where possible

### Step 3 — Power-cut safety (important: Pi loses power with the inverter!)
- [ ] **Overlay / read-only rootfs** so sudden power loss can't corrupt the SD card
  - Raspberry Pi OS has `raspi-config` → Performance → Overlay File System
  - Need a way to temporarily disable overlay to save config changes (config writes must persist)
  - Alternative: keep rootfs read-only but mount a small writable partition for `/opt/solar-bridge`
    config + db (so settings/history survive) — design this carefully
- [ ] **Hardware watchdog** (`dtparam=watchdog=on` + systemd `RuntimeWatchdogSec`) so a hung Pi reboots
- [ ] systemd `WatchdogSec` on the bridge service + `sd_notify` heartbeat (optional, nicer)

### Step 4 — Releases & updates
- [ ] **GitHub Actions** workflow to build the `.img` on every tagged release
- [ ] **OTA update** button in the dashboard (System page): `git pull` + reinstall deps + restart
      (endpoint `/api/update` → run a safe update script; show result)
- [ ] Version string shown in dashboard footer + `/info`

---

## 🔧 Feature ideas (not started) — ranked by value

### High value
- [ ] **Weather-based solar forecast** — replace the 7-day-average "typical" line with a real
      forecast from a free API (Open-Meteo has free solar/cloud data, no key). Show expected
      kWh for today/tomorrow on Overview + Details.
- [ ] **Time-of-use tariffs** — per-hour ₹/kWh (peak/off-peak) for accurate cost (currently flat).
      Extend `[cost]` config + the Details cost math.
- [ ] **Smart load automation** — "if excess solar > X W for N min → turn on relay/smart-plug"
      (control a Tasmota/Shelly plug via MQTT, or a Pi GPIO relay). Extend `automation.py`.
- [ ] **Battery SOH / degradation trend** — track full-charge capacity over months, chart it,
      alert if it drops. Store monthly snapshots in the DB.
- [ ] **Production anomaly alert** — "PV 30% below expected for this hour" using the pv_profile.

### Medium
- [ ] **BMS charge/discharge current** — decode the JK frame offset (charge current likely
      int32 @158). Need a capture while battery is charging/discharging at a KNOWN current to
      confirm sign + scale. (Field at +158 was 0 when battery was full/floating.)
- [ ] **Weekly/Monthly PDF or HTML report** e-mailed automatically.
- [ ] **Grafana + InfluxDB** optional export for power users (the DB already has the data).
- [ ] **MQTT over TLS** option.
- [ ] **CO₂ saved + tree-equivalent** gamification card on Details.
- [ ] **Multi-language** (i18n) — at least Hindi + English.
- [ ] **Generator support** (if a genset is added to the system).

### Nice-to-have / polish
- [ ] Telegram: inline buttons for numeric settings too (maxcharge/float via +/- buttons).
- [ ] Dashboard: light/dark theme toggle.
- [ ] Dashboard: configurable refresh rate.
- [ ] Backup to cloud (Google Drive / S3).
- [ ] DB `VACUUM` occasionally to reclaim disk after purge.

---

## 🐛 Known issues
- **BMS current reads 0 A** — frame offset for charge/discharge current not confirmed (see above).
- **JK BMS is read-only** (passive RS485 broadcast) — no BMS write control; charging is
  controlled via the inverter, which is correct & safe.
- **MQTT username is `manoranjan2050`** (not `manoranjan`) — already fixed in the Pi config.

---

## ✅ Done (for reference — all working & deployed)
Dashboard (overview w/ animated cross power-flow, solar hero, battery gauge + 32 animated cells,
inverter, history charts, details w/ cost+CSV, alerts, settings w/ BMS panel, automation,
notifications, network, bluetooth, system w/ backup-restore + change-password, logs) ·
PWA installable · login + 30-day session · Telegram bot (status + /menu buttons + control +
daily summary) · alerts (SOC/temp/overload/over-voltage/cell/grid/fault/full) · automation rules ·
Home Assistant MQTT discovery + controls · inverter control verified (correct POP/PCP/PCVV/MUCHGC
commands, QPIRI fields 16/17) · BMS SOC fix (offset 173) · custom domain (avahi + nginx script) ·
MQTT-independent operation (live_state.json + control_queue.json) · DB auto-purge + journald cap ·
one-command `install_all.sh` · README with screenshots.
