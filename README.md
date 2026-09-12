# Walkie‑Thinkie

**Key up a VHF handheld, ask a question, and an AI answers you back over the air.**

Built on two LilyGO T‑TWR Plus boards (ESP32‑S3 + SA868 VHF transceiver). One board is
the base station: it receives, streams the audio to a Mac over USB, and transmits the
reply. The Mac does the thinking — speech‑to‑text → LLM → text‑to‑speech. Any FM
handheld on the same frequency talks to it.

Product page: https://castaliainstitute.github.io/thinkie/

## Status (2026‑09‑12)
- [x] Receive: NOAA weather off the air → 16 kHz over USB → transcribed
- [x] Transmit: board plays synthesised speech into the SA868 mic path, keyed by a software interlock
- [x] Full loop, over the air, verified at both ends: question transmitted from one board, received and
      transcribed *word‑for‑word* on the other, answered by Gemini, reply transmitted back and transcribed
      again from what the far board heard
- [x] Survives USB dropping mid‑transmission (autonomous clip TX on the board, 60 s receive ring in PSRAM with catch‑up)
- [ ] Battery‑only / WiFi operation (currently tethered to the Mac over USB)
- [ ] UHF board (TWR‑C, flashed) for FRS/GMRS handhelds — needs a battery

## Layout
- `docs/` — hardware notes (pin map, bench findings), architecture, regulatory notes, prior work; `docs/index.html` is the product page
- `firmware/` — PlatformIO project (`thinkie-rx-*` receive‑only, `thinkie-tx-*` transmit‑capable) + factory backups
- `host/` — Mac side: `thinkie_link.py` protocol, `agent.py` the loop, `say_over_air.py`, `e2e.py`, `monitor.py`, `scan.py`, `play.py`
- `tools/` — `ports.py` (find boards by MAC), `bootlog.py`
- `.claude/agents/thinkie.md` — board‑aware Claude Code subagent with the no‑TX‑without‑approval rule

## Quick start
```sh
python3 -m venv .venv && .venv/bin/pip install pyserial
python3 tools/ports.py                                   # which board is on which port
cd firmware && pio run -e thinkie-tx-a -t upload --upload-port $(../tools/ports.py A) && cd ..
.venv/bin/python host/monitor.py A --freq 162.550 --sq 0 # hear NOAA, watch RSSI/squelch
.venv/bin/python host/agent.py A --freq 146.550 --callsign YOURCALL --tx   # the loop, over the air
```
Gemini credentials come from `GEMINI_API_KEY` / `GCP_API_KEY` in `host/.env` (see `host/ai_gemini.py`).

## Rules of the air
The SA868 is not Part 95 certified, so the clean way to transmit is under an amateur
licence on 2 m (the agent appends your callsign). Receive‑only needs nothing. Details and
the bench‑hygiene rules in `docs/regulatory.md`. The firmware boots with transmit disarmed,
requires an explicit arm per session (10 min TTL), force‑unkeys after 60 s, and unkeys if
the host disappears.

## Hardware
LilyGO T‑TWR Plus Rev2.1 (VHF): ESP32‑S3R8, 16 MB flash, SA868 with NiceRF AT firmware,
AXP2101 PMU, SH1106 OLED. Needs a charged 18650/21700 — the RF section is battery‑fed
and a flat cell shows up as "SA8X8 failed" / silence. Full pin map in `docs/hardware.md`.
