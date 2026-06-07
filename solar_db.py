#!/usr/bin/env python3
"""
Solar Bridge — SQLite data store.
Stores per-minute readings, daily energy totals, and alert history.
Used by solar_bridge.py (writer) and dashboard/app.py (reader).
"""

import sqlite3, time, threading
from pathlib import Path
from datetime import datetime, date

DB_PATH = Path(__file__).parent / "solar_bridge.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS readings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    source  TEXT    NOT NULL,
    key     TEXT    NOT NULL,
    value   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_ts  ON readings(ts);
CREATE INDEX IF NOT EXISTS idx_readings_key ON readings(source, key, ts);

CREATE TABLE IF NOT EXISTS daily_energy (
    date    TEXT    NOT NULL,
    key     TEXT    NOT NULL,
    value   REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (date, key)
);

CREATE TABLE IF NOT EXISTS alerts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL    NOT NULL,
    level   TEXT    NOT NULL,
    message TEXT    NOT NULL,
    acked   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
"""

_local = threading.local()

def get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn.executescript(CREATE_SQL)
        _local.conn.commit()
    return _local.conn


class SolarDB:
    """Thread-safe wrapper around the SQLite database."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_minute = -1
        # Open once on main thread to create schema
        conn = get_conn()

    # ── Write ─────────────────────────────────────────────────────────────
    def store_reading(self, source: str, key: str, value: float):
        """Store a single reading immediately."""
        with self._lock:
            try:
                conn = get_conn()
                conn.execute(
                    "INSERT INTO readings (ts, source, key, value) VALUES (?,?,?,?)",
                    (time.time(), source, key, value))
                conn.commit()
            except Exception as e:
                print(f"DB write error: {e}")

    def store_snapshot(self, data: dict):
        """
        Store a snapshot of multiple readings (called every ~60s).
        data: {source_key: value}  e.g. {"inverter_pv_power": 2714.0}
        Only runs once per minute to avoid bloating the DB.
        """
        now = time.time()
        minute = int(now // 60)
        if minute == self._last_minute:
            return
        self._last_minute = minute

        STORE_KEYS = {
            # inverter
            "inverter_pv_power", "inverter_ac_out_active_power", "inverter_grid_power",
            "inverter_battery_voltage", "inverter_battery_current", "inverter_battery_capacity",
            "inverter_pv_input_voltage", "inverter_inverter_heatsink_temp",
            "inverter_pv_energy", "inverter_load_energy", "inverter_grid_energy_in",
            "inverter_battery_energy_in", "inverter_battery_energy_out",
            # BMS bank
            "bank_battery_voltage", "bank_battery_soc",
            "bank_total_remaining_capacity",
            "bank_temp_battery_1", "bank_temp_battery_2", "bank_temp_mos",
            # BMS 1 & 2
            "bms1_battery_voltage", "bms1_battery_soc",
            "bms1_temp_battery_1", "bms1_temp_mos",
            "bms2_battery_voltage", "bms2_battery_soc",
            "bms2_temp_battery_1", "bms2_temp_mos",
        }
        rows = []
        for key, value in data.items():
            if key in STORE_KEYS and value is not None:
                try:
                    rows.append((now, key.split("_")[0], key, float(value)))
                except (TypeError, ValueError):
                    pass

        if not rows:
            return

        with self._lock:
            try:
                conn = get_conn()
                conn.executemany(
                    "INSERT INTO readings (ts, source, key, value) VALUES (?,?,?,?)",
                    rows)
                conn.commit()
            except Exception as e:
                print(f"DB snapshot error: {e}")

    def update_daily_energy(self, key: str, value: float):
        """Upsert today's energy value."""
        today = date.today().isoformat()
        with self._lock:
            try:
                conn = get_conn()
                conn.execute(
                    "INSERT INTO daily_energy (date, key, value) VALUES (?,?,?) "
                    "ON CONFLICT(date,key) DO UPDATE SET value=excluded.value",
                    (today, key, value))
                conn.commit()
            except Exception as e:
                print(f"DB daily energy error: {e}")

    def update_daily_energy_for(self, day: str, key: str, value: float):
        """Upsert a specific day's energy value (used at midnight rollover)."""
        with self._lock:
            try:
                conn = get_conn()
                conn.execute(
                    "INSERT INTO daily_energy (date, key, value) VALUES (?,?,?) "
                    "ON CONFLICT(date,key) DO UPDATE SET value=excluded.value",
                    (day, key, value))
                conn.commit()
            except Exception as e:
                print(f"DB daily energy(day) error: {e}")

    def add_alert(self, level: str, message: str):
        """Store an alert (level: info / warning / critical)."""
        with self._lock:
            try:
                conn = get_conn()
                conn.execute(
                    "INSERT INTO alerts (ts, level, message) VALUES (?,?,?)",
                    (time.time(), level, message))
                conn.commit()
            except Exception as e:
                print(f"DB alert error: {e}")

    # ── Read ──────────────────────────────────────────────────────────────
    def get_history(self, key: str, hours: int = 24) -> list:
        """Return list of {ts, value} for the last N hours."""
        since = time.time() - hours * 3600
        with self._lock:
            try:
                conn = get_conn()
                rows = conn.execute(
                    "SELECT ts, value FROM readings WHERE key=? AND ts>=? ORDER BY ts",
                    (key, since)).fetchall()
                return [{"ts": r["ts"] * 1000, "v": r["value"]} for r in rows]
            except Exception as e:
                print(f"DB history error: {e}")
                return []

    def get_daily_energy(self, days: int = 7) -> list:
        """Return last N days of energy totals."""
        with self._lock:
            try:
                conn = get_conn()
                rows = conn.execute(
                    "SELECT date, key, value FROM daily_energy ORDER BY date DESC LIMIT ?",
                    (days * 10,)).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                print(f"DB daily energy read error: {e}")
                return []

    def get_today_energy(self) -> dict:
        """Return today's energy totals."""
        today = date.today().isoformat()
        with self._lock:
            try:
                conn = get_conn()
                rows = conn.execute(
                    "SELECT key, value FROM daily_energy WHERE date=?", (today,)).fetchall()
                return {r["key"]: r["value"] for r in rows}
            except Exception as e:
                return {}

    def get_alerts(self, limit: int = 20, unacked_only: bool = False) -> list:
        """Return recent alerts."""
        with self._lock:
            try:
                conn = get_conn()
                where = "WHERE acked=0" if unacked_only else ""
                rows = conn.execute(
                    f"SELECT * FROM alerts {where} ORDER BY ts DESC LIMIT ?",
                    (limit,)).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                return []

    def ack_alert(self, alert_id: int):
        with self._lock:
            try:
                conn = get_conn()
                conn.execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))
                conn.commit()
            except: pass

    def purge_old(self, days: int = 30):
        """Delete readings older than N days (run nightly)."""
        cutoff = time.time() - days * 86400
        with self._lock:
            try:
                conn = get_conn()
                conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM alerts WHERE ts < ? AND acked=1", (cutoff,))
                conn.commit()
            except Exception as e:
                print(f"DB purge error: {e}")


# Singleton instance
db = SolarDB()
