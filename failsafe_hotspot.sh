#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  failsafe_hotspot.sh — when the Pi loses internet, raise a WiFi hotspot  ║
# ║  on wlan0 so you can still reach the dashboard at http://10.42.0.1:8080 ║
# ║  When internet returns, the hotspot is taken down automatically.        ║
# ║  Run periodically by failsafe-hotspot.timer (every 2 min).              ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -uo pipefail

SSID="${HOTSPOT_SSID:-SolarBridge}"
PASS="${HOTSPOT_PASS:-solar12345}"        # >= 8 chars (WPA2 requirement)
CON="solar-hotspot"
STATE="/run/solar-hotspot.fails"
NEED_FAILS=2                              # consecutive failures (~4 min) before raising AP

have_wlan() { nmcli -t -f DEVICE device 2>/dev/null | grep -qx wlan0; }

is_online() {
    local s
    s=$(nmcli -t -f CONNECTIVITY general status 2>/dev/null)
    [[ "$s" == "full" ]] && return 0
    ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && return 0
    return 1
}

# Is wlan0 currently our internet uplink? (don't tear that down for an AP)
wlan_is_uplink() {
    ip route show default 2>/dev/null | grep -q "dev wlan0"
}

hotspot_active() {
    nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$CON"
}

start_hotspot() {
    hotspot_active && return 0
    have_wlan || { logger -t solar-hotspot "no wlan0 — cannot start hotspot"; return 1; }
    wlan_is_uplink && { logger -t solar-hotspot "wlan0 is the uplink — not starting AP"; return 1; }
    nmcli radio wifi on >/dev/null 2>&1
    if nmcli device wifi hotspot ifname wlan0 con-name "$CON" ssid "$SSID" password "$PASS" >/dev/null 2>&1; then
        nmcli connection modify "$CON" connection.autoconnect no >/dev/null 2>&1 || true
        logger -t solar-hotspot "Internet DOWN → hotspot '$SSID' up. Connect & open http://10.42.0.1:8080"
    else
        logger -t solar-hotspot "failed to start hotspot"
    fi
}

stop_hotspot() {
    hotspot_active || return 0
    nmcli connection down "$CON" >/dev/null 2>&1
    nmcli connection delete "$CON" >/dev/null 2>&1 || true
    logger -t solar-hotspot "Internet restored → hotspot stopped"
}

fails=$(cat "$STATE" 2>/dev/null || echo 0)
if is_online; then
    echo 0 > "$STATE"
    stop_hotspot
else
    fails=$((fails + 1)); echo "$fails" > "$STATE"
    if [[ "$fails" -ge "$NEED_FAILS" ]]; then
        start_hotspot
    fi
fi
