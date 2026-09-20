"""
Maps a config.ini protocol/brand string to an adapter class.

Adding a new brand: implement InverterAdapter or BatteryAdapter in a sibling
module under devices/inverters/ or devices/batteries/, then register it here.
Nothing outside this file needs to change to pick it up — solar_bridge.py
only ever asks the registry for a class by name.
"""

from .inverters.voltronic_pi30 import VoltronicPI30
from .batteries.jk_bms import JKBMS

# key = the "protocol"/"brand" string used in config.ini
INVERTER_ADAPTERS = {
    "PI30": VoltronicPI30,   # Voltronic / Axpert / MPP Solar / EASUN / PowMr clones
}

BATTERY_ADAPTERS = {
    "JKBMS": JKBMS,
}


def get_inverter_class(protocol: str):
    try:
        return INVERTER_ADAPTERS[protocol.strip().upper()]
    except KeyError:
        raise ValueError(
            f"Unknown inverter protocol {protocol!r}. "
            f"Supported: {', '.join(INVERTER_ADAPTERS)}. "
            "To add a new brand, implement an InverterAdapter under "
            "devices/inverters/ and register it in devices/registry.py — "
            "never guess its command set without a datasheet or live capture."
        )


def get_battery_class(brand: str):
    try:
        return BATTERY_ADAPTERS[brand.strip().upper()]
    except KeyError:
        raise ValueError(
            f"Unknown battery/BMS brand {brand!r}. "
            f"Supported: {', '.join(BATTERY_ADAPTERS)}. "
            "To add a new brand, implement a BatteryAdapter under "
            "devices/batteries/ and register it in devices/registry.py — "
            "never guess its protocol without a datasheet or live capture."
        )
