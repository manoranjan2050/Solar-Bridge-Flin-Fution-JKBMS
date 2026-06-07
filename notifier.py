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

    def __init__(self, cfg, mqtt_client=None, state_getter=None):
        self.cfg = cfg
        self.mqtt = mqtt_client
        self.state_getter = state_getter or (lambda: {})

        # Telegram
        self.tg_enabled = _cfg_bool(cfg, "telegram", "enabled")
        self.tg_token   = _cfg(cfg, "telegram", "token")
        self.tg_chat    = _cfg(cfg, "telegram", "chat_id")

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
        self.on_grid     = _cfg_bool(cfg, "alerts", "notify_on_grid_loss", True)
        self.on_fault    = _cfg_bool(cfg, "alerts", "notify_on_fault", True)
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

        if self.on_grid:
            gv = g("inverter_grid_voltage")
            if gv is not None:
                self._edge("grid_loss", gv < 50, "warning",
                           "Grid power lost (no AC input voltage)",
                           "Grid power restored")

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

    def _handle_cmd(self, text: str, chat: str):
        s = self.state_getter() or {}
        g = lambda k, d="?": s.get(k, d)
        cmd = text.split()[0].lstrip("/")

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
            reply = ("🤖 *Solar Bridge Bot*\n"
                     "/status — full overview\n"
                     "/pv — solar production\n"
                     "/battery — battery details\n"
                     "/today — today's energy")
        self._tg_send(reply, chat_id=chat)
