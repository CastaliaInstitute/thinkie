#!/usr/bin/env python3
"""Fully automated over-the-air round trip, verified at both ends.

  B transmits a spoken question  ->  A receives, Gemini STT -> reply -> TTS  ->  A transmits the
  answer  ->  B receives it  ->  Gemini transcribes what B heard, so we know what a person
  holding B would have heard.

  host/e2e.py --freq 151.820 "What is the boiling point of water in Fahrenheit?"
  host/e2e.py --freq 146.550 --callsign K0INQ "..."
"""
import argparse, os, sys, threading, time, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from thinkie_link import ThinkieLink, Status, T_AUDIO_RX, T_STATUS, AUDIO_RATE_HZ
from say_over_air import transmit, tx_allowed
from agent import listen_for_utterance, save_wav
import ai_gemini

class Capture:
    """Record everything a board receives until stopped."""
    def __init__(self, link): self.link, self.buf, self._stop = link, bytearray(), threading.Event()
    def __enter__(self):
        while not self.link.q.empty(): self.link.q.get_nowait()
        self.t = threading.Thread(target=self._run); self.t.start(); return self
    def _run(self):
        while not self._stop.is_set():
            try: t, p = self.link.q.get(timeout=0.2)
            except Exception: continue
            if t == T_AUDIO_RX: self.buf.extend(p[2:])
    def __exit__(self, *a): self._stop.set(); self.t.join()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question"); ap.add_argument("--freq", type=float, required=True)
    ap.add_argument("--callsign", default=None); ap.add_argument("--save", default=None)
    ap.add_argument("--mic-gain", type=int, default=30)
    ap.add_argument("--ai", default="A", help="board that plays the AI station (receives question, transmits answer)")
    a = ap.parse_args()
    hz = int(round(a.freq * 1e6))
    if not tx_allowed(hz): sys.exit("not a 2 m / MURS frequency")
    if 144e6 <= hz <= 148e6 and not a.callsign: sys.exit("amateur frequency needs --callsign")
    ai_gemini.load_env(); key, _ = ai_gemini.resolve_api_key()
    ident = lambda s: f"{a.callsign}. {s} {a.callsign}, over." if a.callsign else f"{s} Over."

    ai, hu = a.ai.upper(), ("B" if a.ai.upper() == "A" else "A")
    A = ThinkieLink(ai, log=lambda s: print(f"  [{ai}]", s) if ("PTT" in s or "link" in s) else None)   # AI station
    B = ThinkieLink(hu, log=lambda s: print(f"  [{hu}]", s) if ("PTT" in s or "link" in s) else None)   # human side
    time.sleep(0.5)
    for l in (A, B):
        l.set_freq(hz, sq=3)
        st = l.wait_status()
        if not st.tx_build: sys.exit(f"{st.board} is not a thinkie-tx build")
    print(f"A: {A.status}\nB: {B.status}\n")

    # 1. B asks
    q_text = ident(a.question); print("B says:", q_text)
    q_pcm = ai_gemini.synthesize(q_text, key)
    while not A.q.empty(): A.q.get_nowait()
    B.tx_arm(True); time.sleep(0.2)
    lst = threading.Thread(target=lambda: setattr(main, "clip", listen_for_utterance(A, quiet=True))); lst.start()
    transmit(B, q_pcm, a.mic_gain); B.tx_arm(False)
    lst.join(15); clip = getattr(main, "clip", b"")
    print(f"A heard {len(clip)/(2*AUDIO_RATE_HZ):.1f}s (rssi {A.status.rssi})")
    if a.save: save_wav(os.path.join(a.save, "e2e_A_rx.wav"), clip)

    # 2. AI
    t0 = time.time()
    heard = ai_gemini.transcribe(clip, key); print(f"STT   : {heard!r}")
    system = ai_gemini.RADIO_SYSTEM_PROMPT + (f" End every reply with '{a.callsign}, over'." if a.callsign else "")
    answer = ai_gemini.reply(heard, key, system=system); print(f"reply : {answer}")
    ans_pcm = ai_gemini.synthesize(answer, key); print(f"AI round trip {time.time()-t0:.1f}s, {len(ans_pcm)/(2*AUDIO_RATE_HZ):.1f}s of speech")
    if a.save: save_wav(os.path.join(a.save, "e2e_A_tx.wav"), ans_pcm)

    # 3. A answers over the air, B records
    A.tx_arm(True); time.sleep(0.2)
    with Capture(B) as cap:
        keyed = transmit(A, ans_pcm, a.mic_gain)
        time.sleep(0.5)
    A.tx_arm(False)
    print(f"A keyed {keyed:.1f}s; B captured {len(cap.buf)/(2*AUDIO_RATE_HZ):.1f}s (rssi {B.status.rssi})")
    if a.save: save_wav(os.path.join(a.save, "e2e_B_rx.wav"), bytes(cap.buf))

    # 4. what did B hear?
    back = ai_gemini.transcribe(bytes(cap.buf), key)
    print(f"\nB heard: {back!r}")
    A.close(); B.close()

if __name__ == "__main__":
    main()
