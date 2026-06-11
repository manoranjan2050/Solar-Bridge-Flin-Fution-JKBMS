#!/usr/bin/env python3
"""Solar Bridge Dashboard — Flask + WebSocket backend."""

import configparser, io, json, os, subprocess, sys, threading, time, zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, send_file, send_from_directory)
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt

BASE      = Path(__file__).parent
CFG_PATH  = BASE.parent / "config.ini"
NRG_PATH  = BASE.parent / "energy.json"

# Allow importing the bridge's add-on modules (history DB, automation)
sys.path.insert(0, str(BASE.parent))
try:
    from solar_db import db
except Exception:
    db = None
try:
    import automation as automation_mod
except Exception:
    automation_mod = None

app       = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("DASH_SECRET", "solar-bridge-secret-key-2025")
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30   # stay logged in 30 days
socketio  = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# ── Authentication ─────────────────────────────────────────────────────────
def auth_creds():
    """Return (username, password) from config; password '' means no login."""
    load_cfg()
    return (cfg.get("dashboard", "username", fallback="admin").split("#")[0].strip(),
            cfg.get("dashboard", "password", fallback="").split("#")[0].strip())

def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        _, pwd = auth_creds()
        if pwd and not session.get("auth"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "auth required"}), 401
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    user, pwd = auth_creds()
    if not pwd:
        return redirect(url_for("index"))
    if request.method == "POST":
        body = request.form
        if body.get("username") == user and body.get("password") == pwd:
            session.permanent = True          # honour PERMANENT_SESSION_LIFETIME
            session["auth"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html", error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/api/default_user")
def api_default_user():
    """Return the configured dashboard username (not the password)."""
    user, _ = auth_creds()
    return jsonify({"username": user})

@app.route("/api/change_password", methods=["POST"])
@login_required
def api_change_password():
    body = request.json or {}
    old_user, old_pwd = auth_creds()
    current = body.get("current_password", "")
    new_user = body.get("new_username", "").strip()
    new_pwd  = body.get("new_password", "").strip()
    if old_pwd and current != old_pwd:
        return jsonify({"ok": False, "error": "Current password is incorrect"}), 400
    if not new_user:
        return jsonify({"ok": False, "error": "Username cannot be blank"}), 400
    load_cfg()
    if not cfg.has_section("dashboard"):
        cfg.add_section("dashboard")
    cfg.set("dashboard", "username", new_user)
    cfg.set("dashboard", "password", new_pwd)
    save_cfg()
    session.clear()   # force re-login with new credentials
    notify_change("Dashboard login credentials were changed")
    return jsonify({"ok": True, "msg": "Credentials updated — please sign in again"})

# ── In-memory state ──────────────────────────────────────────────────────────
state   = {}          # topic_key → value
cfg     = configparser.ConfigParser(interpolation=None)
_mqtt   = None

def load_cfg():
    cfg.read(CFG_PATH, encoding="utf-8")

def save_cfg():
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)

def topic_key(topic: str) -> str:
    return topic.replace("solar/", "").replace("/", "_")

# ── MQTT ─────────────────────────────────────────────────────────────────────
def mqtt_on_message(client, userdata, msg):
    key   = topic_key(msg.topic)
    raw   = msg.payload.decode(errors="ignore").strip()
    try:    val = float(raw)
    except: val = raw
    state[key] = val
    socketio.emit("update", {"k": key, "v": raw})

def mqtt_on_connect(client, userdata, flags, rc, props=None):
    code = getattr(rc, "value", rc)
    if code == 0:
        client.subscribe("solar/#")
        print(f"[dashboard] MQTT connected, subscribed to solar/#")
    else:
        print(f"[dashboard] MQTT CONNECTION REFUSED (rc={code}, {rc}). "
              f"Check [mqtt] username/password in config.ini!")
    socketio.emit("bridge_status", {"connected": code == 0})

def start_mqtt():
    global _mqtt
    load_cfg()
    host  = cfg.get("mqtt", "host", fallback="localhost").split("#")[0].strip()
    port  = int(cfg.get("mqtt", "port", fallback="1883").split("#")[0].strip())
    user  = cfg.get("mqtt", "username", fallback="").split("#")[0].strip()
    pwd   = cfg.get("mqtt", "password", fallback="").split("#")[0].strip()

    _mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="solar_dash")
    if user: _mqtt.username_pw_set(user, pwd or None)
    _mqtt.on_connect = mqtt_on_connect
    _mqtt.on_message = mqtt_on_message
    try:
        _mqtt.connect(host, port, keepalive=30)
        _mqtt.loop_start()
    except Exception as e:
        print(f"MQTT connect failed: {e}")

# ── Telegram notification for every settings change ──────────────────────────
def notify_change(message: str):
    """Fire a Telegram message whenever any setting is changed (best-effort,
    runs in a background thread so the HTTP response is never delayed)."""
    # capture remote_addr before leaving the request context
    try: addr = request.remote_addr or ""
    except Exception: addr = ""
    def _bg():
        try:
            from notifier import Notifier
            load_cfg()
            n = Notifier(cfg, None)
            n._tg_send(f"⚙️ *Setting changed*\n{message}" + (f"\n_from {addr}_" if addr else ""))
        except Exception as e:
            print(f"notify_change failed: {e}")
    threading.Thread(target=_bg, daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    _, pwd = auth_creds()
    return render_template("index.html", auth_enabled=bool(pwd))

# ── PWA (installable app) — served at root for correct scope ──────────────────
@app.route("/sw.js")
def pwa_sw():
    return send_from_directory(BASE / "static", "sw.js",
                               mimetype="application/javascript")

@app.route("/manifest.webmanifest")
def pwa_manifest():
    return send_from_directory(BASE / "static", "manifest.webmanifest",
                               mimetype="application/manifest+json")

LIVE_PATH = BASE.parent / "live_state.json"

@app.route("/api/state")
def api_state():
    # live_state.json is written by the bridge from the USB reads, so the
    # dashboard shows data even when MQTT is unavailable. Live MQTT values
    # (in `state`) override the file when present.
    live = {}
    try: live = json.loads(LIVE_PATH.read_text())
    except: pass
    nrg = {}
    try: nrg = json.loads(NRG_PATH.read_text())
    except: pass
    nrg = {k: v for k, v in nrg.items() if not isinstance(v, dict)}  # skip baseline/day
    return jsonify({**live, **state, **{f"energy_{k}": v for k, v in nrg.items()}})

@app.route("/api/config")
def api_config():
    load_cfg()
    return jsonify({
        "mqtt_host":     cfg.get("mqtt","host","fallback","").split("#")[0].strip(),
        "mqtt_port":     cfg.get("mqtt","port",fallback="1883").split("#")[0].strip(),
        "mqtt_user":     cfg.get("mqtt","username",fallback="").split("#")[0].strip(),
        "inv_port":      cfg.get("inverter","port",fallback="/dev/hidraw0").split("#")[0].strip(),
        "inv_interval":  cfg.get("inverter","poll_interval",fallback="10").split("#")[0].strip(),
        "bms_port":      cfg.get("jkbms","port",fallback="/dev/ttyUSB0").split("#")[0].strip(),
        "bms_baud":      cfg.get("jkbms","baud",fallback="115200").split("#")[0].strip(),
        "bms_cells":     cfg.get("jkbms","cell_count",fallback="16").split("#")[0].strip(),
    })

@app.route("/api/config", methods=["POST"])
@login_required
def api_config_save():
    body = request.json or {}
    load_cfg()
    mapping = {
        "mqtt_host":    ("mqtt","host"),
        "mqtt_port":    ("mqtt","port"),
        "mqtt_user":    ("mqtt","username"),
        "mqtt_pass":    ("mqtt","password"),
        "inv_port":     ("inverter","port"),
        "inv_interval": ("inverter","poll_interval"),
        "bms_port":     ("jkbms","port"),
        "bms_baud":     ("jkbms","baud"),
        "bms_cells":    ("jkbms","cell_count"),
    }
    changed = []
    for key, (section, opt) in mapping.items():
        if key in body:
            if not cfg.has_section(section): cfg.add_section(section)
            cfg.set(section, opt, str(body[key]))
            changed.append(key if key != "mqtt_pass" else "mqtt_pass(•••)")
    save_cfg()
    if changed:
        notify_change("Bridge config updated: " + ", ".join(changed))
    return jsonify({"ok": True})

CONTROL_QUEUE = BASE.parent / "control_queue.json"

@app.route("/api/control", methods=["POST"])
@login_required
def api_control():
    body = request.json or {}
    key  = body.get("key","")
    val  = body.get("value","")
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400
    # Append to the queue file the bridge polls — works even when MQTT is down,
    # and avoids double-applying (HA still controls via its own MQTT publishes).
    try:
        q = []
        if CONTROL_QUEUE.exists():
            try: q = json.loads(CONTROL_QUEUE.read_text())
            except Exception: q = []
        q.append({"key": key, "value": str(val), "ts": time.time()})
        CONTROL_QUEUE.write_text(json.dumps(q[-50:]))
        notify_change(f"Inverter control: *{key}* → `{val}`")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/wifi/scan")
def wifi_scan():
    try:
        r = subprocess.run(
            ["nmcli","-t","-f","SSID,SIGNAL,SECURITY","dev","wifi","list","--rescan","yes"],
            capture_output=True, text=True, timeout=15)
        nets = []
        seen = set()
        for line in r.stdout.splitlines():
            p = line.split(":")
            ssid = p[0].strip()
            if ssid and ssid not in seen:
                seen.add(ssid)
                nets.append({"ssid": ssid,
                             "signal": int(p[1]) if len(p)>1 and p[1].isdigit() else 0,
                             "security": p[2] if len(p)>2 else "Open"})
        nets.sort(key=lambda x: x["signal"], reverse=True)
        return jsonify(nets)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/wifi/connect", methods=["POST"])
@login_required
def wifi_connect():
    body = request.json or {}
    ssid = body.get("ssid","")
    pwd  = body.get("password","")
    try:
        if pwd:
            r = subprocess.run(["nmcli","dev","wifi","connect",ssid,"password",pwd],
                               capture_output=True, text=True, timeout=30)
        else:
            r = subprocess.run(["nmcli","dev","wifi","connect",ssid],
                               capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        if ok:
            notify_change(f"WiFi connected to *{ssid}*")
        return jsonify({"ok": ok, "msg": (r.stdout or r.stderr).strip()})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/api/wifi/status")
def wifi_status():
    try:
        r = subprocess.run(["nmcli","-t","-f","DEVICE,STATE,CONNECTION","dev"],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            p = line.split(":")
            if len(p) >= 3 and p[1] == "connected":
                return jsonify({"connected": True, "ssid": p[2], "device": p[0]})
        return jsonify({"connected": False})
    except:
        return jsonify({"connected": False})

# ── Static IP configuration ──────────────────────────────────────────────────
def _active_connection():
    """Return (connection_name, device, type) of the primary active connection."""
    r = subprocess.run(["nmcli","-t","-f","NAME,DEVICE,TYPE","connection","show","--active"],
                       capture_output=True, text=True, timeout=10)
    eth = wifi = None
    for line in r.stdout.splitlines():
        p = line.split(":")
        if len(p) >= 3:
            if p[2] == "802-3-ethernet" and not eth:  eth  = (p[0], p[1], "ethernet")
            if p[2] in ("wifi", "802-11-wireless") and not wifi: wifi = (p[0], p[1], "wifi")
    return eth or wifi or (None, None, None)

# Public (WAN) IP — cached 10 min so we don't hammer the lookup service
_pubip_cache = {"ip": "", "ts": 0.0}

def _public_ip():
    if _pubip_cache["ip"] and time.time() - _pubip_cache["ts"] < 600:
        return _pubip_cache["ip"]
    try:
        import requests as _rq
        ip = _rq.get("https://api.ipify.org", timeout=6).text.strip()
        if ip and len(ip) < 64:
            _pubip_cache.update(ip=ip, ts=time.time())
            return ip
    except Exception:
        pass
    return _pubip_cache["ip"]

@app.route("/api/network/ipinfo")
@login_required
def api_ipinfo():
    """Current IP configuration of the primary connection (for the Network page)."""
    import socket as _socket
    info = {"ip": "", "gateway": "", "dns": "", "method": "", "connection": "",
            "device": "", "type": "", "hostname": _socket.gethostname(),
            "public_ip": _public_ip()}
    try:
        conn, dev, ctype = _active_connection()
        if not conn:
            return jsonify({**info, "error": "No active connection found"})
        info.update({"connection": conn, "device": dev, "type": ctype})
        r = subprocess.run(["nmcli","-t","-f","ipv4.method,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS",
                            "connection","show",conn],
                           capture_output=True, text=True, timeout=10)
        dns = []
        for line in r.stdout.splitlines():
            k, _, val = line.partition(":")
            if k == "ipv4.method":            info["method"]  = val.strip()
            elif k.startswith("IP4.ADDRESS") and not info["ip"]: info["ip"] = val.strip()
            elif k == "IP4.GATEWAY":          info["gateway"] = val.strip()
            elif k.startswith("IP4.DNS"):     dns.append(val.strip())
        info["dns"] = ", ".join(dns)
    except Exception as e:
        info["error"] = str(e)
    return jsonify(info)

@app.route("/api/network/static_ip", methods=["POST"])
@login_required
def api_static_ip():
    """Set a static IP (mode=static) or revert to DHCP (mode=dhcp) via nmcli.
    The change is applied by re-activating the connection, so the dashboard
    will be reachable on the NEW address a few seconds after this returns."""
    body = request.json or {}
    mode = body.get("mode", "static")
    try:
        conn, dev, _ = _active_connection()
        if not conn:
            return jsonify({"ok": False, "msg": "No active connection found"}), 500

        if mode == "dhcp":
            cmds = [["sudo","nmcli","connection","modify",conn,
                     "ipv4.method","auto","ipv4.addresses","","ipv4.gateway","","ipv4.dns",""]]
            change_msg = f"Network *{conn}* reverted to DHCP (automatic IP)"
        else:
            ip      = body.get("ip", "").strip()
            prefix  = str(body.get("prefix", "24")).strip() or "24"
            gateway = body.get("gateway", "").strip()
            dns     = body.get("dns", "").strip() or "8.8.8.8,1.1.1.1"
            if not ip or not gateway:
                return jsonify({"ok": False, "msg": "IP address and gateway are required"}), 400
            cmds = [["sudo","nmcli","connection","modify",conn,
                     "ipv4.method","manual",
                     "ipv4.addresses",f"{ip}/{prefix}",
                     "ipv4.gateway",gateway,
                     "ipv4.dns",dns.replace(" ", "")]]
            change_msg = f"Static IP set: *{ip}/{prefix}* gw {gateway} on {conn}"

        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode != 0:
                msg = (r.stderr or r.stdout).strip()
                if "password" in msg.lower() or "polkit" in msg.lower() or "not authorized" in msg.lower():
                    msg = ("Permission denied — add nmcli to passwordless sudo: "
                           "run install_all.sh again or add "
                           "'/usr/bin/nmcli' to /etc/sudoers.d/solar-bridge")
                return jsonify({"ok": False, "msg": msg}), 500

        notify_change(change_msg)
        # Re-activate in the background AFTER responding, so the browser gets the
        # success message before the IP actually changes underneath it.
        def _reactivate():
            time.sleep(1.5)
            subprocess.run(["sudo","nmcli","connection","up",conn],
                           capture_output=True, text=True, timeout=30)
        threading.Thread(target=_reactivate, daemon=True).start()

        new_ip = body.get("ip", "") if mode == "static" else None
        return jsonify({"ok": True,
                        "msg": ("Applying — Pi will move to the new address in a few seconds. "
                                + (f"Reconnect at http://{new_ip}:8080" if new_ip
                                   else "Reconnect using the router-assigned address.")),
                        "new_ip": new_ip})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/api/bluetooth/scan")
def bt_scan():
    try:
        r = subprocess.run(["bluetoothctl","scan","on"],
                           capture_output=True, text=True, timeout=6)
        r2 = subprocess.run(["bluetoothctl","devices"],
                            capture_output=True, text=True)
        devices = []
        for line in r2.stdout.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) == 3:
                devices.append({"mac": parts[1], "name": parts[2]})
        return jsonify(devices)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/service/<action>", methods=["POST"])
@login_required
def service_action(action):
    # whole-system reboot
    if action == "reboot":
        try:
            notify_change("🔄 Pi reboot requested from dashboard")
            subprocess.Popen(["sudo", "reboot"])
            return jsonify({"ok": True, "msg": "Rebooting..."})
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)}), 500
    allowed = {"restart", "stop", "start", "status"}
    if action not in allowed:
        return jsonify({"ok": False, "msg": "unknown action"}), 400
    svc = request.json.get("service", "solar-bridge") if request.json else "solar-bridge"
    if svc not in ("solar-bridge", "solar-dashboard"):
        return jsonify({"ok": False, "msg": "unknown service"}), 400
    try:
        r = subprocess.run(["sudo", "systemctl", action, svc],
                           capture_output=True, text=True)
        ok = r.returncode == 0
        msg = (r.stdout + r.stderr).strip()
        if not ok and "password" in msg.lower():
            msg = ("Permission denied — passwordless sudo not configured. "
                   "Re-run install_all.sh or add /etc/sudoers.d/solar-bridge.")
        return jsonify({"ok": ok, "msg": msg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/api/logs")
def api_logs():
    try:
        r = subprocess.run(
            ["sudo","journalctl","-u","solar-bridge","-n","50","--no-pager","--output=cat"],
            capture_output=True, text=True)
        return jsonify({"logs": r.stdout})
    except Exception as e:
        return jsonify({"logs": str(e)})

@app.route("/api/sysinfo")
def api_sysinfo():
    info = {}
    try:
        r = subprocess.run(["vcgencmd","measure_temp"], capture_output=True, text=True)
        info["cpu_temp"] = r.stdout.replace("temp=","").strip()
    except: info["cpu_temp"] = "N/A"
    try:
        r = subprocess.run(["free","-m"], capture_output=True, text=True)
        parts = r.stdout.splitlines()[1].split()
        info["mem_used"] = parts[2]; info["mem_total"] = parts[1]
    except: pass
    try:
        r = subprocess.run(["df","-h","/"], capture_output=True, text=True)
        parts = r.stdout.splitlines()[1].split()
        info["disk_used"] = parts[2]; info["disk_total"] = parts[1]; info["disk_pct"] = parts[4]
    except: pass
    try:
        r = subprocess.run(["uptime","-p"], capture_output=True, text=True)
        info["uptime"] = r.stdout.strip()
    except: pass
    return jsonify(info)

# ── History / Energy / Alerts ─────────────────────────────────────────────────
@app.route("/api/history")
@login_required
def api_history():
    key   = request.args.get("key", "")
    hours = int(request.args.get("hours", "24"))
    if not db or not key:
        return jsonify([])
    return jsonify(db.get_history(key, hours))

@app.route("/api/today_energy")
@login_required
def api_today_energy():
    if not db:
        return jsonify({})
    return jsonify(db.get_today_energy())

@app.route("/api/daily_energy")
@login_required
def api_daily_energy():
    if not db:
        return jsonify([])
    return jsonify(db.get_daily_energy(int(request.args.get("days", "7"))))

@app.route("/api/alerts")
@login_required
def api_alerts():
    if not db:
        return jsonify([])
    return jsonify(db.get_alerts(int(request.args.get("limit", "30"))))

@app.route("/api/alerts/ack", methods=["POST"])
@login_required
def api_alerts_ack():
    if db:
        db.ack_alert(int((request.json or {}).get("id", 0)))
    return jsonify({"ok": True})

# ── Automation rules ───────────────────────────────────────────────────────────
@app.route("/api/automation")
@login_required
def api_automation_get():
    if not automation_mod:
        return jsonify([])
    return jsonify(automation_mod.load_rules())

@app.route("/api/automation", methods=["POST"])
@login_required
def api_automation_save():
    if not automation_mod:
        return jsonify({"ok": False, "error": "automation module unavailable"}), 500
    rules = request.json
    if not isinstance(rules, list):
        return jsonify({"ok": False, "error": "expected a list of rules"}), 400
    automation_mod.save_rules(rules)
    notify_change(f"Automation rules updated ({len(rules)} rule{'s' if len(rules)!=1 else ''})")
    # Ask the bridge to reload by bouncing the service (rules are read at start)
    try:
        subprocess.run(["sudo", "systemctl", "restart", "solar-bridge"], timeout=15)
    except Exception:
        pass
    return jsonify({"ok": True})

# ── Notification / alert config ────────────────────────────────────────────────
NOTIFY_FIELDS = {
    "telegram": ["enabled", "token", "chat_id", "daily_summary", "summary_time"],
    "email":    ["enabled", "smtp_host", "smtp_port", "username", "from_addr", "to_addr"],
    "alerts":   ["enabled", "low_soc", "critical_soc", "high_battery_temp",
                 "high_inverter_temp", "high_cell_diff", "overload_pct",
                 "high_battery_voltage", "cell_overvoltage", "notify_on_grid_loss",
                 "notify_on_fault", "notify_on_overload", "notify_on_full", "cooldown"],
}

@app.route("/api/notify_config")
@login_required
def api_notify_get():
    load_cfg()
    out = {}
    for section, keys in NOTIFY_FIELDS.items():
        for k in keys:
            out[f"{section}_{k}"] = cfg.get(section, k, fallback="").split("#")[0].strip()
    return jsonify(out)

@app.route("/api/notify_config", methods=["POST"])
@login_required
def api_notify_save():
    body = request.json or {}
    load_cfg()
    for section, keys in NOTIFY_FIELDS.items():
        if not cfg.has_section(section):
            cfg.add_section(section)
        for k in keys:
            field = f"{section}_{k}"
            if field in body:
                cfg.set(section, k, str(body[field]))
        # password fields only updated when non-empty (so we don't wipe them)
    if body.get("telegram_token_changed") and "telegram_token" in body:
        cfg.set("telegram", "token", str(body["telegram_token"]))
    if body.get("email_password"):
        cfg.set("email", "password", str(body["email_password"]))
    save_cfg()
    notify_change("Notification / alert settings updated")
    try:
        subprocess.run(["sudo", "systemctl", "restart", "solar-bridge"], timeout=15)
    except Exception:
        pass
    return jsonify({"ok": True})

@app.route("/api/test_alert", methods=["POST"])
@login_required
def api_test_alert():
    """Send a test alert through every enabled channel (Telegram / e-mail / HA)."""
    try:
        from notifier import Notifier
        load_cfg()
        Notifier(cfg, _mqtt).send("info", "✅ Test alert from Solar Bridge dashboard")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── Energy statistics / forecast ─────────────────────────────────────────────────
def _cost_cfg():
    load_cfg()
    g = lambda k, d: cfg.get("cost", k, fallback=d).split("#")[0].strip()
    try: imp = float(g("import_rate", "8.0"))
    except: imp = 8.0
    try: exp = float(g("export_rate", "0"))
    except: exp = 0.0
    return {"currency": g("currency", "₹") or "₹", "import_rate": imp, "export_rate": exp}

@app.route("/api/cost_config")
@login_required
def api_cost_get():
    return jsonify(_cost_cfg())

@app.route("/api/cost_config", methods=["POST"])
@login_required
def api_cost_save():
    body = request.json or {}
    load_cfg()
    if not cfg.has_section("cost"):
        cfg.add_section("cost")
    for k in ("currency", "import_rate", "export_rate"):
        if k in body:
            cfg.set("cost", k, str(body[k]))
    save_cfg()
    notify_change("Cost / tariff settings updated")
    return jsonify({"ok": True})

@app.route("/api/energy_stats")
@login_required
def api_energy_stats():
    period = request.args.get("period", "day")
    limit  = int(request.args.get("limit", "30"))
    if not db:
        return jsonify({"buckets": [], "totals": {}, "cost": _cost_cfg()})
    return jsonify({"buckets": db.energy_buckets(period, limit),
                    "totals":  db.energy_totals(),
                    "cost":    _cost_cfg()})

@app.route("/api/export_csv")
@login_required
def api_export_csv():
    """Download energy stats as CSV (day/month/year), incl. cost & savings columns."""
    from flask import Response
    period = request.args.get("period", "day")
    limit  = int(request.args.get("limit", "366"))
    cc = _cost_cfg()
    rows = db.energy_buckets(period, limit) if db else []
    out = ["Period,Solar kWh,Load kWh,Grid In kWh,Grid Out kWh,Battery Charged kWh,"
           "Battery Discharged kWh,Grid Cost,Solar Savings,Export Earnings,Net"]
    for b in rows:
        pv = b.get("pv_kwh",0) or 0; ld = b.get("load_kwh",0) or 0
        gi = b.get("grid_in_kwh",0) or 0; go = b.get("grid_out_kwh",0) or 0
        bi = b.get("batt_in_kwh",0) or 0; bo = b.get("batt_out_kwh",0) or 0
        grid_cost = gi * cc["import_rate"]
        savings   = max(0, ld - gi) * cc["import_rate"]   # load served by solar/battery
        export    = go * cc["export_rate"]
        net       = savings + export - 0                  # money not spent + earned
        out.append(f"{b['bucket']},{pv:.2f},{ld:.2f},{gi:.2f},{go:.2f},{bi:.2f},{bo:.2f},"
                   f"{grid_cost:.2f},{savings:.2f},{export:.2f},{net:.2f}")
    csv = "\n".join(out)
    fn = f"solar-bridge-{period}-{datetime.now():%Y%m%d}.csv"
    return Response(csv, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fn}"})

@app.route("/api/solar_forecast")
@login_required
def api_solar_forecast():
    """Today's actual PV-by-hour + the 'typical day' average (a simple forecast)."""
    if not db:
        return jsonify({"hours": list(range(24)), "today": [], "typical": []})
    days = int(request.args.get("days", "7"))
    typical = db.pv_profile(days)
    today   = db.pv_today_profile()
    hours = list(range(24))
    return jsonify({
        "hours":   hours,
        "today":   [today.get(h) for h in hours],
        "typical": [typical.get(h) for h in hours],
    })

# ── Backup / Restore ───────────────────────────────────────────────────────────
BACKUP_FILES = ["config.ini", "automation.json", "energy.json"]

@app.route("/api/backup")
@login_required
def api_backup():
    """Download a .zip of settings (config + automation + energy; optionally history)."""
    include_history = request.args.get("history") == "1"
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for name in BACKUP_FILES:
            p = BASE.parent / name
            if p.exists():
                z.write(p, name)
        if include_history:
            dbp = BASE.parent / "solar_bridge.db"
            if dbp.exists():
                z.write(dbp, "solar_bridge.db")
        z.writestr("MANIFEST.txt",
                   f"Solar Bridge backup\ncreated: {datetime.now().isoformat()}\n"
                   f"history_included: {include_history}\n")
    mem.seek(0)
    fn = f"solar-bridge-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"
    return send_file(mem, mimetype="application/zip", as_attachment=True, download_name=fn)

@app.route("/api/restore", methods=["POST"])
@login_required
def api_restore():
    """Restore settings from an uploaded backup .zip, then restart the bridge."""
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    allowed = set(BACKUP_FILES) | {"solar_bridge.db"}
    try:
        with zipfile.ZipFile(io.BytesIO(f.read())) as z:
            restored = []
            for name in z.namelist():
                base = os.path.basename(name)        # prevent path traversal
                if base in allowed:
                    with z.open(name) as src:
                        (BASE.parent / base).write_bytes(src.read())
                    restored.append(base)
        if not restored:
            return jsonify({"ok": False, "error": "No recognised files in backup"}), 400
        notify_change("Settings restored from backup: " + ", ".join(restored))
        try:
            subprocess.run(["sudo", "systemctl", "restart", "solar-bridge"], timeout=15)
        except Exception:
            pass
        return jsonify({"ok": True, "restored": restored})
    except zipfile.BadZipFile:
        return jsonify({"ok": False, "error": "Invalid backup file (not a .zip)"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── Local backups (auto + manual) ─────────────────────────────────────────────
BACKUP_DIR = BASE.parent / "backups"

@app.route("/api/backups")
@login_required
def api_backups_list():
    items = []
    if BACKUP_DIR.exists():
        for p in sorted(BACKUP_DIR.glob("solar-*.zip"), reverse=True):
            items.append({"name": p.name, "size": p.stat().st_size,
                          "ts": int(p.stat().st_mtime)})
    next_auto = ""
    try:
        r = subprocess.run(["systemctl", "show", "solar-backup.timer",
                            "--property=NextElapseUSecRealtime", "--value"],
                           capture_output=True, text=True, timeout=5)
        val = r.stdout.strip()
        if val and val not in ("n/a", "0"):
            next_auto = val
    except Exception:
        pass
    return jsonify({"backups": items[:30], "next_auto": next_auto})

@app.route("/api/backups/create", methods=["POST"])
@login_required
def api_backups_create():
    body = request.json or {}
    cloud = bool(body.get("cloud"))
    try:
        import backup_manager
        p = backup_manager.create_backup(include_history=bool(body.get("history", True)))
        msg = f"Created {p.name} ({p.stat().st_size // 1024} KB)"
        if cloud:
            cz = backup_manager.create_settings_zip()
            ok, detail = backup_manager.send_telegram(cz)
            msg += " · Telegram: " + ("sent ✓" if ok else detail)
        backup_manager.rotate()
        notify_change(f"Backup created: {p.name}" + (" + cloud copy" if cloud else ""))
        return jsonify({"ok": True, "msg": msg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/api/backups/download/<name>")
@login_required
def api_backups_download(name):
    name = os.path.basename(name)
    p = BACKUP_DIR / name
    if not (p.exists() and name.startswith("solar-") and name.endswith(".zip")):
        return jsonify({"error": "not found"}), 404
    return send_file(p, as_attachment=True)

@app.route("/api/backups/delete", methods=["POST"])
@login_required
def api_backups_delete():
    name = os.path.basename((request.json or {}).get("name", ""))
    p = BACKUP_DIR / name
    if p.exists() and name.startswith("solar-") and name.endswith(".zip"):
        p.unlink()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "not found"}), 404

# ── Tailscale (remote access) status + simple controls ───────────────────────
@app.route("/api/tailscale")
@login_required
def api_tailscale():
    info = {"installed": False, "running": False, "ip": "", "auth_url": "",
            "dns_name": "", "url": "", "state": ""}
    try:
        r = subprocess.run(["tailscale", "ip", "-4"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            info.update(installed=True, running=True,
                        ip=r.stdout.strip().splitlines()[0])
        else:
            info["installed"] = True
            # logged out / stopped — see if a login URL is pending
            r2 = subprocess.run(["tailscale", "status"],
                                capture_output=True, text=True, timeout=5)
            for line in (r2.stdout + r2.stderr).splitlines():
                if "https://login.tailscale.com" in line:
                    info["auth_url"] = line.strip().split()[-1]
        # MagicDNS name + backend state from the JSON status
        r3 = subprocess.run(["tailscale", "status", "--json"],
                            capture_output=True, text=True, timeout=5)
        if r3.returncode == 0 and r3.stdout.strip():
            d = json.loads(r3.stdout)
            info["state"] = d.get("BackendState", "")
            dns = (d.get("Self") or {}).get("DNSName", "").rstrip(".")
            if dns:
                info["dns_name"] = dns
                info["url"] = f"http://{dns}/"
            info["running"] = info["state"] == "Running"
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return jsonify(info)

@app.route("/api/tailscale/toggle", methods=["POST"])
@login_required
def api_tailscale_toggle():
    """Turn Tailscale on/off (the device stays authorised either way)."""
    action = (request.json or {}).get("action", "")
    if action not in ("up", "down"):
        return jsonify({"ok": False, "msg": "unknown action"}), 400
    try:
        r = subprocess.run(["tailscale", action],
                           capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        if ok:
            notify_change(f"Tailscale remote access turned *{('ON' if action == 'up' else 'OFF')}*")
        return jsonify({"ok": ok, "msg": (r.stdout + r.stderr).strip()[:300] or action})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

# ── SocketIO events ──────────────────────────────────────────────────────────
@socketio.on("connect")
def on_connect():
    socketio.emit("full_state", state)

if __name__ == "__main__":
    load_cfg()
    threading.Thread(target=start_mqtt, daemon=True).start()
    time.sleep(1)
    socketio.run(app, host="0.0.0.0", port=8080, debug=False, allow_unsafe_werkzeug=True)
