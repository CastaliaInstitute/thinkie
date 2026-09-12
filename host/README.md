# Host agent (not yet written)

Mac-side Python: reads framed PCM + squelch state from TWR-A over USB CDC,
segments utterances, STT → Claude → TTS, sends audio back. See
`docs/architecture.md`.
