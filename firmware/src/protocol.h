// thinkie link protocol — board <-> host over USB CDC.
// Mirror of host/thinkie_link.py. Keep the two in sync.
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
    T_AUDIO_RX = 0x01,  // u32 frame_idx, u8 sql, then int16le PCM @ AUDIO_RATE_HZ (20 ms)
    T_STATUS   = 0x02,  // struct twr_status
    T_LOG      = 0x03,  // utf-8 text
    // host -> device
    T_SET_FREQ = 0x10,  // u32 rx_hz, u32 tx_hz, u8 sq(0-8), u8 ctcss_rx, u8 ctcss_tx
    T_AUDIO_TX = 0x11,  // int16le PCM @ AUDIO_RATE_HZ -> current sink (speaker, or radio mic while keyed)
    T_PTT      = 0x12,  // u8 on        (thinkie-tx builds only, and only while armed)
    T_TX_ARM   = 0x13,  // u32 magic (TX_ARM_MAGIC arms, 0 disarms)   (thinkie-tx builds only)
    T_SPK      = 0x14,  // u8 route (0=radio->spk, 1=esp->spk), u8 radio volume 1..8
    T_PING     = 0x15,  // -> T_STATUS (+ T_LOG with PMU detail)
    T_GAIN     = 0x16,  // u8 spk_gain_pct, u8 mic_gain_pct (0..200) for AUDIO_TX playback
    T_FLUSH    = 0x17,  // drop queued AUDIO_TX
    T_CLIP_LOAD= 0x18,  // int16le PCM chunk appended to the on-board clip buffer (PSRAM, up to CLIP_MAX_S)
    T_CLIP_TX  = 0x19,  // key, play the whole clip buffer into the radio mic, unkey; autonomous (twr-tx builds)
    T_CLIP_PLAY= 0x1A,  // play the clip buffer on the speaker (no RF)
    T_CLIP_CLEAR=0x1B,
};
#define CLIP_MAX_S 45

#define TX_ARM_MAGIC   0x54582D4FUL   // "TX-O"
#define TX_ARM_TTL_MS  (10UL * 60UL * 1000UL)   // arm expires after 10 min
#define TX_MAX_KEY_MS  (60UL * 1000UL)          // forced unkey after 60 s

#define AUDIO_RATE_HZ      16000
#define AUDIO_FRAME_SAMPLES 320   // 20 ms
#define RX_RING_S 60              // seconds of received audio kept in PSRAM for catch-up after a USB drop

struct __attribute__((packed)) twr_status {
    uint8_t  sql;        // 1 = squelch open / carrier present (SA868_SQL low)
    int16_t  rssi;       // from AT+RSSI?
    uint16_t batt_mv;
    uint8_t  tx;         // 1 = PTT asserted
    uint8_t  tx_enabled; // 1 = armed (thinkie-tx builds only)
    uint32_t rx_hz;
    uint32_t tx_hz;
    uint8_t  sq;
    uint8_t  band;       // 1 = VHF, 2 = UHF, 0 = unknown
    uint8_t  hw_rev;     // 20 or 21
    uint32_t uptime_ms;
    uint16_t adc_dc;     // current DC offset estimate (raw 12-bit)
    uint16_t dropped;    // audio frames dropped since boot (host too slow)
    uint8_t  board_id;   // 'A' or 'B'
    uint8_t  sink;       // 0 = speaker, 1 = radio mic
    uint16_t play_queued;// AUDIO_TX frames waiting to play
    uint8_t  tx_build;   // 1 if firmware has TX code compiled in
    uint32_t rx_frames;  // frames captured since boot (stream catches up to this after a reconnect)
};

static inline uint8_t twr_crc8(const uint8_t *p, size_t n, uint8_t crc = 0) {
    while (n--) {
        crc ^= *p++;
        for (int i = 0; i < 8; i++) crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : (crc << 1);
    }
    return crc;
}
