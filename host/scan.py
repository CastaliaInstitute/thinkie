#!/usr/bin/env python3
"""Receive-only RSSI scan. host/scan.py A 162.400 162.550 0.025   (MHz start stop step)
Defaults to the seven NOAA weather channels."""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from twr_link import TwrLink, Status, T_STATUS

NOAA = [162.400, 162.425, 162.450, 162.475, 162.500, 162.525, 162.550]

def resolve(p):
    if p.startswith("/dev/"): return p
    return subprocess.check_output([sys.executable, os.path.join(os.path.dirname(__file__), "..", "tools", "ports.py"), p], text=True).strip()

def main():
    port = resolve(sys.argv[1])
    if len(sys.argv) >= 4:
        a, b, step = map(float, sys.argv[2:5]); freqs = []
        f = a
        while f <= b + 1e-9: freqs.append(round(f, 4)); f += step
    else:
        freqs = NOAA
    link = TwrLink(port); time.sleep(0.3)
    results = []
    for mhz in freqs:
        link.set_freq(int(round(mhz * 1e6)), sq=0)
        time.sleep(0.8)                       # let AT settle + one RSSI poll
        while not link.q.empty(): link.q.get_nowait()
        link.ping(); rssi = None; sql = None; t0 = time.time()
        while time.time() - t0 < 1.5:
            try: t, p = link.q.get(timeout=0.5)
            except Exception: continue
            if t == T_STATUS:
                st = Status.unpack(p)
                if st.rx_hz == int(round(mhz * 1e6)) and st.rssi: rssi, sql = st.rssi, st.sql; break
        results.append((mhz, rssi)); print(f"{mhz:8.3f} MHz  rssi={rssi}")
    best = max((r for r in results if r[1] is not None), key=lambda r: r[1], default=None)
    if best: print(f"strongest: {best[0]:.3f} MHz (rssi {best[1]})")
    link.set_freq(int(round((best[0] if best else freqs[0]) * 1e6)), sq=1)
    link.close()

if __name__ == "__main__":
    main()
