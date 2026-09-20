"""
Voltronic PI30 inverter adapter (USB-HID or serial).

Confirmed working on: Flin Fution 5kVA (Voltronic/Axpert clone).
Also known to work with most Voltronic/Axpert/MPP Solar/EASUN/PowMr clones
that speak the PI30 command set — see README.md "Protocol notes" for the
exact byte offsets and set-command prefixes confirmed by live testing.

Moved out of solar_bridge.py unchanged (byte-for-byte logic) so other
inverter brands can be added as sibling adapters under devices/inverters/
without touching this file. Do not "clean up" the confirmed offsets/prefixes
below without re-verifying against real hardware.
"""

import os
import select
import threading
import time

import serial

from ..base import InverterAdapter

log = __import__("logging").getLogger("solar_bridge.inverters.voltronic")

# Serialises HID/serial access between the poll loop and command sends.
# One lock per adapter instance would also work, but a single physical bus
# is shared regardless, so a module-level lock matches solar_bridge.py's
# original behaviour.
_cmd_lock = threading.Lock()


class VoltronicInverter(InverterAdapter):
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

    # QPIRI field positions — confirmed by command testing 2026-06-07
    # 0=220.0 | 1=22.7 | 2=220.0 | 3=50.0 | 4=22.7 | 5=5000 | 6=5000
    # 7=48.0  | 8=46.0 | 9=48.0  | 10=55.1| 11=54.0| 12=2   | 13=20
    # 14=080  | 15=0   | 16=0→1  | 17=0→3 | 18=1   | 19=01  | 20=0
    # 21=0    | 22=46.0| 23=0    | 24=1
    # [15]=input voltage range (always 0, NOT a priority field)
    # [16]=output_source_priority  ← POP01 changed this 0→1 (Solar first) ✓
    # [17]=charger_source_priority ← PCP03 changed this 1→3 (Solar only)  ✓
    QPIRI_FIELDS = [
        (7,  "battery_cutoff_voltage",      "V"),   # shutdown / under-voltage
        (8,  "battery_back_voltage",        "V"),   # back-to-discharge
        (10, "battery_bulk_voltage",        "V"),   # absorption / bulk charge
        (11, "battery_float_voltage",       "V"),   # float charge
        (13, "max_ac_charge_current",       "A"),   # max grid charge current = 20
        (14, "max_charge_current",          "A"),   # max total charge current = 80
        (16, "output_source_priority",      None),  # 0=Grid,1=Solar,2=SBU  (was [15], fixed)
        (17, "charger_source_priority",     None),  # 0=Grid,1=Solar,2=Sol+Grid,3=SolOnly (was [16], fixed)
        (22, "battery_redischarge_voltage", "V"),   # redischarge voltage
    ]

    SET_COMMANDS = {
        # key → (prefix, value_transformer)
        # Confirmed working on Flin Fution (Voltronic clone) 2026-06-07:
        "output_priority":   ("POP",   lambda v: {"Grid first":"00","Solar first":"01","SBU":"02"}.get(v,"")),
        "charger_priority":  ("PCP",   lambda v: {"Grid first":"00","Solar first":"01","Solar+Grid":"02","Solar only":"03"}.get(v,"")),
        "max_charge_current":      ("MUCHGC", lambda v: f"{int(float(v)):03d}"),  # MUCHGC confirmed
        "max_grid_charge_current": ("MUCHGC", lambda v: f"{int(float(v)):03d}"),  # same prefix confirmed
        "battery_float_voltage":   ("PBFT",   lambda v: f"{float(v):.1f}"),
        "battery_bulk_voltage":    ("PCVV",   lambda v: f"{float(v):.1f}"),  # PCVV not PBCV
        "battery_shutdown_voltage":("PSDV",   lambda v: f"{float(v):.1f}"),
        "battery_recharge_voltage":("PBDV",   lambda v: f"{float(v):.1f}"),
    }

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
    def open(self) -> bool:
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

    def close(self) -> None:
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

    def query_status(self) -> dict | None:
        """InverterAdapter interface — same as query_qpigs() for this adapter."""
        return self.query_qpigs()

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


# Alias — the protocol name doubles as the class name in the adapter registry.
VoltronicPI30 = VoltronicInverter
