#!/usr/bin/env python3
"""TRANSMIT a spoken message from a board (thinkie-tx firmware). Arms, keys, plays, unkeys, disarms.

  host/say_over_air.py B --callsign K0INQ "What is the capital of France?"
  host/say_over_air.py B --callsign K0INQ --wav question.wav
  host/say_over_air.py B --callsign K0INQ --tone 1000 --secs 1      # 1 kHz test tone

Every transmission is prefixed/suffixed with the callsign (Part 97 station ID).
Refuses if the board is not a thinkie-tx build, or if --freq is outside 144-148 MHz.
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from thinkie_link import ThinkieLink, AUDIO_RATE_HZ

MURS_HZ = {151_820_000, 151_880_000, 151_940_000, 154_570_000, 154_600_000}
def tx_allowed(hz: int) -> bool:
    """2 m amateur band (licensed) or one of the five MURS channels."""
    return 144_000_000 <= hz <= 148_000_000 or hz in MURS_HZ
from play import load_wav_16k, tone
import ai_gemini

def transmit(link: ThinkieLink, pcm: bytes, mic_gain: int = 25, lead_s: float = 0.3) -> float:
    """Upload the clip to the board, then let the board key/play/unkey on its own.
    A USB hiccup mid-transmission no longer truncates the audio. Returns seconds elapsed."""
    link.gain(100, mic_gain)
    secs = len(pcm) / (2 * AUDIO_RATE_HZ)
    link.clip_load(pcm)
    t0 = time.time()
    ok = link.clip_run(transmit=True, expect_s=secs + lead_s + 0.5)
    if not ok and link.log: link.log("(link) clip transmit: no completion seen")
    return time.time() - t0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port"); ap.add_argument("text", nargs="?")
    ap.add_argument("--callsign", default=None, help="station ID (required on amateur freqs, not on MURS)")
    ap.add_argument("--freq", type=float, help="MHz; 2 m band or a MURS channel")
    ap.add_argument("--wav"); ap.add_argument("--tone", type=float); ap.add_argument("--secs", type=float, default=1.0)
    ap.add_argument("--mic-gain", type=int, default=25, help="percent of full scale into the SA868 mic path")
    ap.add_argument("--no-id", action="store_true", help="omit spoken callsign (tone tests only)")
    a = ap.parse_args()

    link = ThinkieLink(a.port, log=lambda s: print(f"[{a.port}] {s}")); time.sleep(0.4)
    st = link.wait_status()
    if not st or not st.tx_build: sys.exit("board is not running a thinkie-tx build")
    if a.freq:
        if not tx_allowed(int(round(a.freq * 1e6))): sys.exit("refusing: not a 2 m amateur or MURS frequency")
        link.set_freq(int(round(a.freq * 1e6)), sq=3); st = link.wait_status()
    if not tx_allowed(st.tx_hz): sys.exit(f"refusing: board TX freq {st.tx_hz/1e6:.4f} MHz is not 2 m or MURS")
    if 144_000_000 <= st.tx_hz <= 148_000_000 and not a.callsign and not a.no_id:
        sys.exit("amateur frequency: --callsign required")
    if st.batt_mv < 3500: sys.exit(f"refusing: battery {st.batt_mv} mV")

    if a.tone:
        pcm = tone(a.tone, a.secs, amp=0.5)
        if not a.no_id: sys.exit("tone tests need --no-id (or say something instead)")
    else:
        ai_gemini.load_env(); key, _ = ai_gemini.resolve_api_key()
        body = a.text or ""
        if a.wav: pcm_body = load_wav_16k(a.wav)
        text = f"{a.callsign}. {body} {a.callsign}, over." if (a.callsign and not a.no_id) else f"{body} Over."
        print("TTS:", text)
        pcm = ai_gemini.synthesize(text, key) if not a.wav else pcm_body
    print(f"audio {len(pcm)/(2*AUDIO_RATE_HZ):.1f}s; TX from {st.board} on {st.tx_hz/1e6:.4f} MHz low power")

    link.tx_arm(True); time.sleep(0.2)
    if not link.wait_status().tx_enabled: sys.exit("arm failed")
    keyed = transmit(link, pcm, a.mic_gain)
    time.sleep(0.2); link.tx_arm(False)
    print(f"keyed {keyed:.1f}s; disarmed:", link.wait_status())
    link.close()

if __name__ == "__main__":
    main()
