/**
 * twr base-station firmware — Stage 1: receive-only USB audio bridge.
 *
 * Streams SA868 receive audio (GPIO1, ADC1_CH0, 16 kHz / 16-bit) and squelch
 * state to the host over USB CDC using the framed protocol in protocol.h.
 * Frequency / squelch are host-configurable. With TWR_TX_DISABLED (the only
 * env defined so far) there is no code path that drives PTT.
 */
#include <Arduino.h>
#include <U8g2lib.h>
#include <Adafruit_NeoPixel.h>
#include <driver/adc.h>
#include "LilyGo_TWR.h"
#include "protocol.h"

#ifndef TWR_TX_DISABLED
#error "Only the receive-only build exists so far. Build env twr-rx."
#endif

// ---- defaults ------------------------------------------------------------
// NOAA weather radio, Austin-area WXK27 is 162.400; 162.550 is the most common
// nationally. Host can retune with T_SET_FREQ.
static const uint32_t DEFAULT_RX_HZ = 162550000UL;
static const uint8_t  DEFAULT_SQ    = 1;

// ---- globals -------------------------------------------------------------
U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

static Adafruit_NeoPixel pixel(1, PIXELS_PIN, NEO_GRB + NEO_KHZ800);
static QueueHandle_t audioQ;            // int16_t[AUDIO_FRAME_SAMPLES] frames
static volatile uint16_t g_adcDc = 2048;
static volatile uint16_t g_dropped = 0;
static int16_t  g_rssi = 0;
static bool     g_radioOk = false;
static bool     g_oledOk = false;

// ---- framing -------------------------------------------------------------
static void sendFrame(uint8_t type, const void *payload, uint16_t len)
{
    if (!Serial) return;                         // host not connected (no DTR)
    uint8_t hdr[5] = { TWR_SYNC0, TWR_SYNC1, type, (uint8_t)(len & 0xFF), (uint8_t)(len >> 8) };
    uint8_t crc = twr_crc8(hdr + 2, 3);
    crc = twr_crc8((const uint8_t *)payload, len, crc);
    Serial.write(hdr, sizeof(hdr));
    if (len) Serial.write((const uint8_t *)payload, len);
    Serial.write(crc);
}

static void logf(const char *fmt, ...)
{
    char buf[200];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    sendFrame(T_LOG, buf, min(n, (int)sizeof(buf)));
}

static void logPmu()
{
    logf("PMU: batt=%umV(%u%%) conn=%d vbus=%umV(in=%d) vsys=%umV chg=%d done=%d chgcur=%u chgstat=%u",
         twr.getBattVoltage(), twr.getBatteryPercent(), twr.isBatteryConnect(),
         twr.getVbusVoltage(), twr.isVbusIn(), twr.getSystemVoltage(),
         twr.isCharging(), twr.isChargeDone(), twr.getChargerConstantCurr(), twr.getChargerStatus());
}

static void sendStatus()
{
    twr_status s = {};
    s.sql        = TWRClass::isReceiving ? 1 : 0;
    s.rssi       = g_rssi;
    s.batt_mv    = twr.getBattVoltage();
    s.tx         = 0;
    s.tx_enabled = 0;
    s.rx_hz      = radio.getStetting().recvFreq;
    s.tx_hz      = radio.getStetting().transFreq;
    s.sq         = radio.getStetting().SQ;
    s.band       = twr.getBandDefinition() == SA8X8_VHF ? 1 : twr.getBandDefinition() == SA8X8_UHF ? 2 : 0;
    s.hw_rev     = twr.getVersion() == TWRClass::TWR_REV2V1 ? 21 : 20;
    s.uptime_ms  = millis();
    s.adc_dc     = g_adcDc;
    s.dropped    = g_dropped;
    sendFrame(T_STATUS, &s, sizeof(s));
}

// ---- ADC continuous capture ---------------------------------------------
// GPIO1 == ADC1_CH0 on ESP32-S3. DMA at AUDIO_RATE_HZ, 12-bit, 11 dB atten
// (0..~3.1 V). DC offset tracked with a slow IIR and removed; result scaled
// to int16.
static bool adcStart()
{
    adc_digi_init_config_t init = {};
    init.max_store_buf_size = 4096;
    init.conv_num_each_intr = 256;
    init.adc1_chan_mask     = BIT(0);
    init.adc2_chan_mask     = 0;
    if (adc_digi_initialize(&init) != ESP_OK) return false;

    adc_digi_pattern_config_t pat = {};
    pat.atten     = ADC_ATTEN_DB_11;
    pat.channel   = ADC1_CHANNEL_0;
    pat.unit      = 0;
    pat.bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;   // 12 on S3

    adc_digi_configuration_t cfg = {};
    cfg.conv_limit_en  = 0;
    cfg.conv_limit_num = 250;
    cfg.pattern_num    = 1;
    cfg.adc_pattern    = &pat;
    cfg.sample_freq_hz = AUDIO_RATE_HZ;
    cfg.conv_mode      = ADC_CONV_SINGLE_UNIT_1;
    cfg.format         = ADC_DIGI_OUTPUT_FORMAT_TYPE2;
    if (adc_digi_controller_configure(&cfg) != ESP_OK) return false;
    return adc_digi_start() == ESP_OK;
}

static void adcTask(void *)
{
    static uint8_t raw[256 * SOC_ADC_DIGI_RESULT_BYTES];
    static int16_t frame[AUDIO_FRAME_SAMPLES];
    size_t fill = 0;
    int32_t dc = 2048 << 8;                       // Q8 running mean
    for (;;) {
        uint32_t got = 0;
        esp_err_t err = adc_digi_read_bytes(raw, sizeof(raw), &got, 100);
        if (err != ESP_OK && err != ESP_ERR_TIMEOUT) { vTaskDelay(1); continue; }
        for (uint32_t i = 0; i + SOC_ADC_DIGI_RESULT_BYTES <= got; i += SOC_ADC_DIGI_RESULT_BYTES) {
            adc_digi_output_data_t *d = (adc_digi_output_data_t *)&raw[i];
            if (d->type2.channel != ADC1_CHANNEL_0) continue;
            int32_t v = d->type2.data;            // 0..4095
            dc += ((v << 8) - dc) >> 10;          // ~1 s time constant at 16 kHz
            int32_t s = (v - (dc >> 8)) << 4;     // center, scale to ~int16
            frame[fill++] = (int16_t)constrain(s, -32768, 32767);
            if (fill == AUDIO_FRAME_SAMPLES) {
                fill = 0;
                g_adcDc = dc >> 8;
                if (xQueueSend(audioQ, frame, 0) != pdTRUE) g_dropped++;
            }
        }
    }
}

// ---- host command parser -------------------------------------------------
static void handleCommand(uint8_t type, const uint8_t *p, uint16_t len)
{
    switch (type) {
    case T_PING:
        sendStatus();
        logPmu();
        break;
    case T_SET_FREQ: {
        if (len < 11) { logf("SET_FREQ: short payload"); return; }
        uint32_t rx, tx; memcpy(&rx, p, 4); memcpy(&tx, p + 4, 4);
        uint8_t sq = p[8], crx = p[9], ctx = p[10];
        if (!radio.checkFreq(rx) || !radio.checkFreq(tx)) { logf("SET_FREQ: out of band"); return; }
        // setGroup only programs the SA868 registers; nothing here keys PTT.
        bool ok = radio.setGroup(false, tx, rx, (teCXCSS)ctx, sq, (teCXCSS)crx);
        logf("SET_FREQ rx=%lu tx=%lu sq=%u -> %s", rx, tx, sq, ok ? "ok" : "FAIL");
        sendStatus();
        break;
    }
    case T_SPK: {
        if (len < 2) return;
        twr.routingSpeakerChannel(p[0] ? TWRClass::TWR_ESP_TO_SPK : TWRClass::TWR_RADIO_TO_SPK);
        radio.setVolume(constrain(p[1], 1, 8));
        logf("SPK route=%u vol=%u", p[0], p[1]);
        break;
    }
    case T_AUDIO_TX: case T_PTT: case T_TX_ARM:
        logf("TX is compiled out of this build (TWR_TX_DISABLED)");
        break;
    default:
        logf("unknown cmd 0x%02x", type);
    }
}

static void pollHost()
{
    // sync0 sync1 type len_lo len_hi payload crc
    static uint8_t buf[TWR_MAX_PAYLOAD + 6];
    static size_t n = 0;
    while (Serial.available()) {
        uint8_t c = Serial.read();
        if (n == 0 && c != TWR_SYNC0) continue;
        if (n == 1 && c != TWR_SYNC1) { n = 0; continue; }
        buf[n++] = c;
        if (n >= 5) {
            uint16_t len = buf[3] | (buf[4] << 8);
            if (len > TWR_MAX_PAYLOAD) { n = 0; continue; }
            if (n == (size_t)5 + len + 1) {
                uint8_t crc = twr_crc8(buf + 2, 3 + len);
                if (crc == buf[5 + len]) handleCommand(buf[2], buf + 5, len);
                else logf("bad crc on cmd 0x%02x", buf[2]);
                n = 0;
            }
        }
        if (n >= sizeof(buf)) n = 0;
    }
}

// ---- OLED ---------------------------------------------------------------
static void drawOled()
{
    if (!g_oledOk) return;
    char line[32];
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x12_tr);
    u8g2.drawStr(0, 10, "RX ONLY");
    u8g2.drawStr(56, 10, Serial ? "USB" : "---");
    u8g2.setFont(u8g2_font_inb38_mr);
    u8g2.drawStr(92, 44, "A");
    u8g2.setFont(u8g2_font_10x20_tr);
    uint32_t f = radio.getStetting().recvFreq;
    snprintf(line, sizeof(line), "%3lu.%03lu", f / 1000000UL, (f % 1000000UL) / 1000);
    u8g2.drawStr(0, 32, line);
    u8g2.setFont(u8g2_font_6x12_tr);
    snprintf(line, sizeof(line), "SQL %s", TWRClass::isReceiving ? "OPEN" : "----");
    u8g2.drawStr(0, 48, line);
    snprintf(line, sizeof(line), "RSSI %d %umV", g_rssi, twr.getBattVoltage());
    u8g2.drawStr(0, 62, line);
    u8g2.sendBuffer();
}

// ---- setup / loop ---------------------------------------------------------
void setup()
{
    Serial.begin(115200);
    Serial.setTxTimeoutMs(5);        // never block the loop on a stalled host
    delay(300);

    pixel.begin(); pixel.setPixelColor(0, pixel.Color(0, 0, 40)); pixel.show();   // blue = board A
    bool ok = twr.begin(LILYGO_TWR_REV2_1);   // auto-detect samples IO2 and misreads; both boards are Rev2.1
    if (!ok) { while (1) { logf("PMU/board init failed"); delay(1000); } }

    uint8_t addr = twr.getOLEDAddress();
    if (addr != 0xFF) {
        u8g2.setI2CAddress(addr << 1);
        g_oledOk = u8g2.begin();
    }

    if (twr.getVersion() == TWRClass::TWR_REV2V1) {
        radio.setPins(SA868_PTT_PIN, SA868_PD_PIN);
        g_radioOk = radio.begin(RadioSerial, twr.getBandDefinition());
    } else {
        radio.setPins(SA868_PTT_PIN, SA868_PD_PIN, SA868_RF_PIN);
        g_radioOk = radio.begin(RadioSerial, SA8X8_VHF);
    }
    if (!g_radioOk) {
        // Keep running so the host can still read PMU state; retry the radio every 5 s.
        while (!g_radioOk) {
            logf("SA868 not responding (VBAT ok? antenna?)"); logPmu(); drawOled();
            delay(5000);
            g_radioOk = radio.begin(RadioSerial, twr.getBandDefinition());
        }
        logf("SA868 came up after retry");
    }

    radio.lowPower();                                    // config only; no TX path exists
    radio.setBandWidth(12500);
    radio.setGroup(false, DEFAULT_RX_HZ, DEFAULT_RX_HZ, E_CXCSS_NONE, DEFAULT_SQ, E_CXCSS_NONE);
    radio.setFilter(false, false, false);                // flat audio for STT
    radio.setVolume(4);
    twr.routingSpeakerChannel(TWRClass::TWR_RADIO_TO_SPK);
    twr.routingMicrophoneChannel(TWRClass::TWR_MIC_TO_RADIO); // onboard mic stays off the ESP

    audioQ = xQueueCreate(8, AUDIO_FRAME_SAMPLES * sizeof(int16_t));
    if (!adcStart()) { logf("ADC continuous init failed"); }
    xTaskCreatePinnedToCore(adcTask, "adc", 4096, nullptr, configMAX_PRIORITIES - 2, nullptr, 1);

    logf("twr-rx up: rev%s band=%s rx=%lu", twr.getVersion() == TWRClass::TWR_REV2V1 ? "2.1" : "2.0",
         twr.getBandDefinition() == SA8X8_VHF ? "VHF" : "UHF", radio.getStetting().recvFreq);
    sendStatus();
}

void loop()
{
    static int16_t frame[AUDIO_FRAME_SAMPLES];
    static uint16_t seq = 0;
    static uint32_t lastStatus = 0, lastOled = 0, lastRssi = 0;
    static bool lastSql = false;

    twr.tick();
    pollHost();

    while (xQueueReceive(audioQ, frame, 0) == pdTRUE) {
        uint8_t pkt[2 + AUDIO_FRAME_SAMPLES * 2];
        memcpy(pkt, &seq, 2); seq++;
        memcpy(pkt + 2, frame, sizeof(frame));
        sendFrame(T_AUDIO_RX, pkt, sizeof(pkt));
    }

    uint32_t now = millis();
    bool sql = TWRClass::isReceiving;
    if (sql != lastSql) { lastSql = sql; sendStatus(); }
    if (now - lastRssi >= 500)  { lastRssi = now; g_rssi = radio.getRSSI(); }
    if (now - lastStatus >= 1000) { lastStatus = now; sendStatus(); }
    if (now - lastOled >= 250)  { lastOled = now; drawOled();
        pixel.setPixelColor(0, sql ? pixel.Color(0, 60, 0) : pixel.Color(0, 0, 40)); pixel.show(); }
    delay(1);
}
