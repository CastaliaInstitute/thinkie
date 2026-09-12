# Prior work: Channel 6 STT/TTS bridge (June 2026)

`CastaliaInstitute/twr` on GitHub is a fork of Xinyuan-LilyGO/T-TWR with one
custom commit, `12cb298` "Add channel 6 STT TTS bridge" (2026-06-04). Copies
of the two files are in `reference/channel6_bridge/`.

## What it does
- **Firmware** `examples/Channel6_STT_TTS_Bridge.ino`: T-TWR Plus **UHF** tuned
  to FRS/GMRS ch6 (462.6875 MHz), 12.5 kHz, low power, squelch 4. Streams
  8 kHz **8-bit** radio audio (`analogRead(GPIO1)` in a busy-wait loop) as
  text-framed `AUD <n>\n` + bytes at 921600 baud. Accepts `TX <n>\n` + bytes,
  keys PTT, plays them via LEDC PWM (62.5 kHz, 8-bit) on GPIO18, unkeys.
- **Host** `scripts/channel6_stt_tts_host.py`: pyserial; sends WAV to **Gemini**
  for transcription, Gemini text for the reply, Gemini TTS (24 kHz s16) →
  downsampled to 8 kHz u8 → board. Loads keys from `../castalia.institute/.env*`.

## Why it matters
It validates the split we're proposing (dumb board bridge, all intelligence
on the Mac). Same author, same instinct.

## What we're changing and why
| Then | Now | Reason |
|------|-----|--------|
| UHF, FRS ch6 | VHF boards on the bench | different hardware; FRS also requires Part 95 certified radios (SA868 isn't) |
| 8 kHz / 8-bit, busy-wait `analogRead` | 16 kHz / 16-bit, ADC continuous DMA | jitter-free sampling; STT accuracy |
| Text-framed protocol | Binary framed w/ CRC + status frames | robust resync; carries SQL/RSSI |
| No squelch signal | GPIO2 `SA868_SQL` marks utterance boundaries | far better segmenting than VAD alone |
| TX enabled by default | `TX_ARM` interlock, TX compiled out for Stage 1 | receive-first, regulatory hygiene |
| Gemini STT/TTS/LLM | Pluggable; Claude for the LLM, local whisper or hosted for STT | keep Gemini path as an option |

Unknown: whether the June code was ever run against real hardware. Treat its
ADC/PWM settings as hypotheses, not measurements.
