# 🛠️ Solar Bridge — Troubleshooting Guide

Real problems hit during setup + their exact fixes. Find your symptom, apply the fix.

> Quick health check on the Pi:
> ```bash
> sudo systemctl status solar-bridge solar-dashboard      # both should be "active"
> sudo journalctl -u solar-bridge -f                      # live log (sensor reads, MQTT, CMD ACKs)
> cat /opt/solar-bridge/config.ini                        # verify settings
> ```

---

## 1. ⚡ MOST COMMON: Home Assistant gets no data

### 1a. Wrong MQTT **port** (the #1 mistake)
**Symptom:** bridge log shows `MQTT disconnected ... (will retry)` or `Connection refused`; HA gets nothing.
**Cause:** the MQTT port was set to something other than **1883** (e.g. `8123` = HA's *web* page, or a typo like `1993`).
**Fix:** the MQTT broker port is **always `1883`**.
```ini
[mqtt]
host = 192.168.1.82
port = 1883          # <-- MUST be 1883, never 8123/1993/etc.
```
Then: `sudo systemctl restart solar-bridge`

### 1b. Wrong MQTT **username/password** → `rc=135 Not authorized`
**Symptom:** bridge log: `MQTT CONNECTION REFUSED ... (rc=135, Not authorized)`.
**Cause:** Mosquitto authenticates against **Home Assistant user accounts**. The username must be a real HA user — it is **NOT** the Pi SSH login.
**This setup's correct creds:** user `manoranjan2050`, pass `<MQTT_PASSWORD>`.
> Note: `manoranjan` / `<PI_PASSWORD>` is the **Pi SSH login** — the broker rejects it.
**Fix:** set the right creds in `config.ini [mqtt]` (or dashboard → Network & MQTT), restart bridge.
**To find the working combo,** test on the Pi:
```bash
/opt/solar-bridge/venv/bin/python - <<'PY'
import paho.mqtt.client as mqtt, time
def test(u,p):
    r=[None]
    def on_c(c,d,f,rc,pr=None): r[0]=str(rc)
    c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id="t")
    c.username_pw_set(u,p); c.on_connect=on_c
    c.connect("192.168.1.82",1883,10); c.loop_start(); time.sleep(2); c.loop_stop()
    print(u,"/",p,"->",r[0])
test("manoranjan2050","<MQTT_PASSWORD>")
PY
```
`Success` = correct; `Not authorized` = wrong.

### 1c. MQTT **integration** not added in HA (vs. just the broker add-on)
**Symptom:** broker is up, bridge connected, but HA shows no devices.
**Cause:** the **Mosquitto broker add-on** (the server) and the **MQTT integration** (HA's subscriber) are *two separate things*. You need both.
**Fix:**
1. Settings → Add-ons → **Mosquitto broker** → Installed + **Started** (+ Start on boot)
2. Settings → Devices & Services → **+ Add Integration → MQTT**
   - Broker `core-mosquitto`, Port `1883`, user `manoranjan2050`, pass `<MQTT_PASSWORD>`
   - **Enable discovery** (ON, prefix `homeassistant`)
3. Devices appear automatically — **do NOT use the "add MQTT device manually" form**.

### 1d. Live test from inside HA
MQTT integration → **Configure → Listen to a topic** → `#` → Start.
- Messages flood in → HA *is* connected (issue is discovery — re-add with discovery ON).
- Nothing → HA's integration points at the wrong/empty broker.

---

## 2. 🟡 HA shows the devices but all **Sensors = "Unknown"** (Controls have values)
**Cause:** sensor values were published *non-retained*, so HA showed "Unknown" until the next publish / after a reload. (Controls use retained topics, so they showed values — that's the tell.)
**Fix:** already fixed in code — `pub()` now publishes `retain=True`. If you still see Unknown:
- `sudo systemctl restart solar-bridge` (re-publishes retained values)
- HA → MQTT integration → ⋮ → **Reload**

---

## 3. 🖥️ Dashboard shows blank / `--` / `null` everywhere
**Causes & fixes:**
- **Bridge not running:** `sudo systemctl status solar-bridge` → if dead, `sudo journalctl -u solar-bridge -n50`.
- **Inverter/BMS port wrong** (see #8) → bridge can't read hardware → no data.
- The dashboard reads `live_state.json` (written by the bridge from USB reads) — it does **not** need MQTT. If the dashboard is blank, the *bridge* isn't producing data; check its log.

---

## 4. ⚙️ Inverter setting changes don't apply / revert
**Symptom:** change a setting on the dashboard/HA, it snaps back.
**Cause (historical):** wrong inverter command prefixes / QPIRI field offsets for this Flin Fution unit.
**Confirmed-correct commands** (others return `(NAK`):
| Setting | Prefix | Example |
|---|---|---|
| Output priority | `POP` | `POP01` (00=Grid,01=Solar,02=SBU) |
| Charger priority | `PCP` | `PCP03` (00=Grid,01=Solar,02=Sol+Grid,03=SolOnly) |
| Max / grid charge current | `MUCHGC` | `MUCHGC060` |
| Float voltage | `PBFT` | `PBFT54.0` |
| Bulk voltage | `PCVV` | `PCVV55.1` |
| Shutdown voltage | `PSDV` | `PSDV48.0` |
| Recharge voltage | `PBDV` | `PBDV46.0` |

**QPIRI field indices:** `[14]`=max charge, **`[16]`**=output priority, **`[17]`**=charger priority.
**How to verify a change worked:** Logs page (or `journalctl`) → look for `CMD POP01 -> ACK`. `ACK` = applied, `NAK`/`FAIL` = rejected.
**Note:** control goes via `control_queue.json` (works even if MQTT is down) and forces a QPIRI re-read so the dashboard reflects it within seconds.

---

## 5. 🔋 Battery SOC wrong (e.g. shows 46% when full)
**Cause:** JK BMS new-protocol SOC byte offset.
**Fix (done):** SOC is **uint8 at offset 173** (not u32 @182). Other confirmed offsets:
remaining `+174`, design `+178`, cycles `+182`, SOH `+190`, pack V `+150`, cells `+6`, temps `+162/164`, MOS `+144`.
If a new BMS firmware reads wrong, capture a type-02 frame and re-confirm offsets in `solar_bridge.py`.

---

## 6. 🔌 "Save & Restart" or **Reboot** button → `sudo: a password is required`
**Cause:** passwordless sudo rule missing.
**Fix:** re-run `bash install_all.sh` (installs `/etc/sudoers.d/solar-bridge`), or add it manually for `restart/start/stop/status` of both services + reboot.

---

## 7. 🌉 Bridge crashes / crash-loops when the broker is down
**Cause (historical):** `client.connect()` raised `ConnectionRefusedError` at startup and killed the process.
**Fix (done):** uses `connect_async` + background retry — the bridge keeps reading the inverter/serving the dashboard and reconnects automatically. Also, **HA discovery is published in the `on_connect` callback**, so it's re-sent on every reconnect (self-heals after a broker restart).

---

## 8. 🔧 Inverter/BMS "could not open port" / no hardware data
**Symptom:** log: `Inverter open failed: could not open port ...` / `JKBMS open failed`.
**Cause:** wrong device port in config (e.g. an accidental edit set it to `1883`!), or USB unplugged, or permissions.
**Fix — set the right ports (section-aware!):**
```ini
[inverter]
port = /dev/hidraw0
[jkbms]
port = /dev/ttyUSB0
```
> ⚠️ Never `sed -i 's/^port = .*/.../'` — it changes **all** `port=` lines (mqtt + inverter + jkbms). Edit per-section.
Check devices exist: `ls -la /dev/hidraw* /dev/ttyUSB*`. Permissions: `sudo usermod -aG dialout,plugdev <user>` then reboot.

---

## 9. 💬 Telegram bot not responding / no alerts
- Enable it: dashboard → **Notifications** → Telegram → token (from @BotFather) + chat id (from @userinfobot) → Save.
- The bot only replies to the configured chat id.
- Pi needs internet access (Telegram uses HTTPS, not MQTT — works even if MQTT is down).
- Test with the **Send Test Alert** button.

---

## 10. 🌐 `http://solar.local` doesn't resolve
- Reboot the Pi (hostname change needs it); ensure `avahi-daemon` is running.
- Use the IP (`http://192.168.1.32:8080`) meanwhile.
- For a public domain, run `bash setup_domain.sh` (needs DNS A-record + ports 80/443 for HTTPS).

---

## 11. 🗄️ SD card filling up over time
- DB auto-purges nightly (`[history] retain_days`, default 90). Set `0` to disable.
- journald is capped at 200 MB by the installer.
- To shrink the DB file after purge: `sqlite3 /opt/solar-bridge/solar_bridge.db 'VACUUM;'`

---

## 12. ₹ Currency / special characters show as garbage (`â‚¹`)
**Cause:** config read with wrong encoding.
**Fix (done):** config is read/written as UTF-8. If editing by hand, save the file as UTF-8.

---

## Useful one-liners
```bash
# Restart everything
sudo systemctl restart solar-bridge solar-dashboard
# What is the bridge doing right now?
sudo journalctl -u solar-bridge -f
# Is MQTT connected? (look for "MQTT connected ... as 'manoranjan2050'")
sudo journalctl -u solar-bridge | grep -i mqtt | tail
# Are devices/data on the broker? (run on the Pi)
/opt/solar-bridge/venv/bin/python -c "import paho.mqtt.client as m,time; \
c=m.Client(m.CallbackAPIVersion.VERSION2); c.username_pw_set('manoranjan2050','<MQTT_PASSWORD>'); \
g={}; c.on_connect=lambda *a: c.subscribe('solar/#'); c.on_message=lambda cl,u,msg: g.__setitem__(msg.topic,1); \
c.connect('192.168.1.82',1883); c.loop_start(); time.sleep(5); print('topics:',len(g))"
```

---
*Built by [Manoranjan](https://github.com/manoranjan2050) · [manoranjan.dev](https://manoranjan.dev)*
