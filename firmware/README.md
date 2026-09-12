# T-TWR base-station firmware

PlatformIO, Arduino-ESP32 2.0.9, reuses LilyGO's `LilyGo_TWR_Library` + `XPowersLib`
(vendored in `lib/`, MIT). U8g2 from the registry.

```sh
cd firmware
pio run -e twr-rx                                   # build
pio run -e twr-rx -t upload --upload-port $(../tools/ports.py A)
```

Envs:
- `twr-rx` — Stage 1, receive-only. `-DTWR_TX_DISABLED`; `main.cpp` refuses to
  build without it. No code path touches PTT.

Protocol: `src/protocol.h` (mirrored by `../host/twr_link.py`).
`factory/` holds the as-shipped firmware for restore.
