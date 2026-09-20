"""Abstract adapter interfaces that every inverter/battery driver implements."""

from abc import ABC, abstractmethod


class InverterAdapter(ABC):
    """
    One physical inverter. A concrete adapter (e.g. VoltronicPI30) owns the
    wire protocol; solar_bridge.py only calls this interface, so it never
    needs to know whether it's talking PI30, Modbus, or anything else.

    QPIGS_FIELDS / DERIVED / SET_COMMANDS stay class attributes (not part of
    this interface) because the *shape* of live data legitimately differs
    per brand — the poll loop reads them off the instance, same as today.
    """

    @abstractmethod
    def open(self) -> bool:
        """Open the underlying transport (HID/serial/etc). Return True on success."""

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def query_status(self) -> dict | None:
        """One normalized reading cycle (equivalent of QPIGS). None on failure."""

    @abstractmethod
    def send_command(self, cmd: str) -> bool:
        """Send a raw/adapter-specific command, return True if acknowledged."""


class BatteryAdapter(ABC):
    """
    One physical battery pack/BMS. A concrete adapter (e.g. JKBMS) owns the
    wire protocol. Multiple instances are supported — see
    solar_bridge.py's battery list, built from config.ini's [battery1],
    [battery2], ... sections — so "add an extra battery" is a config change,
    not a code change, as long an adapter for that BMS brand exists.
    """

    @abstractmethod
    def open(self) -> bool:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def read(self) -> dict | None:
        """One normalized reading for this pack. None on failure/no data."""
