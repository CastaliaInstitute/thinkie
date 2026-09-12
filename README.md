# twr — walkie-talkie → AI

Can you key up a VHF handheld, ask a question, and have an AI answer you back
over the air? This repo is the experiment, built on two LilyGO **T-TWR Plus**
boards (ESP32-S3 + SA868 VHF transceiver module).

- `docs/hardware.md` — what's actually on the bench: ports, chips, firmware, pin map
- `docs/architecture.md` — the loop design and the staged plan (this file's summary is below)
- `docs/regulatory.md` — what we can and can't transmit
- `tools/` — host probing scripts (read-only)
- `firmware/` — custom T-TWR firmware (PlatformIO) + factory backups
- `host/` — the Mac-side agent
- `.claude/agents/twr.md` — Claude Code subagent that knows the boards and the rules

## Status
- [x] Both boards enumerated, identified, factory firmware backed up
- [ ] Stage 1: receive-only — stream radio audio to the Mac, transcribe it
- [ ] Stage 2: STT → Claude → TTS on the Mac, playback through the *board speaker* (no RF)
- [ ] Stage 3: transmit the reply over air (needs explicit go-ahead + license/dummy-load plan)

## The loop, in one picture

```
  Human on handheld (TWR-B, factory fw)          AI base station (TWR-A, custom fw)   Mac
  ┌──────────────┐   VHF, e.g. 146.xxx   ┌──────────────────────────────┐   USB CDC   ┌──────────────────────┐
  │ PTT → speak  │ ───────────────────▶  │ SA868 RX → audio out (GPIO1) │ ─────────▶  │ VAD → STT (whisper)  │
  │              │                       │ SQL (GPIO2) = "carrier on"   │  PCM+flags  │  → Claude → TTS      │
  │ hears reply  │ ◀───────────────────  │ GPIO18 → SA868 mic, PTT low  │ ◀─────────  │  → PCM               │
  └──────────────┘                       └──────────────────────────────┘             └──────────────────────┘
```

TWR-A is a *USB audio modem for the radio*: custom firmware that configures
the SA868 over AT, watches the squelch line, streams received audio to the Mac,
and (once approved) plays Mac-supplied audio into the radio's mic input while
keying PTT. All intelligence lives on the Mac. TWR-B stays on factory firmware
and is just the handheld a human talks into.

See `docs/architecture.md` for why this split, the alternatives, and the
stage-by-stage plan.
