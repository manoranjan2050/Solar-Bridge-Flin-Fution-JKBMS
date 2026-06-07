#!/usr/bin/env python3
"""
Solar Bridge — Notifications & alert engine.

Dispatches alerts to Telegram, e-mail and Home Assistant (via MQTT), stores
them in the SQLite DB, and runs a Telegram command bot (/status /pv /battery
/today /help).  All channels are optional and driven by config.ini.
"""

import smtplib, threading, time, json, logging
from email.mime.text import MIMEText

try:
    import requests
except ImportError:          # bot/email still degrade gracefully
    requests = None

log = logging.getLogger("notifier")

try:
    from solar_db import db
except Exception:            # DB optional
    db = None


def _cfg(cfg, section, key, default=""):
    try:
        return cfg.get(section, key, fallback=default).split("#")[0].strip()
    except Exception:
        return default

def _cfg_bool(cfg, section, key, default=False):
    return _cfg(cfg, section, key, str(default)).lower() in ("1", "true", "yes", "on")

def _cfg_num(cfg, section, key, default=0.0):
    try:
        return float(_cfg(cfg, section, key, str(default)))
    except (ValueError, TypeError):
        return default


class Notifier:
    """Sends alerts and runs the Telegram command bot."""

    def __init__(self, cfg, mqtt_client=None, state_getter=None, control_cb=None):
        self.cfg = cfg
        self.mqtt = mqtt_client
        self.state_getter = state_getter or (lambda: {})
        # control_cb(ctrl_key:str, value:str) -> bool   (lets the bot drive the inverter)
        self.control_cb = control_cb

        # Telegram
        self.tg_enabled = _cfg_bool(cfg, "telegram", "enabled")
        self.tg_token   = _cfg(cfg, "telegram", "token")
        self.tg_chat    = _cfg(cfg, "telegram", "chat_id")
        self.daily_summary = _cfg_bool(cfg, "telegram", "daily_summary", False)
        self.summary_time  = _cfg(cfg, "telegram", "summary_time", "21:00")

        # E-mail
        self.em_enabled = _cfg_bool(cfg, "email", "enabled")
        self.em_host    = _cfg(cfg, "email", "smtp_host", "smtp.gmail.com")
        self.em_port    = int(_cfg_num(cfg, "email", "smtp_port", 587))
        self.em_user    = _cfg(cfg, "email", "username")
        self.em_pass    = _cfg(cfg, "email", "password")
        self.em_from    = _cfg(cfg, "email", "from_addr") or self.em_user
        self.em_to      = [a.strip() for a in _cfg(cfg, "email", "to_addr").split(",") if a.strip()]

        # Alert thresholds
        self.al_enabled  = _cfg_bool(cfg, "alerts", "enabled", True)
        self.low_soc     = _cfg_num(cfg, "alerts", "low_soc", 20)
        self.crit_soc    = _cfg_num(cfg, "alerts", "critical_soc", 10)
        self.high_bt     = _cfg_num(cfg, "alerts", "high_battery_temp", 50)
        self.high_it     = _cfg_num(cfg, "alerts", "high_inverter_temp", 75)
        self.high_cdiff  = _cfg_num(cfg, "alerts", "high_cell_diff", 0.1)
        self.overload    = _cfg_num(cfg, "alerts", "overload_pct", 90)
        self.high_bv     = _cfg_num(cfg, "alerts", "high_battery_voltage", 57.0)
        self.cell_ov     = _cfg_num(cfg, "alerts", "cell_overvoltage", 3.65)
        self.on_grid     = _cfg_bool(cfg, "alerts", "notify_on_grid_loss", True)
        self.on_fault    = _cfg_bool(cfg, "alerts", "notify_on_fault", True)
        self.on_full     = _cfg_bool(cfg, "alerts", "notify_on_full", True)
        self.on_overload = _cfg_bool(cfg, "alerts", "notify_on_overload", True)
        self.cooldown    = _cfg_num(cfg, "alerts", "cooldown", 1800)

        self._active   = {}      # condition_key → fired timestamp (for cooldown + edge detection)
        self._tg_offset = 0
        self._stop = False

    # ── Sending ────────────────────────────────────────────────────────────
    def send(self, level: str, message: str):
        """Dispatch an alert to every enabled channel + DB + MQTT."""
        log.info("ALERT [%s] %s", level, message)
        if db:
            db.add_alert(level, message)
        if self.mqtt:
            try:
                self.mqtt.publish("solar/alert",
                                  json.dumps({"level": level, "message": message,
                                              "ts": time.time()}))
            except Exception as e:
                log.warning("MQTT alert publish failed: %s", e)
        icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}.get(level, "•")
        self._tg_send(f"{icon} *{level.upper()}*\n{message}")
        self._email_send(f"Solar Alert [{level}]", message)

    def send_daily_summary(self, peak_pv=None, min_soc=None):
        """Send a once-a-day digest of today's energy + extremes to Telegram."""
        if not (self.tg_enabled and self.tg_token):
            return
        te = db.get_today_energy() if db else {}
        s = self.state_getter() or {}
        def kwh(k): return f"{te[k]:.2f}" if k in te else "—"
        load = te.get("load_kwh", 0) or 0
        gin  = te.get("grid_in_kwh", 0) or 0
        self_suff = f"{max(0, min(100, round((1 - gin/load)*100)))}%" if load > 0 else "—"
        soc = s.get("bank_battery_soc", s.get("bms1_battery_soc", "?"))
        lines = [
            "📅 *Daily Solar Summary*",
            f"Solar: {kwh('pv_kwh')} kWh",
            f"Load: {kwh('load_kwh')} kWh",
            f"Grid in: {kwh('grid_in_kwh')} kWh  ·  out: {kwh('grid_out_kwh')} kWh",
            f"Battery charged: {kwh('batt_in_kwh')} kWh  ·  used: {kwh('batt_out_kwh')} kWh",
            f"Self-sufficiency: {self_suff}",
        ]
        if peak_pv is not None:  lines.append(f"Peak solar: {peak_pv:.0f} W")
        if min_soc is not None:  lines.append(f"Lowest SOC: {min_soc:.0f}%")
        lines.append(f"Battery now: {soc}%")
        self._tg_send("\n".join(lines))

    def _tg_send(self, text: str, chat_id=None):
        if not (self.tg_enabled and self.tg_token and requests):
            return
        chat = chat_id or self.tg_chat
        if not chat:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                json={"chat_id": chat, "text": text, "parse_mode": "Markdown"},
                timeout=10)
        except Exception as e:
            log.warning("Telegram send failed: %s", e)

    def _email_send(self, subject: str, body: str):
        if not (self.em_enabled and self.em_user and self.em_to):
            return
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"]    = self.em_from
            msg["To"]      = ", ".join(self.em_to)
            with smtplib.SMTP(self.em_host, self.em_port, timeout=20) as s:
                s.starttls()
                s.login(self.em_user, self.em_pass)
                s.sendmail(self.em_from, self.em_to, msg.as_string())
        except Exception as e:
            log.warning("E-mail send failed: %s", e)

    # ── Alert engine ─────────────────────────────────────────────────────────
    def _edge(self, key: str, condition: bool, level: str, message: str,
              recover_msg: str = None):
        """
        Fire an alert on the rising edge of `condition`, respecting cooldown.
        Emits an info recovery message on the falling edge when given.
        """
        now = time.time()
        last = self._active.get(key)
        if condition:
            if last is None or (now - last) > self.cooldown:
                self.send(level, message)
                self._active[key] = now
        else:
            if last is not None:
                if recover_msg:
                    self.send("info", recover_msg)
                self._active.pop(key, None)

    def check(self, state: dict):
        """Evaluate all alert conditions against the latest flat state dict."""
        if not self.al_enabled:
            return
        g = lambda k: state.get(k)

        soc = g("bank_battery_soc")
        if soc is None:
            soc = g("bms1_battery_soc")
        if soc is not None:
            self._edge("crit_soc", soc <= self.crit_soc, "critical",
                       f"Battery critically low: {soc:.0f}% (≤{self.crit_soc:.0f}%)",
                       f"Battery recovered above critical: {soc:.0f}%")
            self._edge("low_soc", self.crit_soc < soc <= self.low_soc, "warning",
                       f"Battery low: {soc:.0f}% (≤{self.low_soc:.0f}%)",
                       f"Battery recovered: {soc:.0f}%")

        for tk in ("bank_temp_mos", "bank_temp_battery_1", "bms1_temp_mos", "bms2_temp_mos"):
            t = g(tk)
            if t is not None:
                self._edge(f"bt_{tk}", t >= self.high_bt, "warning",
                           f"High battery temperature ({tk}): {t:.1f}°C",
                           f"Battery temperature normal ({tk}): {t:.1f}°C")

        it = g("inverter_inverter_heatsink_temp")
        if it is not None:
            self._edge("inv_temp", it >= self.high_it, "warning",
                       f"High inverter temperature: {it:.1f}°C",
                       f"Inverter temperature normal: {it:.1f}°C")

        cd = g("bank_cell_voltage_diff")
        if cd is None:
            cd = g("bms1_cell_voltage_diff")
        if cd is not None:
            self._edge("cell_diff", cd >= self.high_cdiff, "warning",
                       f"Cell imbalance high: {cd:.3f}V",
                       f"Cell imbalance recovered: {cd:.3f}V")

        # Battery over-voltage (pack)
        bv = g("bank_battery_voltage") or g("inverter_battery_voltage")
        if bv is not None:
            self._edge("high_bv", bv >= self.high_bv, "warning",
                       f"Battery over-voltage: {bv:.1f}V (≥{self.high_bv:.1f}V)",
                       f"Battery voltage normal: {bv:.1f}V")

        # Cell over-voltage (highest cell)
        cmax = g("bank_cell_voltage_max") or g("bms1_cell_voltage_max")
        if cmax is not None:
            self._edge("cell_ov", cmax >= self.cell_ov, "warning",
                       f"Cell over-voltage: {cmax:.3f}V (≥{self.cell_ov:.3f}V)",
                       f"Cell voltage normal: {cmax:.3f}V")

        # Inverter overload
        if self.on_overload:
            lp = g("inverter_load_percent")
            if lp is not None:
                self._edge("overload", lp >= self.overload, "warning",
                           f"Inverter overload: load at {lp:.0f}% (≥{self.overload:.0f}%)",
                           f"Load back to normal: {lp:.0f}%")

        # Battery fully charged (info, edge-triggered)
        if self.on_full and soc is not None:
            self._edge("full", soc >= 100, "info",
                       "🔋 Battery fully charged (100%)", None)

        if self.on_grid:
            gv = g("inverter_grid_voltage")
            if gv is not None:
                self._edge("grid_loss", gv < 50, "warning",
                           "⚡ Grid power lost (running on solar/battery)",
                           "⚡ Grid power restored")

        if self.on_fault:
            fault = g("inverter_fault_text")
            self._edge("fault", bool(fault) and fault not in ("", "None", "OK"),
                       "critical", f"Inverter fault/warning: {fault}",
                       "Inverter fault cleared")

    # ── Telegram command bot ─────────────────────────────────────────────────
    def start_bot(self):
        if not (self.tg_enabled and self.tg_token and self.tg_chat and requests):
            log.info("Telegram bot not started (disabled or missing token/chat_id)")
            return
        threading.Thread(target=self._bot_loop, daemon=True).start()
        log.info("Telegram bot started")

    def stop(self):
        self._stop = True

    def _bot_loop(self):
        # Drop backlog so we only react to new messages
        try:
            r = requests.get(f"https://api.telegram.org/bot{self.tg_token}/getUpdates",
                             params={"timeout": 0}, timeout=10).json()
            if r.get("result"):
                self._tg_offset = r["result"][-1]["update_id"] + 1
        except Exception:
            pass

        while not self._stop:
            try:
                r = requests.get(
                    f"https://api.telegram.org/bot{self.tg_token}/getUpdates",
                    params={"timeout": 25, "offset": self._tg_offset}, timeout=30).json()
            except Exception as e:
                log.debug("getUpdates failed: %s", e)
                time.sleep(5)
                continue
            for upd in r.get("result", []):
                self._tg_offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat = str(msg.get("chat", {}).get("id", ""))
                text = (msg.get("text") or "").strip().lower()
                if not text:
                    continue
                if self.tg_chat and chat != str(self.tg_chat):
                    continue                      # ignore strangers
                self._handle_cmd(text, chat)

    # control command → (control_key, value mapper)
    CONTROL_CMDS = {
        "output":     ("output_priority",
                       {"grid": "Grid first", "solar": "Solar first", "sbu": "SBU"}),
        "charger":    ("charger_priority",
                       {"grid": "Grid first", "solar": "Solar first",
                        "solargrid": "Solar+Grid", "solar+grid": "Solar+Grid",
                        "solaronly": "Solar only"}),
        "maxcharge":  ("max_charge_current", None),       # numeric
        "gridcharge": ("max_grid_charge_current", None),  # numeric
        "float":      ("battery_float_voltage", None),    # numeric
        "bulk":       ("battery_bulk_voltage", None),     # numeric
    }

    def _handle_cmd(self, text: str, chat: str):
        s = self.state_getter() or {}
        g = lambda k, d="?": s.get(k, d)
        parts = text.split()
        cmd = parts[0].lstrip("/")
        arg = parts[1].lower() if len(parts) > 1 else ""

        # ── Control commands ──────────────────────────────────────────────
        if cmd in self.CONTROL_CMDS:
            if not self.control_cb:
                self._tg_send("⚠️ Inverter control is not available.", chat_id=chat)
                return
            ctrl_key, mapping = self.CONTROL_CMDS[cmd]
            if not arg:
                opts = "/".join(mapping.keys()) if mapping else "<number>"
                self._tg_send(f"Usage: /{cmd} {opts}", chat_id=chat)
                return
            value = mapping.get(arg) if mapping else arg
            if value is None:
                self._tg_send(f"Unknown option '{arg}'. Try: " +
                              "/".join(mapping.keys()), chat_id=chat)
                return
            try:
                ok = self.control_cb(ctrl_key, str(value))
            except Exception as e:
                ok = False; log.warning("bot control failed: %s", e)
            self._tg_send((f"✅ Set *{ctrl_key.replace('_',' ')}* = *{value}*"
                           if ok else f"❌ Failed to set {ctrl_key} (inverter NAK)"),
                          chat_id=chat)
            return

        # ── Status / info commands ────────────────────────────────────────
        if cmd in ("status", "start"):
            soc = g("bank_battery_soc", g("bms1_battery_soc", "?"))
            reply = (f"☀️ *Solar Status*\n"
                     f"PV: {g('inverter_pv_power')} W\n"
                     f"Load: {g('inverter_ac_out_active_power')} W\n"
                     f"Grid: {g('inverter_grid_power')} W\n"
                     f"Battery: {g('inverter_battery_voltage')} V "
                     f"({g('inverter_battery_current')} A)\n"
                     f"SOC: {soc} %\n"
                     f"Mode: {g('inverter_device_mode')}")
        elif cmd == "info":
            reply = self._full_info(g)
        elif cmd == "pv":
            reply = (f"☀️ *Solar PV*\n"
                     f"Power: {g('inverter_pv_power')} W\n"
                     f"Voltage: {g('inverter_pv_input_voltage')} V\n"
                     f"Current: {g('inverter_pv_input_current')} A")
        elif cmd == "battery":
            soc = g("bank_battery_soc", g("bms1_battery_soc", "?"))
            reply = (f"🔋 *Battery*\n"
                     f"SOC: {soc} %\n"
                     f"Voltage: {g('bank_battery_voltage', g('inverter_battery_voltage'))} V\n"
                     f"Current: {g('inverter_battery_current')} A\n"
                     f"Temp MOS: {g('bank_temp_mos')} °C\n"
                     f"Cell diff: {g('bank_cell_voltage_diff', g('bms1_cell_voltage_diff'))} V")
        elif cmd == "today":
            te = db.get_today_energy() if db else {}
            if te:
                reply = "📊 *Today's Energy*\n" + "\n".join(
                    f"{k.replace('_kwh','').replace('_',' ').title()}: {v:.2f} kWh"
                    for k, v in sorted(te.items()))
            else:
                reply = "📊 No energy data recorded yet today."
        else:
            reply = ("🤖 *Solar Bridge Bot*\n\n"
                     "*Status*\n"
                     "/info — full inverter + battery status\n"
                     "/status — quick overview\n"
                     "/pv — solar production\n"
                     "/battery — battery details\n"
                     "/today — today's energy\n\n"
                     "*Control*\n"
                     "/output grid|solar|sbu\n"
                     "/charger grid|solar|solargrid|solaronly\n"
                     "/maxcharge <A>\n"
                     "/gridcharge <A>\n"
                     "/float <V>\n"
                     "/bulk <V>")
        self._tg_send(reply, chat_id=chat)

    def _full_info(self, g) -> str:
        soc = g("bank_battery_soc", g("bms1_battery_soc", "?"))
        te = db.get_today_energy() if db else {}
        def kwh(k): return f"{te[k]:.2f}" if k in te else "—"
        return (
            "📋 *Full System Status*\n"
            f"Mode: *{g('inverter_device_mode')}*  |  Fault: {g('inverter_fault_text','OK') or 'OK'}\n"
            "\n☀️ *Solar*\n"
            f"PV: {g('inverter_pv_power')} W  ({g('inverter_pv_input_voltage')}V / {g('inverter_pv_input_current')}A)\n"
            "\n🏠 *Load / AC out*\n"
            f"Load: {g('inverter_ac_out_active_power')} W  ({g('inverter_load_percent')}%)\n"
            f"Output: {g('inverter_ac_out_voltage')}V  {g('inverter_ac_out_frequency')}Hz\n"
            "\n⚡ *Grid*\n"
            f"Grid: {g('inverter_grid_power')} W  ({g('inverter_grid_voltage')}V {g('inverter_grid_frequency')}Hz)\n"
            "\n🔋 *Battery*\n"
            f"SOC: {soc}%   {g('bank_battery_voltage', g('inverter_battery_voltage'))}V  {g('inverter_battery_current')}A\n"
            f"BMS1: {g('bms1_battery_soc')}% {g('bms1_battery_voltage')}V  |  BMS2: {g('bms2_battery_soc')}% {g('bms2_battery_voltage')}V\n"
            f"Cells: min {g('bank_cell_voltage_min', g('bms1_cell_voltage_min'))}V  max {g('bank_cell_voltage_max', g('bms1_cell_voltage_max'))}V  diff {g('bank_cell_voltage_diff', g('bms1_cell_voltage_diff'))}V\n"
            f"Temp: MOS {g('bank_temp_mos')}°C  T1 {g('bank_temp_battery_1')}°C\n"
            f"Remaining: {g('bank_total_remaining_capacity')} Ah  |  SOH: {g('bank_state_of_health')}%\n"
            "\n📊 *Today*\n"
            f"Solar {kwh('pv_kwh')}  Load {kwh('load_kwh')}  Grid {kwh('grid_in_kwh')} kWh\n"
            "\n⚙️ *Settings*\n"
            f"Output: {g('inverter_control_output_priority', g('inverter_output_source_priority'))}  |  "
            f"Charger: {g('inverter_control_charger_priority', g('inverter_charger_source_priority'))}\n"
            f"Max chg: {g('inverter_max_charge_current')}A  Grid chg: {g('inverter_max_ac_charge_current')}A\n"
            f"Float: {g('inverter_battery_float_voltage')}V  Bulk: {g('inverter_battery_bulk_voltage')}V"
        )
