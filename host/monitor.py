#!/usr/bin/env python3
"""Stage 1 bench tool: show status from the board, optionally record RX audio to WAV.

  host/monitor.py A                       # status + live level meter
  host/monitor.py A --freq 162.550        # retune (MHz)
  host/monitor.py A --wav out.wav --secs 30
  host/monitor.py A --wav out.wav --sql   # only record while squelch is open
"""
import argparse, os, subprocess, sys, time, wave, array, math
sys.path.insert(0, os.path.dirname(__file__))
from twr_link import TwrLink, Status, T_AUDIO_RX, T_STATUS, T_LOG, AUDIO_RATE_HZ

def resolve(p):
    if p.startswith("/dev/"): return p
    tool = os.path.join(os.path.dirname(__file__), "..", "tools", "ports.py")
    return subprocess.check_output([sys.executable, tool, p], text=True).strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port"); ap.add_argument("--freq", type=float, help="RX MHz")
    ap.add_argument("--sq", type=int, default=1); ap.add_argument("--wav")
    ap.add_argument("--secs", type=float, default=0); ap.add_argument("--sql", action="store_true")
    ap.add_argument("--vol", type=int); ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    link = TwrLink(resolve(a.port)); time.sleep(0.3)
    if a.freq: link.set_freq(int(round(a.freq * 1e6)), sq=a.sq)
    if a.vol: link.speaker(False, a.vol)
    link.ping()

    wf = None
    if a.wav:
        wf = wave.open(a.wav, "wb"); wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(AUDIO_RATE_HZ)
    st = None; t0 = time.time(); frames = 0; last_seq = None; gaps = 0; written = 0
    try:
        while not a.secs or time.time() - t0 < a.secs:
            try: t, p = link.q.get(timeout=0.5)
            except Exception: continue
            if t == T_STATUS:
                st = Status.unpack(p)
                if not a.quiet:
                    print(f"[status] {st.band_name} rev{st.hw_rev} rx={st.rx_hz/1e6:.4f} sq={st.sq} "
                          f"SQL={'OPEN' if st.sql else '----'} rssi={st.rssi} batt={st.batt_mv}mV "
                          f"dc={st.adc_dc} drop={st.dropped} badcrc={link.bad_crc}")
            elif t == T_LOG:
                print(f"[board] {p.decode('utf-8','replace')}")
            elif t == T_AUDIO_RX:
                seq = int.from_bytes(p[:2], "little")
                if last_seq is not None and (seq - last_seq) & 0xFFFF != 1: gaps += 1
                last_seq = seq
                pcm = array.array("h", p[2:]); frames += 1
                if wf and (not a.sql or (st and st.sql)):
                    wf.writeframes(pcm.tobytes()); written += 1
                if frames % 25 == 0 and not a.quiet:      # every 0.5 s
                    rms = math.sqrt(sum(x * x for x in pcm) / len(pcm))
                    peak = max(abs(x) for x in pcm)
                    bar = "#" * min(40, int(40 * rms / 8000))
                    print(f"[audio] rms={rms:6.0f} peak={peak:5d} gaps={gaps} rec={written*0.02:.1f}s |{bar}")
    except KeyboardInterrupt:
        pass
    finally:
        if wf: wf.close(); print(f"wrote {a.wav}: {written*0.02:.1f}s")
        link.close()

if __name__ == "__main__":
    main()
