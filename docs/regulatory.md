# Regulatory notes (US-centric; not legal advice)

The SA868 VHF module covers ~134–174 MHz at 0.5 W / 1 W. It is **not**
Part 90/95 type-accepted, so it may not be used on MURS, marine, or business
frequencies regardless of licensing. That leaves:

- **Amateur 2 m band (144–148 MHz)** — requires at least a Technician license
  (Part 97). Home-built/non-certified equipment is fine under Part 97.
  Constraints that matter for an AI responder:
  - A licensed **control operator** must be responsible for every transmission.
    An automated station replying to queries is allowed as a remotely/locally
    controlled station with an operator present, but we should not leave it
    running unattended.
  - **Station ID** with the callsign every 10 minutes and at the end of a
    contact (§97.119). The TTS should append the callsign.
  - No broadcasting, no encrypted/obscured content, no commercial traffic.
  - Pick a simplex frequency from the local band plan (e.g. 146.4xx–146.5xx
    simplex range in most US regions); stay off repeater inputs and 146.52 calling.
- **Receiving** anything is unrestricted. NOAA weather (162.400–162.550 MHz)
  is a great always-on RX test signal.

## Bench-test hygiene before any antenna
- Low power mode only.
- Dummy load (50 Ω, ≥1 W) on the SMA — or a 30–40 dB attenuator between the
  two boards' SMAs. A few centimetres of PCB trace will still leak enough to
  couple two boards on the same desk.
- Keep TX bursts short; firmware hard-limits continuous TX to 60 s.
- Never transmit with no load on the SMA connector.

## Decision needed from the user before Stage 3
- Which callsign / license covers the transmissions?
- Which frequency?
- Dummy load / attenuator available?
