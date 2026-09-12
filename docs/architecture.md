# Architecture

## Roles
- **TWR-A — "base station"**: custom firmware. Radio ↔ USB audio bridge. Dumb by design.
- **TWR-B — "handheld"**: factory firmware, untouched. Human's radio.
  (Later: any commercial VHF HT on the same frequency works too.)
- **Mac — "brain"**: Python agent. Audio in → VAD/segmenting → STT → Claude → TTS → audio out.

Keeping the ESP32 dumb means we iterate on the interesting part (the agent) in
Python with instant restarts, and the firmware only needs to be written once.

## TWR-A firmware (PlatformIO, Arduino-ESP32, reuses LilyGo_TWR_Library)

Serial protocol over the USB-JTAG CDC port (115200 is irrelevant for USB CDC;
it's full-speed USB — plenty for 16 kHz 16-bit mono = 256 kbit/s each way).

Framed, binary, host↔device:
```
  0xAA 0x55 | type:u8 | len:u16 | payload | crc8
  types (dev→host):  0x01 AUDIO_RX (PCM16 @ 16 kHz, 320-sample = 20 ms frames)
                     0x02 STATUS   (sql:u8, rssi:i8, batt_mV:u16, tx:u8, freq:u32)
                     0x03 LOG      (text)
  types (host→dev):  0x10 SET_FREQ (rx:u32 Hz, tx:u32 Hz, sql:u8, ctcss:u8)
                     0x11 AUDIO_TX (PCM16 @ 16 kHz)  — buffered, ignored unless TX_ARM
                     0x12 PTT      (0/1)             — ignored unless TX_ARM
                     0x13 TX_ARM   (magic u32)       — must be sent explicitly each session
                     0x14 SPK      (route: radio|esp, vol)
```
`TX_ARM` is the software interlock: firmware boots with TX disabled and the
host must arm it deliberately. Stage 1/2 builds compile with TX code out entirely
(`-DTWR_TX_DISABLED`).

Receive path on the board:
- `SA868_SQL` (GPIO2) interrupt → status frame immediately on change.
- ADC continuous mode on GPIO1 (ADC1_CH0) at 16 kHz, DMA → 20 ms frames → CDC.
  12-bit ADC → PCM16 (subtract DC bias, shift). ESP32-S3 ADC is noisy;
  acceptable for STT, and we can oversample 4× and decimate if needed.
- OLED shows freq / SQL / RSSI / "TX ARMED" so the state is visible on the bench.

Transmit path (Stage 3 only):
- `routingMicrophoneChannel(TWR_MIC_TO_ESP)` → GPIO17 HIGH.
- Audio to GPIO18 via LEDC PWM at ~78 kHz carrier, duty from PCM (the board's
  low-pass filter on that net is designed for this — see schematic "Low Pass
  Filter Audio path"). Sigma-delta output is the alternative if PWM is too noisy.
- PTT LOW → wait 150 ms (SA868 TX settle) → play → 100 ms tail → PTT HIGH.
- Hard limits in firmware: max 60 s continuous TX, then forced release.

## Mac host agent (`host/`, Python)
- `serial` reader thread → ring buffer; SQL edge marks utterance boundaries
  (much better than VAD alone — the radio tells us exactly when someone unkeys).
- STT: `faster-whisper` locally (small.en is fine for radio-quality audio), or
  a hosted API. Radio audio is 300–3000 Hz; whisper copes.
- LLM: Claude via Anthropic SDK. System prompt frames it as a radio operator:
  short answers, spell out numbers, phonetics for anything ambiguous, ends with
  "over". Optionally keep a per-callsign conversation.
- TTS: macOS `say` → AIFF → resample to 16 kHz PCM is the zero-dependency
  option; swap in a better voice later.
- Stage 2 output goes to the board **speaker** (`SPK route=esp`) so the whole
  loop can be heard without radiating anything.
- Stage 3 output goes to `AUDIO_TX` + `PTT`, gated on `TX_ARM` which the host
  only sends when run with `--tx-armed` *and* an on-screen confirmation.

## Alternatives considered
- **Mac sound card ↔ board audio jack**: T-TWR Plus Rev2.1 has no line-in/out
  jack; the audio matrix is internal. USB streaming it is.
- **WiFi instead of USB** (board sends audio via WebSocket): nice for
  untethered placement; adds latency/complexity. Do after USB works — the
  protocol above is transport-agnostic.
- **All on-device (ESP32 calls cloud STT/LLM/TTS directly)**: possible, but
  debugging on-device audio pipelines is painful. Maybe later as a demo.
- **Use TWR-B as an RX monitor**: with two boards on the Mac we can flash the
  same firmware to both and run one as TX-under-test and one as RX monitor,
  which gives us a closed bench test with a dummy load before any real radiating.

## Staged plan
1. **Receive-only firmware on TWR-A** (TX compiled out). Set freq, stream
   audio + SQL. Host records to WAV and transcribes. Test source: TWR-B on
   factory firmware — *but that transmits*, so first test source is any
   nearby VHF activity (NOAA weather radio at 162.400–162.550 MHz is ideal:
   continuous, legal to receive, in the SA868 VHF range). That alone
   validates the whole RX → STT chain with zero transmission.
2. **Full loop, speaker output**. Add Claude + TTS; reply plays on TWR-A's speaker.
3. **Transmit**. Requires: user's go-ahead, frequency + license decided
   (`docs/regulatory.md`), low power, dummy load or attenuator for bench
   tests, then antenna. TWR-B (factory fw) as the human side.

## Open questions to verify on the bench (Stage 1)
- GPIO1 audio level and DC bias from the SA868 — need a quick ADC dump before
  picking gain/offset. If it's too hot, the schematic's `AUDIO2ESP` net may have
  a divider we need to account for.
- Does `SA868_SQL` on Rev2.1 track carrier or squelch-open-after-CTCSS? Either
  is fine, but it changes the endpoint logic.
- SA868 VHF vs UHF confirm (`AT+MODEL` / OLED I2C address per LilyGO).
