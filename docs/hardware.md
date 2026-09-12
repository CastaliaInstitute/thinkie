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
