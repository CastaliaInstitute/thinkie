#!/usr/bin/env python3
"""The walkie-talkie -> AI loop.

Listens on one board: while squelch is open, buffers the received audio; when it closes
(the other station unkeyed), sends the clip to Gemini for transcription, gets a reply,
synthesises it, and plays it back — through the board speaker (default, no RF) or, with
--tx and an explicit arm, over the air.

  host/agent.py A                          # listen on A, answer on A's speaker
  host/agent.py A --freq 146.520           # tune first (MHz)
  host/agent.py A --answer-on B            # answer through B's speaker instead
  host/agent.py A --tx --answer-on A       # answer OVER THE AIR from A (thinkie-tx firmware; prompts to arm)
  host/agent.py A --once                   # handle one exchange, then exit
"""
import argparse, os, sys, time, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from thinkie_link import ThinkieLink, Status, T_AUDIO_RX, T_STATUS, AUDIO_RATE_HZ, FRAME_BYTES
import ai_gemini

MIN_CLIP_S = 0.6        # ignore squelch blips shorter than this
MAX_CLIP_S = 30.0       # cut a clip at this length even if squelch stays open
TAIL_S = 0.3            # keep this much after squelch closes (radio tail)
HANG_S = 0.5            # squelch must stay closed this long to end an utterance

def save_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(AUDIO_RATE_HZ); w.writeframes(pcm)

def listen_for_utterance(link: ThinkieLink, quiet=False, max_clip=MAX_CLIP_S) -> bytes:
    """Block until a squelch-delimited clip is captured; return s16le PCM."""
    pre = []                # rolling 0.3 s pre-roll so we don't lose the first syllable
    clip = bytearray(); active = False; closed_at = None; t_open = None
    while True:
        try: t, p = link.q.get(timeout=1.0)
        except Exception: continue
        if t == T_STATUS:
            st = Status.unpack(p)
            if st.sql and not active:
                active = True; t_open = time.time(); closed_at = None
                clip = bytearray(b"".join(pre))
                if not quiet: print(f"  squelch open (rssi {st.rssi})", flush=True)
            elif st.sql and active:
                closed_at = None                       # squelch blip: still talking
            elif not st.sql and active and closed_at is None:
                closed_at = time.time()
        elif t == T_AUDIO_RX:
            pcm = p[2:]
            pre.append(pcm); pre = pre[-15:]
            if active:
                clip += pcm
                if closed_at and time.time() - closed_at > HANG_S:
                    dur = len(clip) / (2 * AUDIO_RATE_HZ)
                    if dur >= MIN_CLIP_S: return bytes(clip)
                    if not quiet: print(f"  (blip {dur:.2f}s ignored)", flush=True)
                    active = False; clip = bytearray()
                elif not closed_at and time.time() - t_open > max_clip:
                    return bytes(clip)
        # squelch reopened during the hang -> keep going (closed_at reset by STATUS above)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("listen", help="A or B: board that receives")
    ap.add_argument("--answer-on", default=None, help="board that plays the answer (default: same)")
    ap.add_argument("--freq", type=float, help="MHz to tune the listening board (and answer board) to")
    ap.add_argument("--sq", type=int, default=3)
    ap.add_argument("--tx", action="store_true", help="answer over the air (needs thinkie-tx firmware + arm)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--save", default=None, help="dir to save clips/replies as WAV")
    ap.add_argument("--vol", type=int, default=6)
    ap.add_argument("--callsign", default=None, help="station ID appended to every spoken reply")
    ap.add_argument("--max-clip", type=float, default=MAX_CLIP_S, help="cut a clip at this many seconds")
    a = ap.parse_args()

    ai_gemini.load_env(); key, src = ai_gemini.resolve_api_key(); print(f"Gemini key from {src}")
    rx = ThinkieLink(a.listen, log=lambda s: print(f"[{a.listen}] {s}"))
    tx = rx if (a.answer_on or a.listen) == a.listen else ThinkieLink(a.answer_on, log=lambda s: print(f"[{a.answer_on}] {s}"))
    time.sleep(0.4)
    if a.freq:
        hz = int(round(a.freq * 1e6)); rx.set_freq(hz, sq=a.sq)
        if tx is not rx: tx.set_freq(hz, sq=a.sq)
    else:
        rx.set_freq(rx.wait_status().rx_hz, sq=a.sq)
    st = rx.wait_status(); print("listen :", st)
    if tx is not rx: print("answer :", tx.wait_status())

    if a.tx:
        from say_over_air import tx_allowed
        s = tx.wait_status()
        if not s or not s.tx_build:
            sys.exit("answer board is not running a thinkie-tx build; refusing --tx")
        if not tx_allowed(s.tx_hz): sys.exit(f"refusing --tx on {s.tx_hz/1e6:.4f} MHz (not 2 m / MURS)")
        print(f"\n*** --tx: replies will be TRANSMITTED from {tx.status.board} on {s.tx_hz/1e6:.4f} MHz, low power.")
        if input("Type ARM to arm the transmitter for this session: ").strip() != "ARM":
            sys.exit("not armed")
        tx.tx_arm(True); time.sleep(0.2); print("armed  :", tx.wait_status())

    history: list[dict] = []
    n = 0
    print(f"\nListening on {st.board} at {st.rx_hz/1e6:.4f} MHz. Key up and talk; unkey to send.\n")
    while True:
        clip = listen_for_utterance(rx, max_clip=a.max_clip)
        n += 1; dur = len(clip) / (2 * AUDIO_RATE_HZ)
        print(f"[{n}] clip {dur:.1f}s -> transcribing", flush=True)
        if a.save: save_wav(os.path.join(a.save, f"rx_{n:03d}.wav"), clip)
        t0 = time.time()
        try:
            text = ai_gemini.transcribe(clip, key)
            print(f"[{n}] heard ({time.time()-t0:.1f}s): {text!r}")
            system = ai_gemini.RADIO_SYSTEM_PROMPT
            if a.callsign:
                system += f" The station callsign is {a.callsign}; end every reply with '{a.callsign}, over' instead of just 'over'."
            answer = ai_gemini.reply(text, key, history, system=system)
            print(f"[{n}] reply ({time.time()-t0:.1f}s): {answer}")
            pcm = ai_gemini.synthesize(answer, key)
            print(f"[{n}] tts   ({time.time()-t0:.1f}s): {len(pcm)/(2*AUDIO_RATE_HZ):.1f}s audio")
        except Exception as e:
            print(f"[{n}] AI error: {e}"); continue
        if a.save: save_wav(os.path.join(a.save, f"tx_{n:03d}.wav"), pcm)

        # drain anything received during the AI round-trip so it isn't treated as a new question
        while not rx.q.empty():
            try: rx.q.get_nowait()
            except Exception: break

        if a.tx:
            from say_over_air import transmit
            secs = transmit(tx, pcm, mic_gain=30)
        else:
            tx.speaker(True, a.vol)
            secs = tx.play_pcm(pcm)
            time.sleep(0.7)
            tx.speaker(False, 4)
        print(f"[{n}] played {secs:.1f}s {'OVER THE AIR' if a.tx else 'on speaker'}\n")
        if a.once: break

    if a.tx: tx.tx_arm(False)
    rx.close()
    if tx is not rx: tx.close()

if __name__ == "__main__":
    main()
