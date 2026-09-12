#!/usr/bin/env python3
"""Reset a T-TWR and capture its serial boot log (read-only, no flashing).

Usage: tools/bootlog.py A|B|/dev/cu.usbmodemX [seconds]
Requires pyserial: python3 -m venv .venv && .venv/bin/pip install pyserial
"""
import subprocess, sys, time, os
import serial

arg = sys.argv[1]
port = arg if arg.startswith("/dev/") else subprocess.check_output(
    [os.path.join(os.path.dirname(__file__), "ports.py"), arg], text=True).strip()
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 6

s = serial.Serial(port, 115200, timeout=0.2)
# USB-Serial-JTAG reset: pulse RTS (EN) with DTR (IO0) deasserted -> normal boot
s.dtr = False; s.rts = True; time.sleep(0.1); s.rts = False
s.reset_input_buffer()
t0 = time.time(); buf = b""
while time.time() - t0 < secs:
    buf += s.read(4096)
s.close()
sys.stdout.write(buf.decode("utf-8", "replace"))
