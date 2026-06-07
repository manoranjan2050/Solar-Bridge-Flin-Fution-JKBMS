#!/usr/bin/env python3
"""
Solar Bridge — Flin Fution (Voltronic/Axpert) + JKBMS → MQTT → Home Assistant
Sensors: all Solar Assistant equivalents  |  Controls: inverter settings via HA
"""

import configparser, json, logging, os, select, serial, struct, threading, time
from pathlib import Path
from datetime import date
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("solar_bridge")

# Optional add-on modules — degrade gracefully if missing
try:
    from solar_db import db
except Exception as e:                       # pragma: no cover
    db = None
    logging.getLogger("solar_bridge").warning("solar_db unavailable: %s", e)
try:
    from notifier import Notifier
except Exception:                            # pragma: no cover
    Notifier = None
try:
    from automation import AutomationEngine
except Exception:                            # pragma: no cover
    AutomationEngine = None

CONFIG_PATH  = Path(__file__).parent / "config.ini"
ENERGY_PATH  = Path(__file__).parent / "energy.json"
LIVE_PATH    = Path(__file__).parent / "live_state.json"   # latest values for the dashboard (MQTT-independent)
CONTROL_QUEUE = Path(__file__).parent / "control_queue.json"  # dashboard → bridge commands (MQTT-independent)
INSTALL_DIR  = Path(__file__).parent

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(CONFIG_PATH)
    return cfg

def cfg_str(cfg, section, key, default=""):
    return cfg.get(section, key, fallback=default).split("#")[0].strip()

def cfg_int(cfg, section, key, default=0):
    return int(cfg_str(cfg, section, key, str(default)))

# ---------------------------------------------------------------------------
# Energy tracker  (persists kWh totals across restarts)
# ---------------------------------------------------------------------------

class EnergyTracker:
    KEYS = ["pv_kwh", "batt_in_kwh", "batt_out_kwh",
            "load_kwh", "grid_in_kwh", "grid_out_kwh"]

    def __init__(self):
        self._data = {k: 0.0 for k in self.KEYS}
        self._baseline = {k: 0.0 for k in self.KEYS}   # lifetime value at start of today
        self._day = date.today().isoformat()
        self._last_save = time.time()
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            if ENERGY_PATH.exists():
                saved = json.loads(ENERGY_PATH.read_text())
                for k in self.KEYS:
                    self._data[k] = float(saved.get(k, 0))
                    self._baseline[k] = float(saved.get("baseline", {}).get(k, self._data[k]))
                self._day = saved.get("day", self._day)
                log.info("Energy loaded: %s", {k: round(v,2) for k,v in self._data.items()})
        except Exception as e:
            log.warning("Energy load failed: %s", e)

    def _save(self):
        try:
            ENERGY_PATH.write_text(json.dumps({
                **{k: round(v, 4) for k, v in self._data.items()},
                "baseline": {k: round(v, 4) for k, v in self._baseline.items()},
                "day": self._day,
            }))
        except Exception as e:
            log.warning("Energy save failed: %s", e)

    def add(self, key, power_w, interval_s):
        """Add power_w × interval_s to a cumulative kWh counter."""
        with self._lock:
            self._data[key] += (power_w * interval_s) / 3_600_000
            if time.time() - self._last_save > 60:
                self._save()
                self._last_save = time.time()

    def get(self, key):
        return round(self._data.get(key, 0), 3)

    def today(self, key):
        """kWh accumulated since the start of the current day."""
        return round(max(0.0, self._data.get(key, 0) - self._baseline.get(key, 0)), 3)

    def today_all(self):
        return {k: self.today(k) for k in self.KEYS}

    def rollover_if_new_day(self):
        """
        At midnight: persist the finished day's totals to the DB, then reset
        the daily baseline so today() starts from zero again.
        Returns True if a rollover happened.
        """
        today = date.today().isoformat()
        if today == self._day:
            return False
        with self._lock:
            finished = self._day
            if db:
                for k in self.KEYS:
                    db.update_daily_energy_for(finished, f"{k}",
                                               round(self._data[k] - self._baseline[k], 4))
            for k in self.KEYS:
                self._baseline[k] = self._data[k]
            self._day = today
            self._save()
        log.info("Daily energy rollover: %s finalised, new day %s", finished, today)
        return True

    def push_daily(self):
        """Upsert today's running totals into the DB (called each minute)."""
        if not db:
            return
        for k in self.KEYS:
            db.update_daily_energy(k, self.today(k))

# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------

_inverter_ref = None          # set after inverter is created
_cmd_lock = threading.Lock()  # serialise HID access between poll and command

CTRL_TOPIC = "solar/inverter/control"

def mqtt_connect(cfg, on_message_cb):
    host     = cfg_str(cfg, "mqtt", "host")
    port     = cfg_int(cfg, "mqtt", "port", 1883)
    user     = cfg_str(cfg, "mqtt", "username")
    password = cfg_str(cfg, "mqtt", "password")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="solar_bridge", clean_session=True)
    if user:
        client.username_pw_set(user, password or None)

    def _on_connect(cl, userdata, flags, rc, props=None):
        code = getattr(rc, "value", rc)
        if code == 0:
            log.info("MQTT connected to %s:%s as %r", host, port, user or "(anonymous)")
            cl.subscribe(f"{CTRL_TOPIC}/+/set")
            cl.publish("solar/bridge/status", "online", retain=True)
        else:
            log.error("MQTT CONNECTION REFUSED by %s:%s (rc=%s, %s). "
                      "Check [mqtt] username/password in config.ini!",
                      host, port, code, rc)

    def _on_disconnect(cl, userdata, *args):
        log.warning("MQTT disconnected from %s:%s (will retry)", host, port)

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = on_message_cb
    client.will_set("solar/bridge/status", "offline", retain=True)
    client.reconnect_delay_set(min_delay=5, max_delay=60)
    client.connect(host, port, keepalive=60)
    client.loop_start()
    log.info("MQTT connecting to %s:%s ...", host, port)
    return client


def ha_disc(client, uid, name, state_topic, unit=None,
            device_class=None, state_class=None, icon=None,
            device_info=None, entity_type="sensor", extra=None):
    disc = f"homeassistant/{entity_type}/{uid}/config"
    payload = {
        "name":               name,
        "unique_id":          uid,
        "state_topic":        state_topic,
        "device":             device_info,
        "availability_topic": "solar/bridge/status",
        "payload_available":  "online",
        "payload_not_available": "offline",
    }
    if unit:          payload["unit_of_measurement"] = unit
    if device_class:  payload["device_class"]        = device_class
    if state_class:   payload["state_class"]          = state_class
    if icon:          payload["icon"]                 = icon
    if extra:         payload.update(extra)
    payload = {k: v for k, v in payload.items() if v is not None}
    client.publish(disc, json.dumps(payload), retain=True)


def pub(client, topic, value):
    if value is None:
        return
    client.publish(topic, str(round(value, 3) if isinstance(value, float) else value))

# ---------------------------------------------------------------------------
# Voltronic / Axpert inverter
# ---------------------------------------------------------------------------

class VoltronicInverter:
    QPIGS_FIELDS = [
        # (key, unit, device_class, state_class)
        ("grid_voltage",             "V",   "voltage",        "measurement"),
        ("grid_frequency",           "Hz",  "frequency",      "measurement"),
        ("ac_out_voltage",           "V",   "voltage",        "measurement"),
        ("ac_out_frequency",         "Hz",  "frequency",      "measurement"),
        ("ac_out_apparent_power",    "VA",  "apparent_power", "measurement"),
        ("ac_out_active_power",      "W",   "power",          "measurement"),
        ("load_percent",             "%",   None,             "measurement"),
        ("bus_voltage",              "V",   "voltage",        "measurement"),
        ("battery_voltage",          "V",   "voltage",        "measurement"),
        ("battery_charge_current",   "A",   "current",        "measurement"),
        ("battery_capacity",         "%",   "battery",        "measurement"),
        ("inverter_heatsink_temp",   "°C",  "temperature",    "measurement"),
        ("pv_input_current",         "A",   "current",        "measurement"),
        ("pv_input_voltage",         "V",   "voltage",        "measurement"),
        ("battery_scc_voltage",      "V",   "voltage",        "measurement"),
        ("battery_discharge_current","A",   "current",        "measurement"),
    ]

    # Computed / derived sensors published alongside QPIGS
    DERIVED = [
        ("pv_power",         "W",   "power",   "measurement"),
        ("battery_power",    "W",   "power",   "measurement"),
        ("grid_power",       "W",   "power",   "measurement"),
        ("battery_current",  "A",   "current", "measurement"),
    ]

    # QPIRI field positions — confirmed from live response:
    # 0=220.0 | 1=22.7 | 2=220.0 | 3=50.0 | 4=22.7 | 5=5000 | 6=5000
    # 7=48.0  | 8=46.0 | 9=48.0  | 10=55.1| 11=54.0| 12=2   | 13=20
    # 14=080  | 15=0   | 16=0    | 17=1   | 18=1   | 19=01  | 20=0
    # 21=0    | 22=54.0| 23=0    | 24=1L
    QPIRI_FIELDS = [
        (7,  "battery_cutoff_voltage",      "V"),   # shutdown / under-voltage
        (8,  "battery_back_voltage",        "V"),   # back-to-discharge (to-grid voltage)
        (10, "battery_bulk_voltage",        "V"),   # absorption / bulk charge
        (11, "battery_float_voltage",       "V"),   # float charge
        (13, "max_ac_charge_current",       "A"),   # max grid charge current = 20
        (14, "max_charge_current",          "A"),   # max total charge current = 80
        (15, "output_source_priority",      None),  # 0=Grid, 1=Solar, 2=SBU
        (16, "charger_source_priority",     None),  # 0=Grid, 1=Solar, 2=Solar+Grid, 3=Solar
        (22, "battery_redischarge_voltage", "V"),   # redischarge voltage
    ]

    SET_COMMANDS = {
        # key → (prefix, value_transformer)
        "output_priority": ("POPCD", lambda v: {"Grid first":"00","Solar first":"01","SBU":"02"}.get(v,"")),
        "charger_priority": ("PPCP", lambda v: {"Grid first":"00","Solar first":"01","Solar+Grid":"02","Solar only":"03"}.get(v,"")),
        "max_charge_current":      ("MUCHGC", lambda v: f"{int(float(v)):03d}"),
        "max_grid_charge_current": ("MCHGC",  lambda v: f"{int(float(v)):03d}"),
        "battery_float_voltage":   ("PBFT",   lambda v: f"{float(v):.1f}"),
        "battery_bulk_voltage":    ("PBCV",   lambda v: f"{float(v):.1f}"),
        "battery_shutdown_voltage":("PSDV",   lambda v: f"{float(v):.1f}"),
        "battery_recharge_voltage":("PBDV",   lambda v: f"{float(v):.1f}"),
    }

    def __init__(self, port, protocol="PI30"):
        self.port = port
        self.protocol = protocol
        self._fd = None
        self._is_hid = "hidraw" in port
        self._ser = None

    # ── CRC (confirmed correct for this inverter) ───────────────────────────
    @staticmethod
    def _crc(data: bytes) -> bytes:
        crc = 0
        crc_ta = [0x0000,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7,
                  0x8108,0x9129,0xa14a,0xb16b,0xc18c,0xd1ad,0xe1ce,0xf1ef]
        for d in data:
            da = ((crc >> 8) & 0xFF) >> 4
            crc = (crc << 4) & 0xFFFF; crc ^= crc_ta[da ^ (d >> 4)]
            da = ((crc >> 8) & 0xFF) >> 4
            crc = (crc << 4) & 0xFFFF; crc ^= crc_ta[da ^ (d & 0x0F)]
        bhi = (crc >> 8) & 0xFF; blo = crc & 0xFF
        if bhi in (0x28,0x0D,0x0A): bhi += 1
        if blo in (0x28,0x0D,0x0A): blo += 1
        return bytes([bhi, blo])

    def _frame(self, cmd: str) -> bytes:
        raw = cmd.encode()
        return raw + self._crc(raw) + b"\r"

    # ── HID read/write ──────────────────────────────────────────────────────
    def _hid_query(self, frame: bytes) -> str:
        for i in range(0, len(frame), 8):
            chunk = frame[i:i+8]
            os.write(self._fd, b"\x00" + chunk.ljust(8, b"\x00"))
            time.sleep(0.02)
        response = b""
        deadline = time.time() + 3.0
        while time.time() < deadline:
            r, _, _ = select.select([self._fd], [], [], 0.4)
            if not r:
                if response: break
                continue
            pkt = os.read(self._fd, 8)
            if not pkt: break
            response += pkt
            if b"\r" in response: break
        if b"\r" in response:
            response = response[:response.index(b"\r")]
        return response.rstrip(b"\x00").decode("ascii", errors="ignore").strip()

    def _serial_query(self, frame: bytes) -> str:
        self._ser.write(frame)
        response = b""
        while True:
            chunk = self._ser.read(256)
            response += chunk
            if response.endswith(b"\r") or not chunk: break
        return response.decode("ascii", errors="ignore").strip()

    def _query(self, cmd: str) -> str:
        frame = self._frame(cmd)
        if self._is_hid:
            return self._hid_query(frame)
        return self._serial_query(frame)

    # ── Open / close ────────────────────────────────────────────────────────
    def open(self):
        try:
            if self._is_hid:
                self._fd = os.open(self.port, os.O_RDWR)
            else:
                self._ser = serial.Serial(self.port, baudrate=2400, bytesize=8,
                                          parity="N", stopbits=1, timeout=3)
            log.info("Inverter opened on %s", self.port)
            return True
        except Exception as e:
            log.error("Inverter open failed: %s", e)
            return False

    def close(self):
        try:
            if self._is_hid and self._fd is not None:
                os.close(self._fd); self._fd = None
            elif self._ser:
                self._ser.close()
        except Exception: pass

    # ── Queries ─────────────────────────────────────────────────────────────
    def query_qpigs(self) -> dict | None:
        with _cmd_lock:
            raw = self._query("QPIGS")
        if not raw.startswith("(") or raw.startswith("(NAK"):
            log.warning("QPIGS bad response: %r", raw[:30])
            return self._reconnect_and_retry("QPIGS")
        parts = raw[1:].split()
        if len(parts) < 16:
            log.warning("QPIGS short: %d fields", len(parts))
            return None
        result = {}
        for i, (key, *_) in enumerate(self.QPIGS_FIELDS):
            try:   result[key] = float(parts[i])
            except: result[key] = None
        # Status flags (position 16)
        result["status_flags"] = parts[16] if len(parts) > 16 else ""
        return result

    def query_qpiri(self) -> dict | None:
        with _cmd_lock:
            raw = self._query("QPIRI")
        if not raw.startswith("(") or raw.startswith("(NAK"):
            return None
        parts = raw[1:].split()
        log.info("QPIRI raw fields: %s",
                 " | ".join(f"{i}={v}" for i, v in enumerate(parts)))
        result = {}
        for pos, key, _ in self.QPIRI_FIELDS:
            try:   result[key] = float(parts[pos]) if pos < len(parts) and "." in parts[pos] else int(parts[pos])
            except: pass
        return result if result else None

    def query_qmod(self) -> str | None:
        with _cmd_lock:
            raw = self._query("QMOD")
        if not raw.startswith("(") or raw.startswith("(NAK"):
            return None
        code = raw[1:2]
        return {"P":"Power on","S":"Standby","L":"Line/Grid",
                "B":"Battery","F":"Fault","H":"Power saving",
                "D":"Shutdown"}.get(code, f"Unknown({code})")

    def query_serial(self) -> str | None:
        with _cmd_lock:
            raw = self._query("QID")
        if raw.startswith("(") and not raw.startswith("(NAK"):
            return raw[1:].strip()
        return None

    # QPIWS warning/fault bit map (Axpert/Voltronic PI30).
    # (bit_index, label, is_fault)  — bit 0 is the leftmost character.
    QPIWS_BITS = [
        (1,  "Inverter fault",            True),
        (2,  "Bus over",                  True),
        (3,  "Bus under",                 True),
        (4,  "Bus soft fail",             True),
        (5,  "Line fail",                 False),
        (6,  "OPV short",                 True),
        (7,  "Inverter voltage too low",  True),
        (8,  "Inverter voltage too high", True),
        (9,  "Over temperature",          False),
        (10, "Fan locked",                False),
        (11, "Battery voltage high",      False),
        (12, "Battery low alarm",         False),
        (14, "Battery under shutdown",    False),
        (16, "Overload",                  False),
        (17, "EEPROM fault",              False),
        (18, "Inverter over current",     True),
        (19, "Inverter soft fail",        True),
        (20, "Self test fail",            True),
        (21, "OP DC voltage over",        True),
        (22, "Battery open",              True),
        (23, "Current sensor fail",       True),
        (24, "Battery short",             True),
        (25, "Power limit",               False),
        (26, "PV voltage high",           False),
        (27, "MPPT overload fault",       True),
        (28, "MPPT overload warning",     False),
        (29, "Battery too low to charge", False),
    ]

    def query_qpiws(self) -> dict | None:
        """Query warning/fault status. Returns {'text','is_fault','raw'} or None."""
        with _cmd_lock:
            raw = self._query("QPIWS")
        if not raw.startswith("(") or raw.startswith("(NAK"):
            return None
        bits = raw[1:].strip()
        active, faulty = [], False
        for idx, label, is_fault in self.QPIWS_BITS:
            if idx < len(bits) and bits[idx] == "1":
                active.append(label)
                faulty = faulty or is_fault
        return {"text": ", ".join(active) if active else "OK",
                "is_fault": faulty, "raw": bits}

    def send_command(self, cmd: str) -> bool:
        """Send a set command to the inverter, return True if ACK."""
        with _cmd_lock:
            raw = self._query(cmd)
        ok = "ACK" in raw
        log.info("CMD %s -> %s", cmd, "ACK" if ok else f"FAIL({raw[:20]})")
        return ok

    def _reconnect_and_retry(self, cmd: str) -> dict | None:
        """Try reopening the device and querying again."""
        self.close()
        for candidate in [self.port, "/dev/hidraw0", "/dev/hidraw1", "/dev/ttyUSB0"]:
            if os.path.exists(candidate):
                self.port = candidate
                if self.open():
                    log.info("Inverter reconnected on %s", candidate)
                    break
        return None


# ── HA discovery for inverter ────────────────────────────────────────────────

def register_inverter_sensors(client):
    dev = {"identifiers": ["flin_fution"],
           "name": "Flin Fution Inverter",
           "model": "Flin Fution (Voltronic/Axpert)",
           "manufacturer": "Flin Energy"}

    for key, unit, dc, sc in VoltronicInverter.QPIGS_FIELDS:
        label = key.replace("_", " ").title()
        ha_disc(client, f"inv_{key}", label, f"solar/inverter/{key}",
                unit=unit, device_class=dc, state_class=sc, device_info=dev)

    for key, unit, dc, sc in VoltronicInverter.DERIVED:
        label = key.replace("_", " ").title()
        ha_disc(client, f"inv_{key}", label, f"solar/inverter/{key}",
                unit=unit, device_class=dc, state_class=sc, device_info=dev)

    # Extra sensors
    for key, label, unit, dc, sc in [
        ("device_mode",         "Device Mode",         None,  None,          None),
        ("pv_energy",           "PV Energy",           "kWh", "energy",      "total_increasing"),
        ("load_energy",         "Load Energy",         "kWh", "energy",      "total_increasing"),
        ("grid_energy_in",      "Grid Energy In",      "kWh", "energy",      "total_increasing"),
        ("grid_energy_out",     "Grid Energy Out",     "kWh", "energy",      "total_increasing"),
        ("battery_energy_in",   "Battery Energy In",   "kWh", "energy",      "total_increasing"),
        ("battery_energy_out",  "Battery Energy Out",  "kWh", "energy",      "total_increasing"),
        ("max_charge_current",  "Max Charge Current",  "A",   "current",     "measurement"),
        ("max_ac_charge_current","Max Grid Charge Current","A","current",     "measurement"),
        ("battery_float_voltage","Battery Float Voltage","V", "voltage",     "measurement"),
        ("battery_bulk_voltage","Battery Bulk Voltage","V",   "voltage",     "measurement"),
        ("battery_cutoff_voltage","Battery Cutoff Voltage","V","voltage",    "measurement"),
        ("battery_redischarge_voltage","Battery Redischarge Voltage","V","voltage","measurement"),
        ("serial_number",       "Serial Number",       None,  None,          None),
        ("fault_text",          "Fault / Warning",     None,  None,          None),
        ("pv_energy_today",     "PV Energy Today",     "kWh", "energy",      "total_increasing"),
        ("load_energy_today",   "Load Energy Today",   "kWh", "energy",      "total_increasing"),
        ("grid_energy_today",   "Grid Energy Today",   "kWh", "energy",      "total_increasing"),
        ("battery_charge_today","Battery Charged Today","kWh","energy",      "total_increasing"),
        ("battery_discharge_today","Battery Discharged Today","kWh","energy","total_increasing"),
    ]:
        ha_disc(client, f"inv_{key}", label, f"solar/inverter/{key}",
                unit=unit, device_class=dc, state_class=sc, device_info=dev)

    # ── Controls ──────────────────────────────────────────────────────────
    # Select entities
    for ctrl_key, label, options in [
        ("output_priority",  "Output Source Priority", ["Grid first", "Solar first", "SBU"]),
        ("charger_priority", "Charger Source Priority", ["Grid first", "Solar first", "Solar+Grid", "Solar only"]),
    ]:
        ha_disc(client, f"inv_ctrl_{ctrl_key}", label,
                state_topic=f"solar/inverter/control/{ctrl_key}",
                device_info=dev,
                entity_type="select",
                extra={
                    "command_topic": f"solar/inverter/control/{ctrl_key}/set",
                    "options": options,
                })

    # Number entities
    for ctrl_key, label, unit, min_v, max_v, step in [
        ("max_charge_current",      "Max Charge Current",      "A",  10,   120, 10),
        ("max_grid_charge_current", "Max Grid Charge Current", "A",  2,    60,  2),
        ("battery_float_voltage",   "Battery Float Voltage",   "V",  44.0, 58.4, 0.1),
        ("battery_bulk_voltage",    "Battery Bulk Voltage",    "V",  44.0, 58.4, 0.1),
        ("battery_shutdown_voltage","Battery Shutdown Voltage","V",  40.0, 48.0, 0.1),
        ("battery_recharge_voltage","Battery Recharge Voltage","V",  44.0, 51.0, 0.1),
    ]:
        ha_disc(client, f"inv_ctrl_{ctrl_key}", label,
                state_topic=f"solar/inverter/control/{ctrl_key}",
                unit=unit, device_info=dev,
                entity_type="number",
                extra={
                    "command_topic": f"solar/inverter/control/{ctrl_key}/set",
                    "min": min_v, "max": max_v, "step": step,
                    "mode": "box",
                })

    log.info("Inverter HA discovery published (%d sensors + controls)",
             len(VoltronicInverter.QPIGS_FIELDS) + len(VoltronicInverter.DERIVED) + 14)


# ---------------------------------------------------------------------------
# JKBMS — dual-unit support (55 AA EB 90 new protocol, passive broadcast)
# Both BMS units on same RS485 bus, identified by byte 5 of each frame:
#   BMS 1 → frame ID 0x00  (RS485 address 1)
#   BMS 2 → frame ID 0x05  (RS485 address 2, confirmed from live capture)
# ---------------------------------------------------------------------------

BMS_FRAME_IDS = {0x00: "bms1", 0x05: "bms2"}   # frame_id_byte → topic prefix

class JKBMS:
    NEW_HEADER = b"\x55\xaa\xeb\x90"

    def __init__(self, port, baud=115200, cell_count=16):
        self.port = port; self.baud = baud; self.cell_count = cell_count
        self._ser = None

    def open(self):
        try:
            self._ser = serial.Serial(self.port, baudrate=self.baud, bytesize=8,
                                      parity="N", stopbits=1, timeout=2)
            self._ser.reset_input_buffer()
            time.sleep(1.5)
            probe = self._ser.read(128)
            count = probe.count(self.NEW_HEADER)
            log.info("JKBMS opened on %s @ %d (%d header(s) in probe)",
                     self.port, self.baud, count)
            return True
        except Exception as e:
            log.error("JKBMS open failed: %s", e)
            return False

    def read_all(self) -> dict:
        """
        Read one broadcast cycle (~1s) and return parsed data for every BMS found.
        Returns: {frame_id_byte: parsed_dict, ...}
        e.g. {0x00: {...bms1 data...}, 0x05: {...bms2 data...}}
        """
        try:
            self._ser.reset_input_buffer()
            raw = b""
            deadline = time.time() + 4.0
            self._ser.timeout = 0.2

            # Read until we have at least one type-02 frame from every known BMS,
            # or until the time window expires
            while time.time() < deadline:
                chunk = self._ser.read(256)
                if chunk:
                    raw += chunk
                    # Check if we have a type-02 frame for each known BMS ID
                    found_ids = set()
                    pos = 0
                    while True:
                        idx = raw.find(self.NEW_HEADER, pos)
                        if idx == -1: break
                        if idx + 5 < len(raw) and raw[idx + 4] == 0x02:
                            found_ids.add(raw[idx + 5])
                        pos = idx + 1
                    # Stop early once we have a 200-byte region after each known BMS frame
                    if found_ids >= set(BMS_FRAME_IDS.keys()):
                        ok = True
                        for fid in BMS_FRAME_IDS:
                            needle = self.NEW_HEADER + b"\x02" + bytes([fid])
                            idx2 = raw.rfind(needle)
                            if idx2 == -1 or len(raw) - idx2 < 200:
                                ok = False; break
                        if ok:
                            break

            return self._parse_all_frames(raw)

        except Exception as e:
            log.error("JKBMS read error: %s", e)
            return {}

    def _parse_all_frames(self, raw: bytes) -> dict:
        """Find all type-02 frames in the buffer and parse each one."""
        results = {}
        pos = 0
        while True:
            idx = raw.find(self.NEW_HEADER + b"\x02", pos)
            if idx == -1:
                break
            pos = idx + 1
            frame = raw[idx:]
            if len(frame) < 40:
                continue
            frame_id = frame[5]
            # Only parse the FIRST occurrence of each frame ID
            if frame_id in results:
                continue
            parsed = self._parse_cell_frame(frame)
            if parsed:
                parsed["frame_id"] = frame_id
                results[frame_id] = parsed

        if not results:
            log.warning("JKBMS: no type-02 frames found in %d bytes", len(raw))
        else:
            log.debug("JKBMS: parsed frames for IDs: %s",
                      [f"0x{k:02x}" for k in sorted(results)])
        return results

    def _parse_cell_frame(self, frame: bytes) -> dict:
        """
        JK BMS new-protocol type-02 (cell info) frame.
        Confirmed byte offsets (from live dual-BMS passive capture, 2026-06-07):
          +5   : device frame ID (0x00=BMS1, 0x05=BMS2)
          +6   : 16 × uint16 LE  cell voltages (mV)
          +144 : uint16 LE  MOS temperature (0.1°C)
          +150 : uint32 LE  pack voltage (mV)
          +162 : uint16 LE  temperature 1 (0.1°C)
          +164 : uint16 LE  temperature 2 (0.1°C)
          +173 : uint8      SOC %                       ← (was wrongly read at +182)
          +174 : uint32 LE  remaining capacity (mAh)    ← (was wrongly read at +154)
          +178 : uint32 LE  nominal/design capacity (mAh) ← (was wrongly read at +186)
          +182 : uint32 LE  cycle count                 ← (was wrongly read at +190)
          +190 : uint8      state of health %           ← (was wrongly reported as cycles)
        """
        data = {}
        def u8(off):  return frame[off] if off < len(frame) else None
        def u16(off): return struct.unpack_from("<H", frame, off)[0] if off+2<=len(frame) else None
        def u32(off): return struct.unpack_from("<I", frame, off)[0] if off+4<=len(frame) else None

        # Cell voltages
        cells = []
        for i in range(self.cell_count):
            mv = u16(6 + i*2)
            if mv and 2000 < mv < 5000:
                cells.append(round(mv / 1000, 3))
        if cells:
            data["cell_voltages"]     = cells
            data["cell_voltage_min"]  = min(cells)
            data["cell_voltage_max"]  = max(cells)
            data["cell_voltage_diff"] = round(max(cells) - min(cells), 3)
            data["cell_voltage_avg"]  = round(sum(cells) / len(cells), 3)

        v = u32(150)
        if v and 10_000 < v < 120_000:
            data["battery_voltage"] = round(v / 1000, 2)

        rc = u32(174)
        if rc and rc < 1_000_000:
            data["remaining_capacity_ah"] = round(rc / 1000, 1)

        t1 = u16(162)
        if t1 and 200 < t1 < 800:
            data["temp_battery_1"] = round(t1 / 10, 1)
        t2 = u16(164)
        if t2 and 200 < t2 < 800:
            data["temp_battery_2"] = round(t2 / 10, 1)

        mos = u16(144)
        if mos and 200 < mos < 800:
            data["temp_mos"] = round(mos / 10, 1)

        soc = u8(173)
        if soc is not None and 0 <= soc <= 100:
            data["battery_soc"] = soc

        dc_val = u32(178)
        if dc_val and 1000 < dc_val < 2_000_000:
            data["design_capacity_ah"] = round(dc_val / 1000, 1)

        cyc = u32(182)
        if cyc is not None and cyc < 100_000:
            data["battery_cycles"] = cyc

        soh = u8(190)
        if soh is not None and 0 < soh <= 100:
            data["state_of_health"] = soh

        return data

    def close(self):
        if self._ser: self._ser.close()


BMS_SENSORS = [
    ("battery_voltage",       "Battery Voltage",     "V",   "voltage",     "measurement"),
    ("battery_soc",           "State of Charge",     "%",   "battery",     "measurement"),
    ("battery_cycles",        "Cycle Count",         None,  None,          "total_increasing"),
    ("remaining_capacity_ah", "Remaining Capacity",  "Ah",  None,          "measurement"),
    ("design_capacity_ah",    "Design Capacity",     "Ah",  None,          "measurement"),
    ("state_of_health",       "State of Health",     "%",   None,          "measurement"),
    ("cell_voltage_min",      "Cell Voltage Lowest", "V",   "voltage",     "measurement"),
    ("cell_voltage_max",      "Cell Voltage Highest","V",   "voltage",     "measurement"),
    ("cell_voltage_diff",     "Cell Voltage Diff",   "V",   "voltage",     "measurement"),
    ("cell_voltage_avg",      "Cell Voltage Average","V",   "voltage",     "measurement"),
    ("temp_battery_1",        "Temperature 1",       "°C",  "temperature", "measurement"),
    ("temp_battery_2",        "Temperature 2",       "°C",  "temperature", "measurement"),
    ("temp_mos",              "Temperature MOS",     "°C",  "temperature", "measurement"),
]

COMBINED_SENSORS = [
    ("battery_voltage",           "Battery Voltage",           "V",   "voltage",  "measurement"),
    ("battery_soc",               "State of Charge",           "%",   "battery",  "measurement"),
    ("total_remaining_capacity",  "Total Remaining Capacity",  "Ah",  None,       "measurement"),
    ("total_design_capacity",     "Total Design Capacity",     "Ah",  None,       "measurement"),
    ("state_of_health",           "State of Health",           "%",   None,       "measurement"),
    ("cell_voltage_min",          "Cell Voltage Lowest",       "V",   "voltage",  "measurement"),
    ("cell_voltage_max",          "Cell Voltage Highest",      "V",   "voltage",  "measurement"),
    ("temp_battery_1",            "Temperature 1",             "°C",  "temperature","measurement"),
    ("temp_battery_2",            "Temperature 2",             "°C",  "temperature","measurement"),
    ("temp_mos",                  "Temperature MOS",           "°C",  "temperature","measurement"),
]


def register_bms_sensors(client):
    # Per-unit devices (BMS 1 and BMS 2)
    for frame_id, prefix in BMS_FRAME_IDS.items():
        n = prefix[-1]   # "1" or "2"
        dev = {
            "identifiers": [f"jk_{prefix}"],
            "name":         f"JK BMS {n} (100Ah)",
            "model":        "JK-B Series",
            "manufacturer": "Jikong",
        }
        for key, label, unit, dc, sc in BMS_SENSORS:
            ha_disc(client, f"{prefix}_{key}", f"BMS{n} {label}",
                    f"solar/{prefix}/{key}",
                    unit=unit, device_class=dc, state_class=sc, device_info=dev)
        for i in range(16):
            ha_disc(client, f"{prefix}_cell_{i+1:02d}", f"BMS{n} Cell {i+1}",
                    f"solar/{prefix}/cell_{i+1:02d}_voltage",
                    unit="V", device_class="voltage", state_class="measurement",
                    device_info=dev)

    # Combined "Battery Bank" device (totals + averages from both packs)
    bank_dev = {
        "identifiers": ["battery_bank"],
        "name":        "Battery Bank (200Ah)",
        "model":       "2× JK BMS parallel",
        "manufacturer":"Jikong",
    }
    for key, label, unit, dc, sc in COMBINED_SENSORS:
        ha_disc(client, f"bank_{key}", label, f"solar/bank/{key}",
                unit=unit, device_class=dc, state_class=sc, device_info=bank_dev)

    log.info("BMS HA discovery published (BMS1 + BMS2 + Battery Bank)")


# ---------------------------------------------------------------------------
# MQTT control message handler
# ---------------------------------------------------------------------------

def apply_control(inverter: VoltronicInverter, mqtt_client, ctrl_key: str, payload: str) -> bool:
    """Translate a control key/value into an inverter command and send it.
    Shared by the MQTT handler and the automation engine."""
    if ctrl_key not in inverter.SET_COMMANDS:
        log.warning("Unknown control key: %s", ctrl_key)
        return False
    prefix, transformer = inverter.SET_COMMANDS[ctrl_key]
    value = transformer(payload)
    if not value:
        log.warning("Could not map payload %r for %s", payload, ctrl_key)
        return False
    ok = inverter.send_command(prefix + value)
    if ok and mqtt_client:
        # Echo the new value back to HA / dashboard state topic
        mqtt_client.publish(f"solar/inverter/control/{ctrl_key}", payload, retain=True)
    elif not ok:
        log.error("Command %s%s failed (NAK from inverter)", prefix, value)
    return ok


def make_on_message(inverter: VoltronicInverter, mqtt_client):
    def on_message(client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode(errors="ignore").strip()
        log.info("Control msg: %s = %r", topic, payload)

        # Extract control key from topic: solar/inverter/control/<key>/set
        parts = topic.split("/")
        if len(parts) < 5 or parts[-1] != "set":
            return
        apply_control(inverter, mqtt_client, parts[-2], payload)

    return on_message


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()

    inv_port     = cfg_str(cfg, "inverter", "port",          "/dev/hidraw0")
    inv_proto    = cfg_str(cfg, "inverter", "protocol",      "PI30")
    inv_interval = cfg_int(cfg, "inverter", "poll_interval", 10)
    bms_port     = cfg_str(cfg, "jkbms",   "port",          "/dev/ttyUSB0")
    bms_baud     = cfg_int(cfg, "jkbms",   "baud",          115200)
    bms_cells    = cfg_int(cfg, "jkbms",   "cell_count",    16)
    bms_interval = cfg_int(cfg, "jkbms",   "poll_interval", 10)

    energy = EnergyTracker()
    inverter = VoltronicInverter(inv_port, inv_proto)

    # MQTT must be set up before on_message so the closure captures the client
    # We create a placeholder and fill it in after connect
    client_holder = [None]

    def on_msg(client, userdata, msg):
        if client_holder[0]:
            make_on_message(inverter, client_holder[0])(client, userdata, msg)

    client = mqtt_connect(cfg, on_msg)
    client_holder[0] = client

    register_inverter_sensors(client)
    register_bms_sensors(client)

    # ── Add-ons: history DB, notifier (Telegram/e-mail/HA), automation ──────
    latest_state = {}                       # flat key→value mirror of MQTT topics
    def get_state():
        return dict(latest_state)

    notifier = Notifier(cfg, client, get_state,
                        control_cb=lambda k, v: apply_control(inverter, client, k, v)) \
               if Notifier else None
    if notifier:
        notifier.start_bot()
    automation = (AutomationEngine(lambda k, v: apply_control(inverter, client, k, v))
                  if AutomationEngine else None)
    if automation:
        log.info("Automation engine loaded (%d rules)", len(automation.rules))

    bms = JKBMS(bms_port, bms_baud, bms_cells)
    inv_ok = inverter.open()
    bms_ok = bms.open()

    # One-shot queries at startup
    if inv_ok:
        sn = inverter.query_serial()
        if sn:
            pub(client, "solar/inverter/serial_number", sn)
        mode = inverter.query_qmod()
        if mode:
            pub(client, "solar/inverter/device_mode", mode)

    last_inv     = 0
    last_bms     = 0
    last_qpiri   = 0
    last_qmod    = 0
    last_qpiws   = 0
    last_persist = 0          # DB snapshot + daily energy push (every 60s)
    last_live    = 0          # live_state.json write for the dashboard (every 3s)
    last_inv_data = {}
    daily_peak_pv = 0.0       # for the daily Telegram summary
    daily_min_soc = 101.0
    summary_sent_date = None

    log.info("Polling loop started")

    try:
        while True:
            now = time.time()

            # ── Inverter QPIGS ───────────────────────────────────────────
            if inv_ok and now - last_inv >= inv_interval:
                d = inverter.query_qpigs()
                if d:
                    for key, *_ in VoltronicInverter.QPIGS_FIELDS:
                        pub(client, f"solar/inverter/{key}", d.get(key))

                    # Derived values
                    pv_v  = d.get("pv_input_voltage",         0) or 0
                    pv_i  = d.get("pv_input_current",         0) or 0
                    b_v   = d.get("battery_voltage",          0) or 0
                    b_ci  = d.get("battery_charge_current",   0) or 0
                    b_di  = d.get("battery_discharge_current",0) or 0
                    p_out = d.get("ac_out_active_power",      0) or 0

                    pv_power    = round(pv_v * pv_i, 1)
                    bat_current = round(b_ci - b_di, 2)   # + = charging, - = discharging
                    bat_power   = round(b_v * bat_current, 1)
                    # P_grid = P_load - P_pv - P_battery_discharge
                    # When battery discharges bat_power < 0, so -bat_power > 0 (battery helping)
                    bat_discharge_w = max(0.0, -bat_power)  # positive when battery discharges
                    grid_power  = max(0.0, round(p_out - pv_power - bat_discharge_w, 1))

                    pub(client, "solar/inverter/pv_power",    pv_power)
                    pub(client, "solar/inverter/battery_power", bat_power)
                    pub(client, "solar/inverter/grid_power",  grid_power)
                    pub(client, "solar/inverter/battery_current", bat_current)

                    # Energy accumulation
                    dt = now - last_inv if last_inv > 0 else inv_interval
                    if pv_power > 0:
                        energy.add("pv_kwh", pv_power, dt)
                    if p_out > 0:
                        energy.add("load_kwh", p_out, dt)
                    if bat_current > 0:
                        energy.add("batt_in_kwh", abs(bat_power), dt)
                    else:
                        energy.add("batt_out_kwh", abs(bat_power), dt)
                    if grid_power > 0:
                        energy.add("grid_in_kwh", grid_power, dt)

                    pub(client, "solar/inverter/pv_energy",         energy.get("pv_kwh"))
                    pub(client, "solar/inverter/load_energy",       energy.get("load_kwh"))
                    pub(client, "solar/inverter/battery_energy_in", energy.get("batt_in_kwh"))
                    pub(client, "solar/inverter/battery_energy_out",energy.get("batt_out_kwh"))
                    pub(client, "solar/inverter/grid_energy_in",    energy.get("grid_in_kwh"))

                    # Today's energy (reset at midnight)
                    pub(client, "solar/inverter/pv_energy_today",       energy.today("pv_kwh"))
                    pub(client, "solar/inverter/load_energy_today",     energy.today("load_kwh"))
                    pub(client, "solar/inverter/grid_energy_today",     energy.today("grid_in_kwh"))
                    pub(client, "solar/inverter/battery_charge_today",  energy.today("batt_in_kwh"))
                    pub(client, "solar/inverter/battery_discharge_today",energy.today("batt_out_kwh"))

                    # Mirror into flat state for alerts/automation/Telegram
                    for key, *_ in VoltronicInverter.QPIGS_FIELDS:
                        if d.get(key) is not None:
                            latest_state[f"inverter_{key}"] = d[key]
                    latest_state.update({
                        "inverter_pv_power": pv_power,
                        "inverter_battery_power": bat_power,
                        "inverter_grid_power": grid_power,
                        "inverter_battery_current": bat_current,
                        "inverter_pv_energy": energy.get("pv_kwh"),
                        "inverter_pv_energy_today":        energy.today("pv_kwh"),
                        "inverter_load_energy_today":      energy.today("load_kwh"),
                        "inverter_grid_energy_today":      energy.today("grid_in_kwh"),
                        "inverter_battery_charge_today":   energy.today("batt_in_kwh"),
                        "inverter_battery_discharge_today":energy.today("batt_out_kwh"),
                    })

                    log.info("Inverter: PV=%.0fW (%.1fV %.1fA)  Load=%.0fW  Grid=%.0fW  Batt=%.1fV %.1fA",
                             pv_power, pv_v, pv_i, p_out, grid_power, b_v, bat_current)

                    last_inv_data = d
                else:
                    # Try reconnect
                    inverter.close()
                    for cand in [inv_port, "/dev/hidraw0", "/dev/hidraw1"]:
                        if os.path.exists(cand):
                            inverter.port = cand
                            inv_ok = inverter.open()
                            if inv_ok: break
                last_inv = now

            # ── QPIRI (settings, every 60s) ──────────────────────────────
            if inv_ok and now - last_qpiri >= 60:
                s = inverter.query_qpiri()
                if s:
                    for _, key, _ in VoltronicInverter.QPIRI_FIELDS:
                        if key in s:
                            pub(client, f"solar/inverter/{key}", s[key])
                            latest_state[f"inverter_{key}"] = s[key]
                    # Publish control state topics (so HA shows current values)
                    pri_map = {0:"Grid first", 1:"Solar first", 2:"SBU"}
                    chg_map = {0:"Grid first", 1:"Solar first", 2:"Solar+Grid", 3:"Solar only"}
                    op = s.get("output_source_priority")
                    cp = s.get("charger_source_priority")
                    if op is not None: latest_state["inverter_control_output_priority"]  = pri_map.get(int(op),"")
                    if cp is not None: latest_state["inverter_control_charger_priority"] = chg_map.get(int(cp),"")
                    if "max_charge_current"    in s: latest_state["inverter_max_charge_current"]    = int(s["max_charge_current"])
                    if "max_ac_charge_current" in s: latest_state["inverter_max_ac_charge_current"] = int(s["max_ac_charge_current"])
                    if op is not None: client.publish("solar/inverter/control/output_priority",  pri_map.get(int(op),""), retain=True)
                    if cp is not None: client.publish("solar/inverter/control/charger_priority", chg_map.get(int(cp),""), retain=True)
                    if "max_charge_current" in s:
                        client.publish("solar/inverter/control/max_charge_current",      str(int(s["max_charge_current"])), retain=True)
                        client.publish("solar/inverter/max_charge_current",              str(int(s["max_charge_current"])))
                    if "max_ac_charge_current" in s:
                        client.publish("solar/inverter/control/max_grid_charge_current", str(int(s["max_ac_charge_current"])), retain=True)
                        client.publish("solar/inverter/max_ac_charge_current",           str(int(s["max_ac_charge_current"])))
                    # Voltage settings → controls + sensor topics
                    for v_key, ctrl_key, sensor_key in [
                        ("battery_float_voltage",   "battery_float_voltage",   "battery_float_voltage"),
                        ("battery_bulk_voltage",    "battery_bulk_voltage",    "battery_bulk_voltage"),
                        ("battery_cutoff_voltage",  "battery_shutdown_voltage","battery_cutoff_voltage"),
                        ("battery_back_voltage",    "battery_recharge_voltage","battery_back_voltage"),
                        ("battery_redischarge_voltage", None,                  "battery_redischarge_voltage"),
                    ]:
                        val = s.get(v_key)
                        if val is not None:
                            client.publish(f"solar/inverter/{sensor_key}", str(val))
                            if ctrl_key:
                                client.publish(f"solar/inverter/control/{ctrl_key}", str(val), retain=True)
                    log.info("QPIRI: out_pri=%s chg_pri=%s max_chg=%sA float=%.1fV",
                             pri_map.get(op,"?"), chg_map.get(cp,"?"),
                             s.get("max_charge_current","?"),
                             s.get("battery_float_voltage", 0))
                last_qpiri = now

            # ── QMOD (device mode, every 15s) ────────────────────────────
            if inv_ok and now - last_qmod >= 15:
                mode = inverter.query_qmod()
                if mode:
                    pub(client, "solar/inverter/device_mode", mode)
                    latest_state["inverter_device_mode"] = mode
                last_qmod = now

            # ── QPIWS (fault / warning status, every 30s) ────────────────
            if inv_ok and now - last_qpiws >= 30:
                w = inverter.query_qpiws()
                if w:
                    pub(client, "solar/inverter/fault_text", w["text"])
                    pub(client, "solar/inverter/fault_status",
                        "fault" if w["is_fault"] else ("warning" if w["text"] != "OK" else "ok"))
                    # Only real faults trigger the critical alert; warnings such as
                    # "Line fail" (grid simply absent) show as a sensor but don't spam.
                    latest_state["inverter_fault_text"] = w["text"] if w["is_fault"] else ""
                last_qpiws = now

            # ── JKBMS (dual-unit) ────────────────────────────────────────
            if bms_ok and now - last_bms >= bms_interval:
                all_bms = bms.read_all()   # {frame_id: {field: value}}
                if all_bms:
                    # Publish per-unit sensors
                    for frame_id, d in all_bms.items():
                        prefix = BMS_FRAME_IDS.get(frame_id, f"bms_{frame_id:02x}")
                        for key, value in d.items():
                            if key in ("frame_id",): continue
                            if key == "cell_voltages":
                                for i, v in enumerate(value):
                                    pub(client, f"solar/{prefix}/cell_{i+1:02d}_voltage", v)
                                continue
                            pub(client, f"solar/{prefix}/{key}", value)

                    # Build combined Bank sensors from all units
                    all_v   = [d.get("battery_voltage") for d in all_bms.values() if d.get("battery_voltage")]
                    all_soc = [d.get("battery_soc")     for d in all_bms.values() if d.get("battery_soc") is not None]
                    all_t1  = [d.get("temp_battery_1")  for d in all_bms.values() if d.get("temp_battery_1")]
                    all_t2  = [d.get("temp_battery_2")  for d in all_bms.values() if d.get("temp_battery_2")]
                    all_mos = [d.get("temp_mos")         for d in all_bms.values() if d.get("temp_mos")]
                    all_rem = [d.get("remaining_capacity_ah") for d in all_bms.values() if d.get("remaining_capacity_ah")]
                    all_des = [d.get("design_capacity_ah")    for d in all_bms.values() if d.get("design_capacity_ah")]
                    all_cm  = [d.get("cell_voltage_min") for d in all_bms.values() if d.get("cell_voltage_min")]
                    all_cx  = [d.get("cell_voltage_max") for d in all_bms.values() if d.get("cell_voltage_max")]

                    if all_v:   pub(client, "solar/bank/battery_voltage",          round(sum(all_v)/len(all_v), 3))
                    if all_soc: pub(client, "solar/bank/battery_soc",               round(sum(all_soc)/len(all_soc)))
                    if all_t1:  pub(client, "solar/bank/temp_battery_1",            round(sum(all_t1)/len(all_t1), 1))
                    if all_t2:  pub(client, "solar/bank/temp_battery_2",            round(sum(all_t2)/len(all_t2), 1))
                    if all_mos: pub(client, "solar/bank/temp_mos",                  round(sum(all_mos)/len(all_mos), 1))
                    if all_rem: pub(client, "solar/bank/total_remaining_capacity",  round(sum(all_rem), 1))
                    if all_des: pub(client, "solar/bank/total_design_capacity",     round(sum(all_des), 1))
                    if all_cm:  pub(client, "solar/bank/cell_voltage_min",          min(all_cm))
                    if all_cx:  pub(client, "solar/bank/cell_voltage_max",          max(all_cx))
                    if all_rem and all_des and sum(all_des) > 0:
                        soh = round(sum(all_rem) / sum(all_des) * 100, 1)
                        pub(client, "solar/bank/state_of_health", min(100.0, soh))

                    # Mirror BMS data into flat state for alerts/automation/Telegram/dashboard
                    for frame_id, d in all_bms.items():
                        prefix = BMS_FRAME_IDS.get(frame_id, f"bms_{frame_id:02x}")
                        for k2, val in d.items():
                            if k2 == "frame_id":
                                continue
                            if k2 == "cell_voltages":
                                for i, cv in enumerate(val):
                                    latest_state[f"{prefix}_cell_{i+1:02d}_voltage"] = cv
                                continue
                            latest_state[f"{prefix}_{k2}"] = val
                    if all_v:   latest_state["bank_battery_voltage"] = round(sum(all_v)/len(all_v), 3)
                    if all_soc: latest_state["bank_battery_soc"]     = round(sum(all_soc)/len(all_soc))
                    if all_mos: latest_state["bank_temp_mos"]        = round(sum(all_mos)/len(all_mos), 1)
                    if all_t1:  latest_state["bank_temp_battery_1"]  = round(sum(all_t1)/len(all_t1), 1)
                    if all_t2:  latest_state["bank_temp_battery_2"]  = round(sum(all_t2)/len(all_t2), 1)
                    if all_rem: latest_state["bank_total_remaining_capacity"] = round(sum(all_rem), 1)
                    if all_des: latest_state["bank_total_design_capacity"]    = round(sum(all_des), 1)
                    if all_cm:  latest_state["bank_cell_voltage_min"] = min(all_cm)
                    if all_cx:  latest_state["bank_cell_voltage_max"] = max(all_cx)
                    if all_cm and all_cx:
                        latest_state["bank_cell_voltage_diff"] = round(max(all_cx) - min(all_cm), 3)
                    if all_rem and all_des and sum(all_des) > 0:
                        latest_state["bank_state_of_health"] = min(100.0, round(sum(all_rem)/sum(all_des)*100, 1))

                    # Log one line per BMS + combined
                    parts = []
                    for fid, d in sorted(all_bms.items()):
                        n = BMS_FRAME_IDS.get(fid, f"bms{fid:02x}")[-1]
                        parts.append(f"BMS{n}: {d.get('battery_voltage','?')}V "
                                     f"SOC={d.get('battery_soc','?')}% "
                                     f"T={d.get('temp_mos','?')}°C "
                                     f"cy={d.get('battery_cycles','?')}")
                    if all_rem:
                        parts.append(f"BANK: {sum(all_v)/len(all_v):.2f}V "
                                     f"SOC={round(sum(all_soc)/len(all_soc))}% "
                                     f"rem={sum(all_rem):.0f}Ah")
                    log.info("  ".join(parts))
                else:
                    bms.close()
                    bms_ok = bms.open()
                last_bms = now

            # ── Dashboard control queue (works even when MQTT is down) ───
            if inv_ok and CONTROL_QUEUE.exists():
                try:
                    cmds = json.loads(CONTROL_QUEUE.read_text())
                    CONTROL_QUEUE.unlink()       # consume the queue
                except Exception:
                    cmds = []
                    try: CONTROL_QUEUE.unlink()
                    except Exception: pass
                for cmd in cmds:
                    k, v = cmd.get("key"), cmd.get("value")
                    if k:
                        log.info("Control (queue): %s = %s", k, v)
                        apply_control(inverter, client, k, str(v))

            # ── Track daily extremes (for the Telegram summary) ──────────
            _pv = latest_state.get("inverter_pv_power")
            if _pv is not None and _pv > daily_peak_pv:
                daily_peak_pv = _pv
            _soc = latest_state.get("bank_battery_soc", latest_state.get("bms1_battery_soc"))
            if _soc is not None and _soc < daily_min_soc:
                daily_min_soc = _soc

            # ── Scheduled daily Telegram summary ─────────────────────────
            if notifier and getattr(notifier, "daily_summary", False):
                from datetime import datetime as _dt
                nowdt = _dt.now()
                today_str = nowdt.date().isoformat()
                if nowdt.strftime("%H:%M") == (notifier.summary_time or "21:00") \
                        and summary_sent_date != today_str:
                    summary_sent_date = today_str
                    try:
                        notifier.send_daily_summary(
                            peak_pv=daily_peak_pv if daily_peak_pv > 0 else None,
                            min_soc=daily_min_soc if daily_min_soc <= 100 else None)
                        log.info("Daily summary sent to Telegram")
                    except Exception as e:
                        log.warning("Daily summary failed: %s", e)

            # ── Periodic add-on tasks ────────────────────────────────────
            # Midnight rollover for daily energy
            if energy.rollover_if_new_day():
                daily_peak_pv = 0.0; daily_min_soc = 101.0
                if notifier:
                    notifier.send("info", "📅 New day — daily energy counters reset.")

            # Live snapshot file for the dashboard (works even if MQTT is down)
            if now - last_live >= 3 and latest_state:
                try:
                    tmp = LIVE_PATH.with_suffix(".tmp")
                    tmp.write_text(json.dumps({**latest_state, "_ts": now}))
                    tmp.replace(LIVE_PATH)
                except Exception as e:
                    log.debug("live_state write failed: %s", e)
                last_live = now

            # History snapshot + daily energy push (every 60s)
            if now - last_persist >= 60:
                if db:
                    db.store_snapshot(latest_state)
                energy.push_daily()
                last_persist = now

            # Alert checks + automation (every cycle, cheap with cooldown/edge)
            if notifier:
                try: notifier.check(latest_state)
                except Exception as e: log.warning("Alert check failed: %s", e)
            if automation:
                try: automation.evaluate(latest_state)
                except Exception as e: log.warning("Automation eval failed: %s", e)

            time.sleep(1)

    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        if notifier:
            notifier.stop()
        client.publish("solar/bridge/status", "offline", retain=True)
        inverter.close()
        bms.close()
        client.loop_stop()


if __name__ == "__main__":
    main()
