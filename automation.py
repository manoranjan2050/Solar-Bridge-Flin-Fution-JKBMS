#!/usr/bin/env python3
"""
Solar Bridge — automation engine.

Evaluates user-defined rules every poll cycle and fires inverter control
commands.  Rules are stored as JSON in automation.json next to this file and
are editable from the dashboard.

Rule schema
-----------
Threshold rule (fires when a sensor crosses a value):
    {
      "id": "abc123", "name": "Charge from grid at night",
      "enabled": true, "type": "threshold",
      "sensor": "bank_battery_soc", "op": "<", "value": 30,
      "action_key": "charger_priority", "action_value": "Solar+Grid",
      "cooldown": 600
    }

Time rule (fires once a day at HH:MM):
    {
      "id": "def456", "name": "Solar only after sunrise",
      "enabled": true, "type": "time", "at": "07:00",
      "days": [0,1,2,3,4,5,6],
      "action_key": "charger_priority", "action_value": "Solar only"
    }

`action_key` matches the dashboard/MQTT control keys:
    output_priority, charger_priority, max_charge_current,
    max_grid_charge_current, battery_float_voltage, battery_bulk_voltage,
    battery_shutdown_voltage, battery_recharge_voltage
"""

import json, time, logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("automation")

RULES_PATH = Path(__file__).parent / "automation.json"


def load_rules() -> list:
    try:
        if RULES_PATH.exists():
            return json.loads(RULES_PATH.read_text())
    except Exception as e:
        log.warning("Could not load rules: %s", e)
    return []


def save_rules(rules: list):
    RULES_PATH.write_text(json.dumps(rules, indent=2))


class AutomationEngine:
    """Evaluates rules and dispatches actions via a callback."""

    def __init__(self, action_cb):
        # action_cb(action_key:str, action_value:str) -> bool
        self.action_cb = action_cb
        self.rules = load_rules()
        self._last_fired = {}        # rule_id → timestamp (threshold cooldown)
        self._fired_today = {}       # rule_id → date string (time rules, once/day)

    def reload(self):
        self.rules = load_rules()
        log.info("Automation rules reloaded (%d)", len(self.rules))

    def _fire(self, rule):
        key = rule.get("action_key")
        val = rule.get("action_value")
        if not key:
            return
        log.info("Automation '%s' → set %s = %s", rule.get("name", rule.get("id")), key, val)
        try:
            self.action_cb(key, str(val))
        except Exception as e:
            log.warning("Automation action failed: %s", e)

    def evaluate(self, state: dict):
        now = time.time()
        nowdt = datetime.now()
        today = nowdt.date().isoformat()

        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            rid = rule.get("id") or rule.get("name", "")
            rtype = rule.get("type", "threshold")

            if rtype == "threshold":
                sensor = rule.get("sensor")
                val = state.get(sensor)
                if val is None:
                    continue
                try:
                    target = float(rule.get("value"))
                    cur = float(val)
                except (TypeError, ValueError):
                    continue
                op = rule.get("op", "<")
                hit = (cur < target) if op == "<" else \
                      (cur > target) if op == ">" else \
                      (cur == target) if op == "==" else False
                if not hit:
                    continue
                cooldown = float(rule.get("cooldown", 600))
                last = self._last_fired.get(rid, 0)
                if now - last < cooldown:
                    continue
                self._last_fired[rid] = now
                self._fire(rule)

            elif rtype == "time":
                at = rule.get("at", "")
                days = rule.get("days", list(range(7)))
                if nowdt.weekday() not in days:
                    continue
                hhmm = nowdt.strftime("%H:%M")
                if hhmm != at:
                    continue
                if self._fired_today.get(rid) == today:
                    continue
                self._fired_today[rid] = today
                self._fire(rule)
