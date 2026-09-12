// twr link protocol — board <-> host over USB CDC.
// Mirror of host/twr_link.py. Keep the two in sync.
//
//   frame := 0xAA 0x55 | type:u8 | len:u16le | payload[len] | crc8
//   crc8  := poly 0x07, init 0, over type,len,payload
#pragma once
#include <stdint.h>

#define TWR_SYNC0 0xAA
#define TWR_SYNC1 0x55
#define TWR_MAX_PAYLOAD 1024

enum : uint8_t {
    // device -> host
    T_AUDIO_RX = 0x01,  // u16 seq, then int16le PCM @ AUDIO_RATE_HZ
    T_STATUS   = 0x02,  // struct twr_status
    T_LOG      = 0x03,  // utf-8 text
    // host -> device
    T_SET_FREQ = 0x10,  // u32 rx_hz, u32 tx_hz, u8 sq(0-8), u8 ctcss_rx, u8 ctcss_tx
    T_AUDIO_TX = 0x11,  // int16le PCM  (Stage 3 only)
    T_PTT      = 0x12,  // u8 on        (Stage 3 only)
    T_TX_ARM   = 0x13,  // u32 magic    (Stage 3 only)
    T_SPK      = 0x14,  // u8 route (0=radio->spk, 1=esp->spk), u8 volume 1..8
    T_PING     = 0x15,  // -> T_STATUS
};

#define AUDIO_RATE_HZ      16000
#define AUDIO_FRAME_SAMPLES 320   // 20 ms

struct __attribute__((packed)) twr_status {
    uint8_t  sql;        // 1 = squelch open / carrier present (SA868_SQL low)
    int16_t  rssi;       // from AT+RSSI?
    uint16_t batt_mv;
    uint8_t  tx;         // 1 = PTT asserted (always 0 in TWR_TX_DISABLED builds)
    uint8_t  tx_enabled; // build has TX code and it is armed
    uint32_t rx_hz;
    uint32_t tx_hz;
    uint8_t  sq;
    uint8_t  band;       // 1 = VHF, 2 = UHF, 0 = unknown
    uint8_t  hw_rev;     // 20 or 21
    uint32_t uptime_ms;
    uint16_t adc_dc;     // current DC offset estimate (raw 12-bit)
    uint16_t dropped;    // audio frames dropped since boot (host too slow)
};

static inline uint8_t twr_crc8(const uint8_t *p, size_t n, uint8_t crc = 0) {
    while (n--) {
        crc ^= *p++;
        for (int i = 0; i < 8; i++) crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : (crc << 1);
    }
    return crc;
}
