"""
JK BMS adapter — new protocol (55 AA EB 90), passive RS485 broadcast.

Confirmed on: 2x JK-B2A24S20P 16S LiFePO4 packs sharing one RS485 bus,
distinguished by frame-ID byte +5 (0x00, 0x05, ...).

Unlike the inverter, one JKBMS instance here reads the WHOLE bus and returns
one dict per frame ID it sees — that's what makes "extra add-on battery"
possible without code changes: adding a 3rd/4th pack to the bus with its own
RS485 address just needs its frame ID added to config.ini's [jkbms]
frame_ids, nothing in this file changes.

Do not "clean up" the confirmed byte offsets below without re-verifying
against a live capture — see README.md "Protocol notes".
"""

import struct
import time

import serial

from ..base import BatteryAdapter

log = __import__("logging").getLogger("solar_bridge.batteries.jkbms")


class JKBMS(BatteryAdapter):
    NEW_HEADER = b"\x55\xaa\xeb\x90"

    def __init__(self, port, baud=115200, cell_count=16, expected_frame_ids=None):
        """
        expected_frame_ids: RS485 addresses (frame-ID byte) this bus is known
        to carry, e.g. [0x00, 0x05] for two packs, or [0x00, 0x05, 0x0a] for
        three. Used only to know when a read cycle can stop early once every
        expected pack has reported — read_all() still returns whatever frame
        IDs actually show up, expected or not, so misconfiguring this list
        just costs a little poll latency, not lost data.
        """
        self.port = port; self.baud = baud; self.cell_count = cell_count
        self.expected_frame_ids = list(expected_frame_ids or [0x00, 0x05])
        self._ser = None

    def open(self) -> bool:
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

    def read(self) -> dict | None:
        """BatteryAdapter interface — not used directly (see read_all())."""
        return None

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

            # Read until we have at least one type-02 frame from every expected
            # BMS, or until the time window expires
            while time.time() < deadline:
                chunk = self._ser.read(256)
                if chunk:
                    raw += chunk
                    # Check if we have a type-02 frame for each expected BMS ID
                    found_ids = set()
                    pos = 0
                    while True:
                        idx = raw.find(self.NEW_HEADER, pos)
                        if idx == -1: break
                        if idx + 5 < len(raw) and raw[idx + 4] == 0x02:
                            found_ids.add(raw[idx + 5])
                        pos = idx + 1
                    # Stop early once we have a 200-byte region after each expected BMS frame
                    if found_ids >= set(self.expected_frame_ids):
                        ok = True
                        for fid in self.expected_frame_ids:
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
        def s32(off): return struct.unpack_from("<i", frame, off)[0] if off+4<=len(frame) else None

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

        # Per-pack current: int32 LE @ +158, milliamps, signed (+charge / -discharge).
        # Confirmed by cross-referencing the inverter's total: sum of all packs ≈ inverter.
        cur = s32(158)
        if cur is not None and abs(cur) < 600_000:        # < 600 A sanity
            amps = round(cur / 1000, 2)
            data["battery_current"] = amps
            if "battery_voltage" in data:
                data["battery_power"] = round(data["battery_voltage"] * amps, 1)

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

    def close(self) -> None:
        if self._ser: self._ser.close()
