/* ╔══════════════════════════════════════════════════════════════════════╗
 * ║  SOLAR BRIDGE CARD — custom Lovelace card for Home Assistant         ║
 * ║  Power flow · analog gauges · energy totals · solar chart ·          ║
 * ║  battery status · inverter status — all in one card.                 ║
 * ║                                                                      ║
 * ║  Repo: github.com/manoranjan2050/Solar-Bridge-Flin-Fution-JKBMS      ║
 * ║  Install guide: ha-card/README.md                                    ║
 * ╚══════════════════════════════════════════════════════════════════════╝ */

(() => {
"use strict";

const CARD_VERSION = "1.0.0";
console.info(`%c SOLAR-BRIDGE-CARD %c v${CARD_VERSION} `,
  "background:#f59e0b;color:#000;font-weight:700", "background:#1a2234;color:#f59e0b");

/* ── Default entity mapping (override any of these in the card config) ────── */
const DEFAULT_ENTITIES = {
  pv_power:        "sensor.pv_power",
  pv_voltage:      "sensor.pv_input_voltage",
  pv_current:      "sensor.pv_input_current",
  load_power:      "sensor.ac_out_active_power",
  load_percent:    "sensor.load_percent",
  grid_power:      "sensor.grid_power",
  grid_voltage:    "sensor.grid_voltage",
  battery_voltage: "sensor.battery_voltage",
  battery_current: "sensor.battery_current",
  battery_soc:     "sensor.state_of_charge",
  remaining_ah:    "sensor.total_remaining_capacity",
  design_ah:       "sensor.total_design_capacity",
  device_mode:     "sensor.device_mode",
  ac_voltage:      "sensor.ac_out_voltage",
  ac_frequency:    "sensor.ac_out_frequency",
  bus_voltage:     "sensor.bus_voltage",
  heatsink_temp:   "sensor.inverter_heatsink_temp",
  energy_pv:       "sensor.pv_energy",
  energy_load:     "sensor.load_energy",
  energy_grid_in:  "sensor.grid_energy_in",
  energy_batt_in:  "sensor.battery_energy_in",
  energy_batt_out: "sensor.battery_energy_out",
};

const DEFAULT_CONFIG = {
  title: "Solar Bridge",
  show: { gauges: true, flow: true, energy: true, chart: true, battery: true, inverter: true },
  gauge_max: { pv: 4000, load: 5000, grid: 5000 },
  chart_hours: 24,
  entities: {},
};

/* ── Colour scales (same tiers as the Solar Bridge dashboard) ─────────────── */
const pvColor   = w => w < 100 ? "#64748b" : w < 800 ? "#facc15" : w < 2000 ? "#f59e0b" : "#10b981";
const gridColor = w => w < 50 ? "#10b981" : w < 1000 ? "#facc15" : w < 2500 ? "#fb923c" : "#f43f5e";
const socColor  = p => p <= 15 ? "#f43f5e" : p <= 30 ? "#fb923c" : p <= 50 ? "#facc15"
                      : p <= 75 ? "#a3e635" : "#10b981";
const loadColorF = (w, max) => { const p = w / max * 100;
  return p < 40 ? "#10b981" : p < 60 ? "#facc15" : p < 80 ? "#fb923c" : "#f43f5e"; };

const ARC = Math.PI * 40;

class SolarBridgeCard extends HTMLElement {

  static getStubConfig() { return { title: "Solar Bridge" }; }

  setConfig(config) {
    this._config = {
      ...DEFAULT_CONFIG, ...config,
      show:      { ...DEFAULT_CONFIG.show,      ...(config.show || {}) },
      gauge_max: { ...DEFAULT_CONFIG.gauge_max, ...(config.gauge_max || {}) },
      entities:  { ...DEFAULT_ENTITIES,         ...(config.entities || {}) },
    };
    this._built = false;
    this._lastChart = 0;
  }

  getCardSize() { return 10; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  /* ── helpers ─────────────────────────────────────────────────────────── */
  _num(key) {
    const ent = this._config.entities[key];
    const st = ent && this._hass.states[ent];
    if (!st || st.state === "unavailable" || st.state === "unknown") return null;
    const v = parseFloat(st.state);
    return isNaN(v) ? null : v;
  }
  _str(key) {
    const ent = this._config.entities[key];
    const st = ent && this._hass.states[ent];
    return st && st.state !== "unavailable" && st.state !== "unknown" ? st.state : null;
  }
  _el(id) { return this.shadowRoot.getElementById(id); }
  _set(id, txt) { const e = this._el(id); if (e && e.textContent !== txt) e.textContent = txt; }
  _color(id, c) { const e = this._el(id); if (e) e.style.color = c; }

  /* ── build DOM (once) ─────────────────────────────────────────────────── */
  _build() {
    this._built = true;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const S = this._config.show;

    const gauge = (id, label) => `
      <div class="g-wrap">
        <div class="g-dial" id="g-${id}">
          <svg viewBox="0 0 100 62">
            <path d="M 10 52 A 40 40 0 0 1 90 52" fill="none" stroke="var(--divider-color,#333)"
                  stroke-width="9" stroke-linecap="round"/>
            <path class="gfill" d="M 10 52 A 40 40 0 0 1 90 52" fill="none" stroke="#64748b"
                  stroke-width="9" stroke-linecap="round"
                  stroke-dasharray="${ARC}" stroke-dashoffset="${ARC}"/>
            <line class="gneedle" x1="50" y1="52" x2="50" y2="20" stroke="#94a3b8"
                  stroke-width="2.5" stroke-linecap="round" style="transform:rotate(-90deg)"/>
            <circle cx="50" cy="52" r="4" fill="var(--card-background-color,#111)"
                    stroke="#64748b" stroke-width="1.5"/>
          </svg>
        </div>
        <div class="g-val" id="gv-${id}">--</div>
        <div class="g-lbl">${label}</div>
        <div class="g-sub" id="gs-${id}"></div>
      </div>`;

    const node = (id, icon, label) => `
      <div class="fnode" id="fn-${id}">
        <div class="fnode-ic">${icon}</div>
        <div class="fnode-v" id="fnv-${id}">--</div>
        <div class="fnode-l">${label}</div>
      </div>`;

    const ebox = (id, icon, label, cls) => `
      <div class="ebox ${cls}"><div class="e-val" id="e-${id}">--</div>
      <div class="e-lbl">${icon} ${label}</div></div>`;

    const irow = (id, label) => `
      <div class="irow"><span class="il">${label}</span><span class="iv" id="${id}">--</span></div>`;

    this.shadowRoot.innerHTML = `
    <style>
      :host { --sb-solar:#f59e0b; --sb-batt:#10b981; --sb-grid:#3b82f6; --sb-load:#f43f5e;
              --sb-dim:var(--secondary-text-color,#94a3b8); }
      ha-card { padding: 14px 16px 16px; }
      .title { font-size:1.05rem; font-weight:700; color:var(--sb-solar);
               display:flex; align-items:center; gap:8px; margin-bottom:10px; }
      .sect { margin-top:14px; }
      .sect-t { font-size:.68rem; letter-spacing:.08em; text-transform:uppercase;
                color:var(--sb-dim); margin-bottom:8px; }

      /* gauges */
      .gauges { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
      .g-wrap { text-align:center; }
      .g-dial svg { width:100%; max-width:110px; }
      .gfill { transition:stroke-dashoffset 1s cubic-bezier(.4,0,.2,1), stroke .5s; }
      .gneedle { transition:transform 1s cubic-bezier(.34,1.3,.5,1), stroke .5s;
                 transform-origin:50px 52px; }
      .g-val { font-size:1.05rem; font-weight:700; line-height:1.1; }
      .g-lbl { font-size:.7rem; color:var(--sb-dim); }
      .g-sub { font-size:.65rem; color:var(--sb-dim); opacity:.8; min-height:.9em; }

      /* power flow */
      .flow { position:relative; height:230px; max-width:420px; margin:0 auto; }
      .fnode { position:absolute; width:96px; text-align:center; background:var(--secondary-background-color,#1c2333);
               border:1.5px solid var(--divider-color,#333); border-radius:12px; padding:8px 4px;
               transition:box-shadow .4s, border-color .4s; }
      .fnode.live { box-shadow:0 0 14px -2px var(--nc,#fff5); border-color:var(--nc,#888); }
      .fnode-ic { font-size:1.15rem; }
      .fnode-v { font-weight:700; font-size:.9rem; }
      .fnode-l { font-size:.62rem; color:var(--sb-dim); }
      #fn-solar { top:0;   left:50%; transform:translateX(-50%); --nc:var(--sb-solar); }
      #fn-batt  { bottom:0;left:50%; transform:translateX(-50%); --nc:var(--sb-batt); }
      #fn-load  { right:0; top:50%;  transform:translateY(-50%); --nc:var(--sb-load); }
      #fn-grid  { left:0;  top:50%;  transform:translateY(-50%); --nc:var(--sb-grid); }
      .fcenter { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
                 width:46px; height:46px; border-radius:50%; display:flex; align-items:center;
                 justify-content:center; font-size:.6rem; font-weight:700; color:var(--sb-dim);
                 background:var(--secondary-background-color,#1c2333);
                 border:1.5px solid var(--divider-color,#333); z-index:2; }
      .fline { position:absolute; background:var(--divider-color,#333); }
      .fl-v { width:3px; left:50%; transform:translateX(-50%); }
      .fl-h { height:3px; top:50%; transform:translateY(-50%); }
      #fl-solar { top:58px;  height:48px; }
      #fl-batt  { bottom:58px; height:48px; }
      #fl-load  { right:104px; width:calc(50% - 130px); }
      #fl-grid  { left:104px;  width:calc(50% - 130px); }
      .dot { position:absolute; width:8px; height:8px; border-radius:50%; opacity:0;
             left:50%; top:50%; transform:translate(-50%,-50%); }
      .fline.on .dot { opacity:1; animation:1.2s linear infinite; }
      #fl-solar.on .dot { animation-name:dDown; } #fl-batt.on .dot  { animation-name:dDown; }
      #fl-batt.on.rev .dot { animation-name:dUp; }
      #fl-load.on .dot  { animation-name:dRight; } #fl-grid.on .dot { animation-name:dRight; }
      @keyframes dDown  { from{top:-4px;opacity:0} 20%{opacity:1} 80%{opacity:1} to{top:calc(100% + 4px);opacity:0} }
      @keyframes dUp    { from{top:calc(100% + 4px);opacity:0} 20%{opacity:1} 80%{opacity:1} to{top:-4px;opacity:0} }
      @keyframes dRight { from{left:-4px;opacity:0} 20%{opacity:1} 80%{opacity:1} to{left:calc(100% + 4px);opacity:0} }

      /* energy boxes */
      .energy { display:grid; grid-template-columns:repeat(5,1fr); gap:6px; }
      .ebox { text-align:center; background:var(--secondary-background-color,#1c2333);
              border-radius:10px; padding:8px 2px; }
      .e-val { font-size:.85rem; font-weight:700; }
      .e-lbl { font-size:.6rem; color:var(--sb-dim); margin-top:2px; }
      .ebox.solar .e-val{color:var(--sb-solar)} .ebox.load .e-val{color:var(--sb-load)}
      .ebox.grid .e-val{color:var(--sb-grid)}  .ebox.batt .e-val{color:var(--sb-batt)}

      /* chart */
      .chart { width:100%; height:110px; }
      .chart-empty { font-size:.75rem; color:var(--sb-dim); text-align:center; padding:20px 0; }

      /* battery + inverter status */
      .two { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
      .panel { background:var(--secondary-background-color,#1c2333); border-radius:12px; padding:10px 12px; }
      .bstate { text-align:center; font-size:1.05rem; font-weight:700; padding:4px 0 2px; }
      .beta { text-align:center; font-size:.68rem; color:var(--sb-dim); min-height:1em; margin-bottom:4px; }
      .pulse { animation:pl 1.5s ease-in-out infinite; display:inline-block; }
      @keyframes pl { 0%,100%{opacity:1} 50%{opacity:.4} }
      .irow { display:flex; justify-content:space-between; padding:4px 0;
              border-bottom:1px solid var(--divider-color,#2a3142); font-size:.78rem; }
      .irow:last-child { border-bottom:none; }
      .il { color:var(--sb-dim); } .iv { font-weight:600; }

      @media (max-width:460px) {
        .gauges { grid-template-columns:repeat(2,1fr); }
        .energy { grid-template-columns:repeat(3,1fr); }
        .two { grid-template-columns:1fr; }
      }
    </style>
    <ha-card>
      <div class="title">☀️ ${this._config.title}</div>

      ${S.gauges ? `<div class="sect gauges">
        ${gauge("pv","Solar Power")} ${gauge("load","Load")}
        ${gauge("batt","Battery")}   ${gauge("grid","Grid")}
      </div>` : ""}

      ${S.flow ? `<div class="sect">
        <div class="sect-t">Power Flow</div>
        <div class="flow">
          ${node("solar","☀️","Solar")} ${node("load","🔌","Load")}
          ${node("batt","🔋","Battery")} ${node("grid","⚡","Grid")}
          <div class="fcenter">INV</div>
          <div class="fline fl-v" id="fl-solar"><span class="dot" style="background:var(--sb-solar)"></span></div>
          <div class="fline fl-v" id="fl-batt"><span class="dot" style="background:var(--sb-batt)"></span></div>
          <div class="fline fl-h" id="fl-load"><span class="dot" style="background:var(--sb-load)"></span></div>
          <div class="fline fl-h" id="fl-grid"><span class="dot" style="background:var(--sb-grid)"></span></div>
        </div>
      </div>` : ""}

      ${S.energy ? `<div class="sect">
        <div class="sect-t">Energy Totals (kWh)</div>
        <div class="energy">
          ${ebox("pv","☀️","PV","solar")} ${ebox("load","🏠","Load","load")}
          ${ebox("gin","⚡","Grid In","grid")}
          ${ebox("bin","🔋","Batt In","batt")} ${ebox("bout","🪫","Batt Out","batt")}
        </div>
      </div>` : ""}

      ${S.chart ? `<div class="sect">
        <div class="sect-t">Solar Generation — last ${this._config.chart_hours}h</div>
        <div id="chart-box"><div class="chart-empty">loading…</div></div>
      </div>` : ""}

      ${(S.battery || S.inverter) ? `<div class="sect two">
        ${S.battery ? `<div class="panel">
          <div class="sect-t">Battery Status</div>
          <div class="bstate" id="b-state">--</div>
          <div class="beta" id="b-eta">&nbsp;</div>
          ${irow("b-cur","Current")} ${irow("b-pow","Power")}
          ${irow("b-volt","Voltage")} ${irow("b-soc","State of Charge")}
        </div>` : ""}
        ${S.inverter ? `<div class="panel">
          <div class="sect-t">Inverter Status</div>
          ${irow("i-mode","Device Mode")} ${irow("i-ac","AC Output")}
          ${irow("i-bus","Bus Voltage")} ${irow("i-temp","Heatsink Temp")}
        </div>` : ""}
      </div>` : ""}
    </ha-card>`;
  }

  /* ── live update ──────────────────────────────────────────────────────── */
  _update() {
    const S = this._config.show, M = this._config.gauge_max;
    const pv   = this._num("pv_power")        ?? 0;
    const load = this._num("load_power")      ?? 0;
    const grid = this._num("grid_power")      ?? 0;
    const soc  = this._num("battery_soc")     ?? 0;
    const amps = this._num("battery_current") ?? 0;
    const bv   = this._num("battery_voltage") ?? 0;

    if (S.gauges) {
      this._gauge("pv",   pv / M.pv,     pvColor(pv),         `${Math.round(pv)} W`,
                  `${this._fmt("pv_voltage",0,"V")} / ${this._fmt("pv_current",1,"A")}`);
      this._gauge("load", load / M.load, loadColorF(load, M.load), `${Math.round(load)} W`,
                  `${this._fmt("load_percent",0,"%")}`);
      this._gauge("batt", soc / 100,     socColor(soc),       `${Math.round(soc)} %`,
                  `${bv ? bv.toFixed(1) + " V" : ""}`);
      this._gauge("grid", grid / M.grid, gridColor(grid),     `${Math.round(grid)} W`,
                  `${this._fmt("grid_voltage",0,"V")}`);
    }

    if (S.flow) {
      this._set("fnv-solar", `${Math.round(pv)}W`);
      this._set("fnv-load",  `${Math.round(load)}W`);
      this._set("fnv-grid",  `${Math.round(grid)}W`);
      this._set("fnv-batt",  `${Math.round(soc)}%`);
      const chg = amps > 0.5, dis = amps < -0.5;
      this._flow("fn-solar","fl-solar", pv > 15, false);
      this._flow("fn-load", "fl-load",  load > 15, false);
      this._flow("fn-grid", "fl-grid",  grid > 15, false);
      this._flow("fn-batt", "fl-batt",  chg || dis, dis);
    }

    if (S.energy) {
      this._set("e-pv",   this._fmt("energy_pv", 1));
      this._set("e-load", this._fmt("energy_load", 1));
      this._set("e-gin",  this._fmt("energy_grid_in", 1));
      this._set("e-bin",  this._fmt("energy_batt_in", 1));
      this._set("e-bout", this._fmt("energy_batt_out", 1));
    }

    if (S.battery) {
      const rem = this._num("remaining_ah"), des = this._num("design_ah");
      let state = "Resting", color = "#64748b", icon = "⏸", eta = " ", pulse = "";
      if (amps > 0.5) {
        state = "Charging"; color = "#10b981"; icon = "⚡"; pulse = "pulse";
        if (des && rem != null && des > rem) {
          const h = (des - rem) / amps;
          if (h > 0 && h < 99) eta = `≈ ${this._fmtH(h)} to full`;
        }
      } else if (amps < -0.5) {
        state = "Discharging"; color = "#fb923c"; icon = "🔻"; pulse = "pulse";
        if (rem) { const h = rem / (-amps); if (h < 999) eta = `≈ ${this._fmtH(h)} of backup left`; }
      }
      const st = this._el("b-state");
      if (st) { st.innerHTML = `<span class="${pulse}">${icon}</span> ${state}`; st.style.color = color; }
      this._set("b-eta", eta);
      this._set("b-cur", `${amps > 0 ? "+" : ""}${amps.toFixed(1)} A`);
      this._color("b-cur", color);
      this._set("b-pow", `${Math.abs(Math.round(amps * bv))} W`);
      this._set("b-volt", bv ? `${bv.toFixed(2)} V` : "--");
      this._set("b-soc", `${Math.round(soc)} %`);
      this._color("b-soc", socColor(soc));
    }

    if (S.inverter) {
      this._set("i-mode", this._str("device_mode") || "--");
      this._set("i-ac", `${this._fmt("ac_voltage",0,"V")} / ${this._fmt("ac_frequency",1,"Hz")}`);
      this._set("i-bus", this._fmt("bus_voltage",0," V"));
      this._set("i-temp", this._fmt("heatsink_temp",1," °C"));
    }

    if (S.chart && Date.now() - this._lastChart > 300_000) {   // refresh every 5 min
      this._lastChart = Date.now();
      this._drawChart();
    }
  }

  _fmt(key, dec, unit = "") {
    const v = this._num(key);
    return v == null ? "--" : v.toFixed(dec) + unit;
  }
  _fmtH(h) { return h >= 1 ? h.toFixed(1) + " h" : Math.max(1, Math.round(h * 60)) + " min"; }

  _gauge(id, frac, color, val, sub) {
    frac = Math.max(0, Math.min(1, frac || 0));
    const d = this._el(`g-${id}`);
    if (d) {
      const f = d.querySelector(".gfill"), n = d.querySelector(".gneedle");
      if (f) { f.style.strokeDashoffset = ARC * (1 - frac); f.style.stroke = color; }
      if (n) { n.style.transform = `rotate(${-90 + 180 * frac}deg)`; n.style.stroke = color; }
    }
    this._set(`gv-${id}`, val); this._color(`gv-${id}`, color);
    this._set(`gs-${id}`, sub || "");
  }

  _flow(nodeId, lineId, active, reverse) {
    const n = this._el(nodeId), l = this._el(lineId);
    if (n) n.classList.toggle("live", !!active);
    if (l) { l.classList.toggle("on", !!active); l.classList.toggle("rev", !!reverse); }
  }

  /* ── solar generation chart (HA history API) ─────────────────────────── */
  async _drawChart() {
    const box = this._el("chart-box");
    const ent = this._config.entities.pv_power;
    if (!box || !ent) return;
    try {
      const start = new Date(Date.now() - this._config.chart_hours * 3600_000).toISOString();
      const res = await this._hass.callApi("GET",
        `history/period/${start}?filter_entity_id=${ent}&minimal_response&no_attributes`);
      const series = (res && res[0] ? res[0] : [])
        .map(p => ({ t: new Date(p.last_changed || p.lu).getTime(),
                     v: parseFloat(p.state ?? p.s) }))
        .filter(p => !isNaN(p.v));
      if (series.length < 2) {
        box.innerHTML = `<div class="chart-empty">No history yet for ${ent}</div>`;
        return;
      }
      const W = 600, H = 110, P = 4;
      const t0 = series[0].t, t1 = series[series.length - 1].t || t0 + 1;
      const vmax = Math.max(...series.map(p => p.v), 100);
      const X = t => P + (t - t0) / (t1 - t0) * (W - 2 * P);
      const Y = v => H - P - v / vmax * (H - 2 * P);
      let dLine = "", dArea = `M ${X(t0)} ${H - P}`;
      series.forEach((p, i) => {
        const cmd = `${i === 0 ? "M" : "L"} ${X(p.t).toFixed(1)} ${Y(p.v).toFixed(1)}`;
        dLine += cmd + " ";
        dArea += ` L ${X(p.t).toFixed(1)} ${Y(p.v).toFixed(1)}`;
      });
      dArea += ` L ${X(t1).toFixed(1)} ${H - P} Z`;
      const peak = Math.max(...series.map(p => p.v));
      box.innerHTML = `
        <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
          <defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#f59e0b" stop-opacity=".45"/>
            <stop offset="100%" stop-color="#f59e0b" stop-opacity=".03"/>
          </linearGradient></defs>
          <path d="${dArea}" fill="url(#sg)"/>
          <path d="${dLine}" fill="none" stroke="#f59e0b" stroke-width="2"/>
        </svg>
        <div style="font-size:.62rem;color:var(--sb-dim);text-align:right">
          peak ${Math.round(peak)} W · max scale ${Math.round(vmax)} W</div>`;
    } catch (e) {
      box.innerHTML = `<div class="chart-empty">History unavailable (${e.message || e})</div>`;
    }
  }
}

customElements.define("solar-bridge-card", SolarBridgeCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "solar-bridge-card",
  name: "Solar Bridge Card",
  description: "Power flow, analog gauges, energy totals, solar chart, battery & inverter status",
});
})();
