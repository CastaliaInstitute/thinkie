# Hardware inventory (probed 2026-09-12)

Two **LilyGO T-TWR Plus** boards, both identical:

| Alias | Serial port (today) | ESP32-S3 MAC | USB location |
|-------|---------------------|--------------|--------------|
| TWR-A | `/dev/cu.usbmodem1101` | `3c:84:27:cc:18:1c` | 0x110000 |
| TWR-B | `/dev/cu.usbmodem4`    | `48:ca:43:35:b5:b8` | 0x1100000 |

`usbmodemNNN` names depend on which USB port/hub the board is on. Use
`tools/ports.py` to resolve by MAC. **Do not touch** other serial devices on
this Mac (Luminary ESP32-P4 on `usbmodem133101`, CH343 on `wchusbserial5B901846451`,
`cu.T7`, Bluetooth ports).

## Chip / memory
- ESP32-S3 (QFN56, rev v0.2), 40 MHz crystal, **8 MB embedded OPI PSRAM**
- **16 MB flash** (GigaDevice, id c8/4018)
- USB: native USB-Serial/JTAG (VID 0x303A PID 0x1001) — no external UART bridge.
  Consequence: esptool reads/writes at ~92 kbit/s; baud arg is ignored.

## Firmware as delivered (both boards)
- LilyGO **factory firmware** (`examples/Factory` from Xinyuan-LilyGO/T-TWR),
  Arduino-ESP32 2.0.9 / IDF v4.4.4, built 2023-04-20, PlatformIO.
- Auto-detects Rev2.0 vs Rev2.1 at boot; serves a "TWR Setting" web page over WiFi.
- Partition table: `otadata`, `ota_0` (4 MB @0x10000), `ota_1` (4 MB @0x410000),
  `uf2` factory app (@0x810000, tinyuf2 bootloader), `ffat` (7.7 MB @0x850000).
- App image is ~1.75 MB. Backups in `firmware/factory/` (read from TWR-A).
- SA868 radio module runs NiceRF **AT firmware** (`AT+DMOCONNECT`, `AT+DMOSETGROUP`,
  `AT+DMOSETVOLUME`, `AT+SETFILTER`, `AT+RSSI?`, `AT+MODEL`). Not OpenRTX.
  Per LilyGO: flashing OpenRTX onto the SA868 is **irreversible** — don't.
- Band: user reports VHF (SA868 VHF = 134–174 MHz). Confirm with `AT+MODEL` /
  `twr.getBandDefinition()` before assuming.

## Pin map (T-TWR Plus, from `utilities.h` + Rev2.1 schematic)

### Radio (SA868 via `Serial1`, 9600 baud)
| Signal | GPIO | Notes |
|--------|------|-------|
| SA868_TX (ESP→radio UART) | 39 | |
| SA868_RX (radio→ESP UART) | 48 | |
| SA868_PTT | 41 | LOW = transmit (active-low) |
| SA868_PD  | 40 | power-down |
| SA868_RF (H/L power) | 38 | Rev2.0 only; Rev2.1 uses AT+DMOSETGROUP power bit |
| SA868_SQL | 2 | **Rev2.1: carrier/squelch open indicator** — RX activity signal |

### Audio matrix (Rev2.1 only — Rev2.0 has none)
| Signal | GPIO | Notes |
|--------|------|-------|
| MIC_CTRL / AUDIO_SELECT (`MIC_CH_SEL`) | 17 | LOW: onboard mic→radio. HIGH: mic→ESP ADC(15), ESP(18)→radio mic in |
| ESP2SA868_MIC (`ESP2MIC`) | 18 | ESP audio *out* to radio mic input (PWM/LEDC or sigma-delta) |
| ESP_MIC_ADC | 15 | Onboard mic → ESP ADC (valid only when GPIO17 HIGH) |
| SA8682ESP_AUDIO (`AUDIO2ESP` / `RADIO_AUDIO_OUT`) | 1 | Radio audio *out* → ESP ADC1_CH0. **Verify level/bias on scope before trusting** |
| ESP32_PWM_TONE | 45 | ESP → speaker amp (I2S PDM in WAV_Player example) |
| Speaker routing | (PMU ALDO3 / `AudioSwitchEn`) | `twr.routingSpeakerChannel(TWR_ESP_TO_SPK / TWR_RADIO_TO_SPK)` |

### Everything else
| Signal | GPIO |
|--------|------|
| BUTTON_PTT | 3 |
| BUTTON_DOWN / BOOT | 0 |
| ENCODER A / B / OK | 47 / 46 / 21 |
| I2C SDA / SCL (OLED SH1106, PMU AXP2101) | 8 / 9 |
| PMU_IRQ | 4 |
| SPI MOSI / MISO / SCK, SD_CS, USER_CS | 11 / 13 / 12, 10, 14 |
| GNSS TX / RX / PPS (`Serial2`, 9600) | 6 / 5 / 7 |
| PIXELS (WS2812) | 42 |

## Power caveat
LilyGO: **Rev2.1 must have a battery** — the RF module is fed straight from the
battery; USB alone is not enough for TX (and RX may be flaky). High power mode
draws a lot and gains little; use low power.

## Useful upstream
- Repo: https://github.com/Xinyuan-LilyGO/T-TWR (cloned to scratchpad during probe)
- Library: `lib/LilyGo_TWR_Library` (`TWRClass`, `SA868`)
- Examples of interest: `SA868_ATDebug_Example` (AT passthrough),
  `SA868_ESPSendAudio_Example` (ESP→radio audio), `WAV_Player` (ESP→speaker)
- Schematics: `schematic/T-TWR-Plus_Rev2.1.pdf`

## Bench findings 2026-09-12 (Stage 1 firmware on TWR-A)
- Custom RX-only firmware boots, `twr.begin()` detects **Rev2.1**, band **VHF**
  (OLED at 0x3C), SA868 answers AT. 16 kHz audio streams over USB CDC with
  0 gaps / 0 drops / 0 CRC errors.
- `SA868_SQL` (IO2) tracks squelch: goes OPEN with sq=0. Confirmed usable as
  utterance boundary.
- **With USB only (no battery)**: `batt=0mV`, `AT+RSSI?` → 0, and IO1 audio is
  ~silent even with squelch fully open (DC bias ≈ 331 counts ≈ 0.25 V, expected
  ~half-rail). Schematic: SA868 VCC pin 8 = VBAT; AF_OUT → 10 nF → 10K/10K
  offset network + 1N4148 clamps → `AUDIO2ESP` (IO1). RF/audio section is dead
  without a battery. **Attach 18650/21700 before any RX test.**
- Radio volume (AT+DMOSETVOLUME) did not change the IO1 level in that state;
  re-check with battery whether the ESP tap is pre- or post-volume.

## Bench findings, later 2026-09-12 (battery in)
- A "battery" reading of ~0.9–1.0 V with `chgstat=0` (trickle) means the cell is
  flat or not seated; the SA868 (VCC = VBAT) goes silent. A good cell reads
  ~4.1–4.4 V. Swapping cells between boards moved the fault with the cell.
- Factory firmware only probes the SA868 once at boot ("SA8X8 failed" on OLED);
  reset the board after fixing the cell. Custom firmware now re-inits on recovery.
- `twr.begin()` auto-detect misread a Rev2.1 board as Rev2.0 once (it samples
  IO2 at boot). Pinned to `LILYGO_TWR_REV2_1` in firmware.
- RSSI noise floor ≈ 15–20 without antenna, ≈ 55 with antenna in this room;
  NOAA on 162.450 MHz reads ≈ 72 and opens squelch at sq=1 only intermittently
  (use sq=0 while listening to a known-continuous source).
- ADC DC bias with the radio powered ≈ 760 counts. Open-squelch hiss ≈ 3600 RMS;
  NOAA speech peaks ≈ 7400. Recording at -21 dB mean / -9 dB peak — no gain change needed.
- Local `whisper` (openai-whisper CLI, homebrew) base.en transcribes the NOAA
  feed well enough. `faster-whisper` not installed.

## TWR-C (UHF), added 2026-09-12 evening
- Third T-TWR Plus, MAC `dc:da:0c:16:d3:34`, S3R8 / 16 MB. Arrived running the user's
  `meerya_esp32` build (enumerated as "Meerya Sonatino UAC", USB audio only, no CDC, OLED
  dark). Needed BOOT-held reset to reach the ROM bootloader; bootloader+partition table
  backed up to `firmware/factory/uhf-C/`. Now on `thinkie-tx-c`.
- If a board keeps reporting `boot:0x0 (DOWNLOAD)` after flashing, BOOT is still held.

## USB drops on key-up
- Both boards' USB CDC links drop when *either* board keys (0.5 W, antennas next to the
  cables/hub). PMU VBUS/charge limits did not change it → RF, not current. Mitigated in
  firmware: autonomous clip transmit + PSRAM RX ring with catch-up; host reconnects by MAC.
  Physical fix to try: ferrites, shorter cables, antennas away from the hub.
- C's SA868 is **silent**: no reply to `AT+DMOCONNECT` (NiceRF) or `AT+MODEL` (OpenRTX) at
  9600 or 115200, with PD high, DC3 boost on (3.4 V, Rev2.0 path), VBAT 4.2 V, UART RX idle
  high. OLED at 0x3C (Rev2.0 doesn't encode band). Most likely the Rev2.0 "community"
  variant with a blank module for OpenRTX (`sa8x8-fw`), which on Rev2.0 needs external
  programming wiring. Firmware env `thinkie-tx-c` builds with `-DTWR_HW_REV=20`.
