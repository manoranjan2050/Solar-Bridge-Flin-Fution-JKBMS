# Solar Bridge — Flin Fution Inverter + JKBMS → Home Assistant

Self-hosted replacement for Solar Assistant. Runs on Raspberry Pi 4, reads data from inverter and BMS over USB, publishes to MQTT with full Home Assistant auto-discovery and controls.

---

## Hardware

| Device | Connection | Port | USB ID |
|---|---|---|---|
| Flin Fution Inverter (Voltronic/Axpert) | USB HID cable | `/dev/hidraw0` | `0665:5161` |
| JKBMS (new protocol) | CH340 USB-RS485 adapter | `/dev/ttyUSB0` | `1a86:7523` |
| Raspberry Pi 4 | Ethernet | `192.168.1.32` | — |
| Home Assistant | Network | `192.168.1.82` | — |

---

## Software Architecture

```
Raspberry Pi 4 (192.168.1.32)
│
├── /opt/solar-bridge/
│   ├── solar_bridge.py      ← Main monitoring + control script
│   ├── config.ini           ← MQTT credentials, port settings
│   ├── energy.json          ← Persistent kWh totals (survives restarts)
│   ├── venv/                ← Python virtualenv (pyserial + paho-mqtt)
│   └── solar-bridge.service ← Systemd service (auto-start on boot)
│
└── systemd → solar-bridge.service
        polls inverter every 10s (QPIGS)
        polls settings every 60s (QPIRI)
        polls device mode every 15s (QMOD)
        reads BMS every 10s (passive broadcast)
        publishes to MQTT → Home Assistant
```

---

## Sensors in Home Assistant

### Flin Fution Inverter

| Sensor | MQTT Topic | Notes |
|---|---|---|
| PV Voltage | `solar/inverter/pv_input_voltage` | |
| PV Current | `solar/inverter/pv_input_current` | |
| PV Power | `solar/inverter/pv_power` | Calculated: V×I |
| PV Energy | `solar/inverter/pv_energy` | kWh accumulated |
| AC Output Voltage | `solar/inverter/ac_out_voltage` | |
| AC Output Frequency | `solar/inverter/ac_out_frequency` | |
| Load Power | `solar/inverter/ac_out_active_power` | W |
| Load Apparent Power | `solar/inverter/ac_out_apparent_power` | VA |
| Load Percentage | `solar/inverter/load_percent` | % |
| Load Energy | `solar/inverter/load_energy` | kWh accumulated |
| Grid Voltage | `solar/inverter/grid_voltage` | |
| Grid Frequency | `solar/inverter/grid_frequency` | |
| Grid Power | `solar/inverter/grid_power` | Estimated |
| Grid Energy In | `solar/inverter/grid_energy_in` | kWh accumulated |
| Battery Voltage | `solar/inverter/battery_voltage` | |
| Battery Current | `solar/inverter/battery_current` | + charge / - discharge |
| Battery Power | `solar/inverter/battery_power` | |
| Battery Charge Current | `solar/inverter/battery_charge_current` | |
| Battery Discharge Current | `solar/inverter/battery_discharge_current` | |
| Battery Capacity | `solar/inverter/battery_capacity` | % from inverter |
| Battery Energy In | `solar/inverter/battery_energy_in` | kWh accumulated |
| Battery Energy Out | `solar/inverter/battery_energy_out` | kWh accumulated |
| Bus Voltage | `solar/inverter/bus_voltage` | |
| Heatsink Temp | `solar/inverter/inverter_heatsink_temp` | |
| Device Mode | `solar/inverter/device_mode` | Grid / Battery / Line |
| Max Charge Current | `solar/inverter/max_charge_current` | From QPIRI |
| Battery Float Voltage | `solar/inverter/battery_float_voltage` | |
| Battery Bulk Voltage | `solar/inverter/battery_bulk_voltage` | |
| Battery Cutoff Voltage | `solar/inverter/battery_cutoff_voltage` | |
| Serial Number | `solar/inverter/serial_number` | |

### JK BMS

| Sensor | MQTT Topic | Notes |
|---|---|---|
| Battery Voltage | `solar/bms/battery_voltage` | Pack voltage |
| State of Charge | `solar/bms/battery_soc` | % |
| Cycle Count | `solar/bms/battery_cycles` | |
| Remaining Capacity | `solar/bms/remaining_capacity_ah` | Ah |
| Design Capacity | `solar/bms/design_capacity_ah` | Ah |
| State of Health | `solar/bms/state_of_health` | % |
| Temperature 1 | `solar/bms/temp_battery_1` | °C |
| Temperature 2 | `solar/bms/temp_battery_2` | °C |
| Temperature MOS | `solar/bms/temp_mos` | MOSFET °C |
| Cell Voltage Min | `solar/bms/cell_voltage_min` | V |
| Cell Voltage Max | `solar/bms/cell_voltage_max` | V |
| Cell Voltage Avg | `solar/bms/cell_voltage_avg` | V |
| Cell Voltage Diff | `solar/bms/cell_voltage_diff` | V |
| Cell 1–16 Voltages | `solar/bms/cell_01_voltage` .. `cell_16_voltage` | V each |

---

## Controls in Home Assistant

These appear as editable entities in HA and send commands directly to the inverter.

| Control | HA Entity Type | MQTT Command Topic | Inverter Command |
|---|---|---|---|
| Output Source Priority | Select | `solar/inverter/control/output_priority/set` | `POPCD00/01/02` |
| Charger Source Priority | Select | `solar/inverter/control/charger_priority/set` | `PPCP00/01/02/03` |
| Max Charge Current | Number | `solar/inverter/control/max_charge_current/set` | `MUCHGCXXX` |
| Max Grid Charge Current | Number | `solar/inverter/control/max_grid_charge_current/set` | `MCHGCXXX` |
| Battery Float Voltage | Number | `solar/inverter/control/battery_float_voltage/set` | `PBFTxx.x` |
| Battery Bulk Voltage | Number | `solar/inverter/control/battery_bulk_voltage/set` | `PBCVxx.x` |
| Battery Shutdown Voltage | Number | `solar/inverter/control/battery_shutdown_voltage/set` | `PSDVxx.x` |
| Battery Recharge Voltage | Number | `solar/inverter/control/battery_recharge_voltage/set` | `PBDVxx.x` |

**Priority values:**
- Output: `Grid first` (Utility) / `Solar first` / `SBU` (Solar→Battery→Utility)
- Charger: `Grid first` / `Solar first` / `Solar+Grid` / `Solar only`

---

## Inverter Protocol Details

- **Protocol:** Voltronic / Axpert PI30
- **Interface:** USB HID (`/dev/hidraw0`)
- **CRC:** Voltronic CRC-16 lookup table (NOT standard CRC-16/XMODEM)
- **Frame format:** `[command bytes][CRC 2 bytes][0x0D]`
- **HID packet format:** 9 bytes write (0x00 + 8 data), 8 bytes read (raw data, no count prefix)
- **Commands used:**
  - `QPIGS` — real-time data (every 10s)
  - `QPIRI` — rated info / current settings (every 60s)
  - `QMOD` — device mode (every 15s)
  - `QID` — serial number (once at startup)

### QPIRI Field Positions (confirmed live)
```
Index: 0=220.0 | 1=22.7 | 2=220.0 | 3=50.0 | 4=22.7 | 5=5000 | 6=5000
       7=48.0  | 8=46.0 | 9=48.0  | 10=55.1| 11=54.0| 12=2   | 13=20
       14=080  | 15=0   | 16=0    | 17=1   | 18=1   | 19=01  | 20=0
       21=0    | 22=54.0| 23=0    | 24=1L

Key fields:
  [7]  battery_cutoff_voltage      = 48.0V  (shutdown)
  [8]  battery_back_voltage        = 46.0V  (back to discharge)
  [10] battery_bulk_voltage        = 55.1V  (absorption)
  [11] battery_float_voltage       = 54.0V
  [13] max_ac_charge_current       = 20A    (grid charge limit)
  [14] max_charge_current          = 80A    (total charge limit)
  [15] output_source_priority      = 0      (0=Grid, 1=Solar, 2=SBU)
  [16] charger_source_priority     = 0      (0=Grid, 1=Solar, 2=Solar+Grid, 3=Solar only)
  [22] battery_redischarge_voltage = 54.0V
```

---

## JKBMS Protocol Details

- **Protocol:** JK new broadcast protocol (55 AA EB 90)
- **Interface:** RS485 via CH340 USB adapter (`/dev/ttyUSB0`)
- **Baud rate:** 115200
- **Mode:** Passive — BMS broadcasts automatically every ~1 second, no request needed
- **Frame header:** `55 AA EB 90`
- **Frame type used:** `0x02` (cell info)

### Type-02 Frame Byte Offsets (confirmed from live capture)
```
+0    : 55 AA EB 90       (header)
+4    : 02                (type = cell info)
+5    : counter byte
+6    : cell voltages start
+6 to +37 : 16 × uint16 LE   cell voltages in mV (3308–3391 mV range)
+144  : uint32 LE         MOS temperature (0.1°C, range check 200–800)
+150  : uint32 LE         pack voltage (mV, confirmed: 52979 = 52.979V)
+154  : uint32 LE         remaining capacity (mAh, sanity < 1,000,000)
+162  : uint16 LE         temperature 1 (0.1°C)
+164  : uint16 LE         temperature 2 (0.1°C)
+182  : uint32 LE         SOC % (0–100)
+186  : uint32 LE         design capacity (mAh)
+190  : uint32 LE         cycle count
```

---

## Current Inverter Settings (as configured)

| Setting | Value |
|---|---|
| Battery absorption charge voltage | 55.1V |
| Battery float charge voltage | 54.0V |
| Battery shutdown voltage | 48.0V |
| Back-to-grid battery voltage | 46.0V |
| Max charge current | 80A |
| Max grid charge current | 20A |
| Output source priority | Grid first (Utility first) |
| Charger source priority | Grid first |

---

## Known Issues / TODO

| Issue | Status | Notes |
|---|---|---|
| BMS current showing 0A | Open | Frame offset not confirmed — need reading with known current |
| BMS SOC fluctuates 36%↔46% | Open | Catching different frame types; type-01 vs type-02 |
| BMS remaining capacity unstable | Partial | Sanity cap at 1000Ah; offset 154 may shift between frame types |
| QPIRI charger priority shows Grid first | Open | Inverter may have reverted; was Solar first in Solar Assistant |

---

## Quick Commands (SSH into Pi)

```bash
# Check service status
sudo systemctl status solar-bridge

# Watch live logs
sudo journalctl -u solar-bridge -f

# Restart service
sudo systemctl restart solar-bridge

# Edit config (MQTT host, ports, poll intervals)
sudo nano /opt/solar-bridge/config.ini

# View energy totals
cat /opt/solar-bridge/energy.json

# Check USB devices
lsusb
ls -la /dev/hidraw* /dev/ttyUSB*

# Run diagnostic (identify device ports)
sudo /opt/solar-bridge/venv/bin/python /opt/solar-bridge/detect_devices.sh
```

---

## File Index

| File | Location | Purpose |
|---|---|---|
| `solar_bridge.py` | `/opt/solar-bridge/` | Main script |
| `config.ini` | `/opt/solar-bridge/` | MQTT + device settings |
| `energy.json` | `/opt/solar-bridge/` | Persistent energy totals |
| `solar-bridge.service` | `/etc/systemd/system/` | Systemd unit |
| `99-solar.rules` | `/etc/udev/rules.d/` | USB device permissions |
| `detect_devices.sh` | `/opt/solar-bridge/` | USB port detector |

---

## Setup From Scratch

If you need to reinstall on a fresh Raspberry Pi OS Lite:

```bash
# Copy files to Pi then run:
bash setup.sh

# Edit config with your HA IP and MQTT credentials
nano /opt/solar-bridge/config.ini

# Start service
sudo systemctl start solar-bridge
sudo journalctl -u solar-bridge -f
```

---

## Development / Resuming Work

Working directory on Windows: `C:\Users\MANORANJAN\soalrpi\`

To deploy changes:
```python
# From C:\Users\MANORANJAN\soalrpi\
python deploy.py    # uploads solar_bridge.py and restarts service
```

Or manually:
```bash
scp solar_bridge.py manoranjan@192.168.1.32:/opt/solar-bridge/
ssh manoranjan@192.168.1.32 "sudo systemctl restart solar-bridge"
```
