#!/usr/bin/env python3
"""Play audio through a board's speaker (no RF).

  host/play.py A --say "Testing one two three"     # Gemini TTS -> speaker
  host/play.py A --wav file.wav                     # any WAV (resampled to 16 kHz mono)
  host/play.py A --tone 440 --secs 2                # sine test tone
  add --radio-audio to route the SA868 audio back to the speaker afterwards (default: yes)
"""
import argparse, math, struct, subprocess, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from twr_link import TwrLink, AUDIO_RATE_HZ
import ai_gemini

def load_wav_16k(path: str) -> bytes:
    """Any audio file -> mono s16le @16k via ffmpeg."""
    return subprocess.check_output(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(AUDIO_RATE_HZ),
                                    "-f", "s16le", "-"])

def tone(freq: float, secs: float, amp: float = 0.5) -> bytes:
    n = int(secs * AUDIO_RATE_HZ)
    return b"".join(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * i / AUDIO_RATE_HZ))) for i in range(n))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--say"); g.add_argument("--wav"); g.add_argument("--tone", type=float)
    ap.add_argument("--secs", type=float, default=2.0)
    ap.add_argument("--gain", type=int, default=100, help="speaker gain percent")
    ap.add_argument("--vol", type=int, default=6, help="radio volume 1-8 (feeds the same amp)")
    a = ap.parse_args()

    if a.say:
        ai_gemini.load_env(); key, _ = ai_gemini.resolve_api_key()
        t0 = time.time(); pcm = ai_gemini.synthesize(a.say, key); print(f"TTS {len(pcm)/32000:.1f}s in {time.time()-t0:.1f}s")
    elif a.wav:
        pcm = load_wav_16k(a.wav)
    else:
        pcm = tone(a.tone, a.secs)

    link = TwrLink(a.port, log=lambda s: print("[board]", s))
    time.sleep(0.3)
    link.speaker(True, a.vol); link.gain(a.gain, 25)
    secs = link.play_pcm(pcm)
    time.sleep(2.2)                                   # let the on-board queue drain
    link.speaker(False, 4)
    print(f"played {secs:.1f}s on {link.status.board if link.status else '?'}")
    link.close()

if __name__ == "__main__":
    main()
