# 🏠 Solar Bridge Card — custom card for Home Assistant

One Lovelace card with **everything from the Solar Bridge dashboard's System Overview**:

| Section | What you get |
|---|---|
| **Analog gauges** | Solar / Load / Battery / Grid — needle + arc, colour changes with the live value (same tiers as the dashboard) |
| **Power Flow** | Animated cross diagram — dots flow Solar→INV→Load, Grid→INV, Battery charge/discharge direction-aware |
| **Energy Totals** | PV · Load · Grid In · Battery In/Out (kWh) |
| **Solar Generation chart** | Last 24 h PV power curve, drawn from HA's own history |
| **Battery Status** | Charging ⚡ / Discharging 🔻 / Resting — with "≈ 2.5 h to full" / "≈ 6 h of backup left" estimate |
| **Inverter Status** | Device mode, AC output, bus voltage, heatsink temp |

Every section can be switched on/off, every entity can be remapped, and it follows your HA theme (light & dark).

> Requires the Solar Bridge MQTT integration already working — the card only *displays*
> the sensors that Solar Bridge auto-discovers into Home Assistant.

---

## 📥 Install

### ✅ Method 1 — HACS custom repository (recommended, 2 minutes)

1. **HACS → ⋮ (top-right menu) → Custom repositories**
2. Fill in:
   - **Repository:** `https://github.com/manoranjan2050/Solar-Bridge-Flin-Fution-JKBMS`
   - **Type:** `Dashboard`
3. Click **Add**, close the dialog
4. In HACS, search **"Solar Bridge Card"** → open it → **Download**
5. Reload when prompted (HACS registers the dashboard resource automatically)

Updates arrive through HACS like any other card.

> Older HACS versions: if the card doesn't render after download, add the resource manually —
> Settings → Dashboards → ⋮ → Resources → `+` →
> URL `/hacsfiles/Solar-Bridge-Flin-Fution-JKBMS/solar-bridge-card.js`, type **JavaScript module**.

### Method 2 — Manual copy (no HACS)

**Step 1.** Put [`dist/solar-bridge-card.js`](../dist/solar-bridge-card.js) into your HA `config/www/` folder (Samba / File editor add-on / SCP), or via SSH on the HA box:
```bash
mkdir -p /config/www
wget -O /config/www/solar-bridge-card.js \
  https://raw.githubusercontent.com/manoranjan2050/Solar-Bridge-Flin-Fution-JKBMS/master/dist/solar-bridge-card.js
```

**Step 2.** Register the resource: **Settings → Dashboards → ⋮ (top-right) → Resources → + Add Resource**
- URL: `/local/solar-bridge-card.js`
- Type: **JavaScript module**

> Don't see "Resources"? Enable **Advanced Mode** in your user profile first.

Then hard-refresh the browser (Ctrl+Shift+R) / restart the HA companion app.

### Add the card

Edit a dashboard → **+ Add Card** → search **"Solar Bridge Card"** (or scroll to *Custom: Solar Bridge Card*), or add it via YAML:

```yaml
type: custom:solar-bridge-card
title: Solar Bridge
```

That's it — if your entity names match the defaults, everything lights up immediately.

---

## 🔎 Step 4 (important): check your entity names

The card ships with default entity ids, but **Home Assistant may have named yours slightly
differently** (e.g. `sensor.battery_voltage_2` when two sensors share a name). To check:

**Developer Tools → States** → filter for `pv_power`, `state_of_charge`, etc.
The Solar Bridge devices are under **Settings → Devices & Services → MQTT →
Flin Fution Inverter / Battery Bank**, where every entity id is listed.

Then override only the ones that differ:

```yaml
type: custom:solar-bridge-card
title: Solar Bridge
entities:
  battery_soc: sensor.state_of_charge_3        # ← your Battery Bank SOC
  battery_voltage: sensor.battery_voltage_2    # ← your Battery Bank voltage
```

### All mappable entities (with their defaults)

| Key | Default entity | Used by |
|---|---|---|
| `pv_power` | `sensor.pv_power` | Gauge, flow, chart |
| `pv_voltage` / `pv_current` | `sensor.pv_input_voltage` / `sensor.pv_input_current` | Gauge subtitle |
| `load_power` | `sensor.ac_out_active_power` | Gauge, flow |
| `load_percent` | `sensor.load_percent` | Gauge subtitle |
| `grid_power` / `grid_voltage` | `sensor.grid_power` / `sensor.grid_voltage` | Gauge, flow |
| `battery_soc` | `sensor.state_of_charge` | Gauge, flow, battery panel |
| `battery_voltage` / `battery_current` | `sensor.battery_voltage` / `sensor.battery_current` | Battery panel, flow direction |
| `remaining_ah` / `design_ah` | `sensor.total_remaining_capacity` / `sensor.total_design_capacity` | Time-to-full / backup-left estimate |
| `device_mode` | `sensor.device_mode` | Inverter panel |
| `ac_voltage` / `ac_frequency` | `sensor.ac_out_voltage` / `sensor.ac_out_frequency` | Inverter panel |
| `bus_voltage` / `heatsink_temp` | `sensor.bus_voltage` / `sensor.inverter_heatsink_temp` | Inverter panel |
| `energy_pv` / `energy_load` / `energy_grid_in` | `sensor.pv_energy` / `sensor.load_energy` / `sensor.grid_energy_in` | Energy totals |
| `energy_batt_in` / `energy_batt_out` | `sensor.battery_energy_in` / `sensor.battery_energy_out` | Energy totals |

---

## ⚙️ Full configuration example

```yaml
type: custom:solar-bridge-card
title: My Solar System
show:                 # turn sections on/off (all default to true)
  gauges: true
  flow: true
  energy: true
  chart: true
  battery: true
  inverter: true
gauge_max:            # full-scale watts for the needle gauges
  pv: 4000
  load: 5000
  grid: 5000
chart_hours: 24       # solar chart window (needs HA history for pv_power)
entities:             # override any default (see table above)
  battery_soc: sensor.state_of_charge_2
```

**Minimal "gauges only" card:**
```yaml
type: custom:solar-bridge-card
title: Solar
show: { flow: false, energy: false, chart: false, battery: false, inverter: false }
```

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| "Custom element doesn't exist: solar-bridge-card" | Resource not registered or not loaded — re-check step 2, hard-refresh (Ctrl+Shift+R) |
| Card shows `--` everywhere | Entity names differ — do step 4 and map them in `entities:` |
| Battery gauge stuck at 0% | `battery_soc` points at the wrong entity (look for the Battery Bank's *State of Charge*, may have a `_2`/`_3` suffix) |
| Chart says "No history yet" | The `pv_power` entity is excluded from the recorder, or HA was just restarted — give it an hour |
| Chart says "History unavailable" | Older HA versions: remove `&no_attributes` support issue by updating HA |
| Sections overflow on phone | They auto-stack below 460 px width; put the card in a single-column view for best results |
| Values lag | Card updates instantly on every state change HA receives; check the bridge is publishing (Solar Bridge dashboard → Logs) |

---

## 💡 Tips

- Pair it with HA's **Energy Dashboard** (Settings → Dashboards → Energy) using the same
  `pv_energy` / `grid_energy_in` / battery energy sensors for official long-term statistics.
- The card is theme-aware — try it with your dark theme; the accent colours match the
  Solar Bridge web dashboard.
- You can place **two cards** with different sections (e.g. gauges+flow on top of a grid,
  battery+inverter in a side column).
