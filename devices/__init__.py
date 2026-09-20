"""
Device-adapter layer for Solar Bridge.

Physical Interface -> Protocol Driver -> Device Adapter -> Normalized Data Model -> Application

Every inverter/BMS brand plugs in here as an adapter behind a common interface
(InverterAdapter / BatteryAdapter) instead of being hard-coded into
solar_bridge.py. This is what lets the project support more than one
inverter or battery brand, and more than two battery packs, through
config.ini alone.

Do not invent register maps / protocol offsets for a new adapter. Every
existing offset here was confirmed against real hardware (see README.md
"Protocol notes"). A new adapter needs the same: a manufacturer datasheet
or a live capture, or it stays unimplemented.
"""

from .base import InverterAdapter, BatteryAdapter
from .registry import get_inverter_class, get_battery_class, INVERTER_ADAPTERS, BATTERY_ADAPTERS

__all__ = [
    "InverterAdapter", "BatteryAdapter",
    "get_inverter_class", "get_battery_class",
    "INVERTER_ADAPTERS", "BATTERY_ADAPTERS",
]
