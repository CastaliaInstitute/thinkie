# twr — walkie-talkie → AI experiments

Two LilyGO T-TWR Plus (ESP32-S3 + SA868 VHF) boards on USB. Goal: route VHF
voice to an AI (STT → LLM → TTS) and answer back over the air.

Read `docs/hardware.md` first — pin map, ports, firmware state, caveats.

## Rules (non-negotiable)
- **Never transmit without the user's explicit OK for that specific test.**
  That means: no `radio.transmit()`, no driving PTT (GPIO41) low, no
  `AT+DMOSETGROUP` that enables TX, no flashing firmware that keys PTT on boot.
  Receive-side work first. When TX is approved: low power, brief, agreed frequency.
- Only touch the two T-TWR ports. Resolve them with `tools/ports.py`
  (by MAC). Never open the Luminary ESP32-P4 (`usbmodem133101`), the CH343
  (`wchusbserial5B901846451`), `cu.T7`, or Bluetooth ports.
- Don't flash anything until the user has seen the plan. Factory firmware
  backups live in `firmware/factory/` — restore path must stay viable.
- Never flash the SA868 module itself (OpenRTX etc.) — irreversible.
- Rev2.1 boards need a battery for RF; don't debug "radio not responding"
  without checking that first.

## Layout
- `docs/` — hardware notes, architecture, regulatory notes
- `tools/` — host-side probing scripts (ports.py, bootlog.py); read-only
- `firmware/` — PlatformIO project for the custom T-TWR firmware (+ factory backups)
- `host/` — Mac-side agent (audio ↔ STT ↔ Claude ↔ TTS)
- `.claude/agents/thinkie.md` — the board-aware subagent

## Tooling
- `esptool.py` v4.9 (homebrew), `pio`/`platformio`, `arduino-cli`, `uv` all on PATH.
- USB-JTAG serial is slow (~92 kbit/s); full-flash reads take ~25 min. Read
  only the partitions you need.
