# Factory firmware backup (from TWR-A, 2026-09-12)

Read with `esptool.py read_flash` before any custom firmware was flashed.
Both boards shipped with identical images.

| File | Offset | Size | Contents |
|------|--------|------|----------|
| `bootloader_and_pt_0x0.bin` | 0x0 | 64 KiB | 2nd-stage bootloader (@0x0) + partition table (@0x8000) + otadata region start |
| `app_0x10000.bin` | 0x10000 | 0x1AC000 | `ota_0`: LilyGO Factory app (Arduino-ESP32 2.0.9, built 2023-04-20) |
| `uf2_0x810000.bin` | 0x810000 | 256 KiB | tinyuf2 factory-app partition |

## Restore (only when asked)
```sh
P=$(tools/ports.py A)
esptool.py --port $P write_flash 0x0 firmware/factory/bootloader_and_pt_0x0.bin \
    0x10000 firmware/factory/app_0x10000.bin \
    0x810000 firmware/factory/uf2_0x810000.bin
```
Upstream copies also exist at Xinyuan-LilyGO/T-TWR `firmware/twr_Rev2.x.bin`.
