#!/bin/bash
# Run this AFTER plugging in both USB devices to identify their ports
echo "=== USB devices ==="
lsusb

echo ""
echo "=== Device nodes ==="
ls -la /dev/hidraw* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null

echo ""
echo "=== USB → device mapping ==="
for sysdev in /sys/bus/usb/devices/*/; do
    vid=$(cat "$sysdev/idVendor" 2>/dev/null)
    pid=$(cat "$sysdev/idProduct" 2>/dev/null)
    prod=$(cat "$sysdev/product" 2>/dev/null)
    [ -z "$vid" ] && continue
    echo "  $vid:$pid  $prod"
    find "$sysdev" -name "ttyUSB*" -o -name "hidraw*" 2>/dev/null | while read n; do
        echo "    → /dev/$(basename $n)"
    done
done

echo ""
echo "=== Quick inverter test (PI30 QPIGS) ==="
echo "Plug in inverter USB and press Enter to test..."
read
for dev in /dev/hidraw0 /dev/hidraw1 /dev/ttyUSB0 /dev/ttyUSB1; do
    [ -e "$dev" ] || continue
    echo "  Testing $dev ..."
    python3 - "$dev" <<'PYEOF'
import sys, os, time, struct

dev = sys.argv[1]
cmd = b"QPIGS"

def crc(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c << 1) ^ 0x1021 if c & 0x80 else c << 1
        c &= 0xFFFF
    result = []
    for byte in struct.pack(">H", c):
        if byte in (0x28, 0x0D, 0x0A): byte += 1
        result.append(byte)
    return bytes(result)

frame = cmd + crc(cmd) + b"\r"

if "hidraw" in dev:
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    pkt = b"\x00" + frame[:8].ljust(8, b"\x00")
    os.write(fd, pkt)
    time.sleep(0.8)
    try:
        resp = os.read(fd, 256)
        print(f"  Response: {resp[:40]}")
    except BlockingIOError:
        print("  No response (wrong device or protocol)")
    os.close(fd)
else:
    import serial
    s = serial.Serial(dev, 2400, timeout=2)
    s.write(frame)
    resp = s.read(100)
    print(f"  Response: {resp[:40]}")
    s.close()
PYEOF
done

echo ""
echo "=== Quick JKBMS test ==="
echo "RS485 adapter plugged in? Press Enter to test all ttyUSB ports..."
read
REQUEST = bytes([0x4E,0x57,0x00,0x13,0x00,0x00,0x00,0x00,0x06,0x03,0x00,0x00,0x00,0x00,0x00,0x00,0x68,0x00,0x00,0x01,0x29])
for dev in /dev/ttyUSB*; do
    [ -e "$dev" ] || continue
    echo "  Testing $dev for JKBMS..."
    python3 - "$dev" <<'PYEOF'
import sys, serial, time

dev = sys.argv[1]
req = bytes([0x4E,0x57,0x00,0x13,0x00,0x00,0x00,0x00,0x06,0x03,0x00,
             0x00,0x00,0x00,0x00,0x00,0x68,0x00,0x00,0x01,0x29])
try:
    s = serial.Serial(dev, 115200, timeout=2)
    s.reset_input_buffer()
    s.write(req)
    time.sleep(0.4)
    resp = s.read(300)
    s.close()
    if resp[:2] == b"\x4e\x57":
        print(f"  ✓ JKBMS found on {dev}! Response: {len(resp)} bytes")
    else:
        print(f"  ✗ Not JKBMS (got: {resp[:6].hex()})")
except Exception as e:
    print(f"  Error: {e}")
PYEOF
done
