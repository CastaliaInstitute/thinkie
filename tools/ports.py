#!/usr/bin/env python3
"""Resolve the two T-TWR boards' serial ports by ESP32-S3 MAC (macOS).

usbmodemNNN names change with the USB port; the MAC baked into the USB serial
number does not. Prints one line per board: ALIAS PORT MAC. Never touches
other serial devices.

Usage: tools/ports.py [A|B]   (with an alias, prints just that port path)
"""
import re, subprocess, sys

BOARDS = {
    "A": "3c:84:27:cc:18:1c",
    "B": "48:ca:43:35:b5:b8",
    "C": "dc:da:0c:16:d3:34",   # UHF T-TWR Plus (was running Sonatino UAC firmware)
}

def usb_serial_ports():
    """Return {mac: port} for Espressif USB-JTAG/serial units (and anything else whose USB
    serial number is a MAC). ioreg lists the callout device nested under the interface, so
    walk the tree in order and attribute each /dev/cu.* to the last MAC-shaped serial seen."""
    out = subprocess.run(["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w0"],
                         capture_output=True, text=True).stdout
    result, cur = {}, None
    for line in out.splitlines():
        m = re.search(r'"USB Serial Number" = "([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})"', line)
        if m: cur = m.group(1).lower(); continue
        p = re.search(r'"IOCalloutDevice" = "(/dev/cu\.[^"]+)"', line)
        if p and cur: result[cur] = p.group(1)
    return result

def main():
    found = usb_serial_ports()
    want = sys.argv[1].upper() if len(sys.argv) > 1 else None
    rc = 0
    for alias, mac in BOARDS.items():
        port = found.get(mac)
        if want:
            if alias == want:
                if port: print(port)
                else: print(f"TWR-{alias} ({mac}) not attached", file=sys.stderr); rc = 1
            continue
        print(f"TWR-{alias}  {port or '(not attached)'}  {mac}")
        if not port: rc = 1
    sys.exit(rc)

if __name__ == "__main__":
    main()
