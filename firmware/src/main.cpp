/**
 * Walkie-Thinkie base-station firmware — USB audio bridge for the LilyGO T-TWR Plus.
 *
 *  RX : SA868 audio out (GPIO1, ADC1_CH0) -> 16 kHz/16-bit -> USB CDC frames, plus
 *       squelch state, RSSI, battery.
 *  OUT: host PCM (T_AUDIO_TX) -> I2S PDM -> board speaker (GPIO45)      [all builds]
 *                              -> I2S PDM -> SA868 mic in (GPIO18)      [thinkie-tx builds, keyed only]
 *  TX : thinkie-tx builds only. Boots disarmed; host must T_TX_ARM each session (10 min TTL),
 *       key with T_PTT; forced unkey after TX_MAX_KEY_MS or if the host goes away.
 *       TWR_TX_DISABLED builds contain no code path that drives PTT.
 */
#include <Arduino.h>
#include <U8g2lib.h>
#include <Adafruit_NeoPixel.h>
#include <driver/adc.h>
#include <driver/i2s.h>
#include "LilyGo_TWR.h"
#include "protocol.h"

#ifndef TWR_BOARD_ID
#error "Build with -DTWR_BOARD_ID=65 (A) or 66 (B)"
#endif
#ifdef TWR_TX_DISABLED
#define TX_BUILD 0
#else
#define TX_BUILD 1
#endif

// ---- defaults ------------------------------------------------------------
static const uint32_t DEFAULT_RX_HZ = 162550000UL;   // NOAA; host retunes with T_SET_FREQ
static const uint8_t  DEFAULT_SQ    = 1;
static const int      PLAY_QUEUE_FRAMES = 150;        // 3 s of host audio buffered on-board

// ---- globals -------------------------------------------------------------
U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
static Adafruit_NeoPixel pixel(1, PIXELS_PIN, NEO_GRB + NEO_KHZ800);

static QueueHandle_t audioQ;            // ADC frames  int16_t[AUDIO_FRAME_SAMPLES]
static QueueHandle_t playQ;             // host frames int16_t[AUDIO_FRAME_SAMPLES]
static volatile uint16_t g_adcDc = 2048;
static volatile uint16_t g_dropped = 0;
static int16_t  g_rssi = 0;
static bool     g_radioOk = false;
static bool     g_oledOk = false;
static uint8_t  g_sink = 0;             // 0 speaker, 1 radio mic
static uint8_t  g_spkGain = 100, g_micGain = 25;
static bool     g_armed = false;
static uint32_t g_armExpiry = 0;
static bool     g_tx = false;
static uint32_t g_txStart = 0;
static int16_t *g_clip = nullptr;          // PSRAM clip buffer for autonomous playback/transmit
static size_t   g_clipLen = 0;             // samples
static volatile int g_clipJob = 0;         // 0 idle, 1 play on speaker, 2 transmit

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
    s.tx         = g_tx ? 1 : 0;
    s.tx_enabled = g_armed ? 1 : 0;
    s.rx_hz      = radio.getStetting().recvFreq;
    s.tx_hz      = radio.getStetting().transFreq;
    s.sq         = radio.getStetting().SQ;
    s.band       = twr.getBandDefinition() == SA8X8_VHF ? 1 : twr.getBandDefinition() == SA8X8_UHF ? 2 : 0;
    s.hw_rev     = twr.getVersion() == TWRClass::TWR_REV2V1 ? 21 : 20;
    s.uptime_ms  = millis();
    s.adc_dc     = g_adcDc;
    s.dropped    = g_dropped;
    s.board_id   = TWR_BOARD_ID;
    s.sink       = g_sink;
    s.play_queued= playQ ? uxQueueMessagesWaiting(playQ) : 0;
    s.tx_build   = TX_BUILD;
    s.play_queued= g_clipJob ? 0xFFFF : s.play_queued;   // 0xFFFF = clip job running
    sendFrame(T_STATUS, &s, sizeof(s));
}

// ---- ADC continuous capture (radio -> host) --------------------------------
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
    pat.bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;

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
    int32_t dc = 2048 << 8;
    for (;;) {
        uint32_t got = 0;
        esp_err_t err = adc_digi_read_bytes(raw, sizeof(raw), &got, 100);
        if (err != ESP_OK && err != ESP_ERR_TIMEOUT) { vTaskDelay(1); continue; }
        for (uint32_t i = 0; i + SOC_ADC_DIGI_RESULT_BYTES <= got; i += SOC_ADC_DIGI_RESULT_BYTES) {
            adc_digi_output_data_t *d = (adc_digi_output_data_t *)&raw[i];
            if (d->type2.channel != ADC1_CHANNEL_0) continue;
            int32_t v = d->type2.data;
            dc += ((v << 8) - dc) >> 10;
            int32_t s = (v - (dc >> 8)) << 4;
            frame[fill++] = (int16_t)constrain(s, -32768, 32767);
            if (fill == AUDIO_FRAME_SAMPLES) {
                fill = 0;
                g_adcDc = dc >> 8;
                if (xQueueSend(audioQ, frame, 0) != pdTRUE) g_dropped++;
            }
        }
    }
}

// ---- I2S PDM output (host -> speaker / radio mic) ---------------------------
static void setSink(uint8_t sink)
{
    uint8_t oldPin = g_sink ? ESP2SA868_MIC : ESP32_PWM_TONE;
    uint8_t newPin = sink   ? ESP2SA868_MIC : ESP32_PWM_TONE;
    if (oldPin != newPin) pinMode(oldPin, INPUT);           // detach previous output
    i2s_pin_config_t pins = {};
    pins.mck_io_num = I2S_PIN_NO_CHANGE; pins.bck_io_num = I2S_PIN_NO_CHANGE;
    pins.ws_io_num  = I2S_PIN_NO_CHANGE; pins.data_in_num = I2S_PIN_NO_CHANGE;
    pins.data_out_num = newPin;
    i2s_set_pin(I2S_NUM_0, &pins);
    g_sink = sink;
}

static bool i2sStart()
{
    i2s_config_t cfg = {};
    cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_PDM);
    cfg.sample_rate = AUDIO_RATE_HZ;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 6;
    cfg.dma_buf_len = AUDIO_FRAME_SAMPLES;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = true;                          // silence on underrun
    if (i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL) != ESP_OK) return false;
    setSink(0);
    return true;
}

static void writeFrame(const int16_t *frame, int n)
{
    static int16_t stereo[AUDIO_FRAME_SAMPLES * 2];
    int32_t gain = g_sink ? g_micGain : g_spkGain;
    for (int i = 0; i < n; i++) {
        int32_t v = (frame[i] * gain) / 100;
        v = constrain(v, -32768, 32767);
        stereo[2 * i] = stereo[2 * i + 1] = (int16_t)v;
    }
    size_t written = 0;
    i2s_write(I2S_NUM_0, stereo, n * 4, &written, portMAX_DELAY);
}

static void playClipBlocking()
{
    for (size_t off = 0; off < g_clipLen; off += AUDIO_FRAME_SAMPLES)
        writeFrame(g_clip + off, min((size_t)AUDIO_FRAME_SAMPLES, g_clipLen - off));
    static const int16_t zeros[AUDIO_FRAME_SAMPLES] = {0};
    for (int i = 0; i < 6; i++) writeFrame(zeros, AUDIO_FRAME_SAMPLES);   // flush DMA with silence
}

#if TX_BUILD
static void key(bool espAudio);
static void unkey(const char *why);
#endif

static void playTask(void *)
{
    static int16_t frame[AUDIO_FRAME_SAMPLES];
    for (;;) {
        if (g_clipJob) {
            int job = g_clipJob;
            if (job == 1) {
                twr.routingSpeakerChannel(TWRClass::TWR_ESP_TO_SPK);
                playClipBlocking();
                twr.routingSpeakerChannel(TWRClass::TWR_RADIO_TO_SPK);
            }
#if TX_BUILD
            else if (job == 2) {
                key(true);
                if (g_tx) { delay(250); playClipBlocking(); delay(120); unkey("clip done"); }
            }
#endif
            g_clipJob = 0;
            logf("clip job %d finished (%u samples)", job, (unsigned)g_clipLen);
            continue;
        }
        if (xQueueReceive(playQ, frame, pdMS_TO_TICKS(100)) != pdTRUE) continue;
        writeFrame(frame, AUDIO_FRAME_SAMPLES);
    }
}

// ---- transmit control (thinkie-tx builds) ---------------------------------------
#if TX_BUILD
static void unkey(const char *why)
{
    if (!g_tx) return;
    radio.receive();                                          // PTT high
    g_tx = false;
    setSink(0);
    twr.routingMicrophoneChannel(TWRClass::TWR_MIC_TO_RADIO);
    logf("PTT off (%s, %lu ms)", why, millis() - g_txStart);
    sendStatus();
}

static void key(bool espAudio)
{
    if (g_tx) return;
    if (!g_armed) { logf("PTT refused: not armed"); return; }
    if (!g_radioOk) { logf("PTT refused: radio down"); return; }
    if (twr.getBattVoltage() < 3400) { logf("PTT refused: batt %umV", twr.getBattVoltage()); return; }
    if (espAudio) {
        twr.routingMicrophoneChannel(TWRClass::TWR_MIC_TO_ESP);  // GPIO17 high: ESP -> radio mic
        setSink(1);
    } else {
        twr.routingMicrophoneChannel(TWRClass::TWR_MIC_TO_RADIO); // onboard mic -> radio
    }
    radio.transmit();                                         // PTT low
    g_tx = true; g_txStart = millis();
    logf("PTT on (%s) tx=%lu Hz low-power", espAudio ? "esp audio" : "onboard mic", radio.getStetting().transFreq);
    sendStatus();
}

// Physical PTT button (GPIO3, active low): human talks into the onboard mic. Still needs arm.
static void pollPttButton()
{
    static bool wasDown = false;
    static uint32_t lastChange = 0;
    bool down = digitalRead(BUTTON_PTT_PIN) == LOW;
    if (down != wasDown && millis() - lastChange > 30) {
        wasDown = down; lastChange = millis();
        if (down) key(false); else if (g_tx) unkey("button");
    }
}
#endif

// ---- host command parser -------------------------------------------------
static void handleCommand(uint8_t type, const uint8_t *p, uint16_t len)
{
    switch (type) {
    case T_PING:
        sendStatus(); logPmu();
        break;
    case T_SET_FREQ: {
        if (len < 11) { logf("SET_FREQ: short payload"); return; }
        uint32_t rx, tx; memcpy(&rx, p, 4); memcpy(&tx, p + 4, 4);
        uint8_t sq = p[8], crx = p[9], ctx = p[10];
        if (!radio.checkFreq(rx) || !radio.checkFreq(tx)) { logf("SET_FREQ: out of band"); return; }
        bool ok = radio.setGroup(false, tx, rx, (teCXCSS)ctx, sq, (teCXCSS)crx);   // low power, registers only
        logf("SET_FREQ rx=%lu tx=%lu sq=%u -> %s", rx, tx, sq, ok ? "ok" : "FAIL");
        sendStatus();
        break;
    }
    case T_SPK: {
        if (len < 2) return;
        twr.routingSpeakerChannel(p[0] ? TWRClass::TWR_ESP_TO_SPK : TWRClass::TWR_RADIO_TO_SPK);
        radio.setVolume(constrain(p[1], 1, 8));
        logf("SPK route=%s vol=%u", p[0] ? "esp" : "radio", p[1]);
        break;
    }
    case T_GAIN:
        if (len < 2) return;
        g_spkGain = min<uint8_t>(p[0], 200); g_micGain = min<uint8_t>(p[1], 200);
        logf("gain spk=%u%% mic=%u%%", g_spkGain, g_micGain);
        break;
    case T_AUDIO_TX: {
        if (len != AUDIO_FRAME_SAMPLES * 2) { logf("AUDIO_TX: want %u bytes, got %u", AUDIO_FRAME_SAMPLES * 2, len); return; }
        if (xQueueSend(playQ, p, 0) != pdTRUE) g_dropped++;
        break;
    }
    case T_FLUSH:
        xQueueReset(playQ);
        break;
    case T_CLIP_CLEAR:
        if (!g_clipJob) g_clipLen = 0;
        break;
    case T_CLIP_LOAD: {
        if (g_clipJob) return;
        size_t n = len / 2, cap = (size_t)CLIP_MAX_S * AUDIO_RATE_HZ;
        if (!g_clip) g_clip = (int16_t *)ps_malloc(cap * sizeof(int16_t));
        if (!g_clip) { logf("clip: no PSRAM"); return; }
        if (g_clipLen + n > cap) { logf("clip: full at %us", CLIP_MAX_S); return; }
        memcpy(g_clip + g_clipLen, p, n * 2); g_clipLen += n;
        break;
    }
    case T_CLIP_PLAY:
        if (g_clipLen && !g_clipJob) { logf("clip: playing %.1fs on speaker", g_clipLen / (float)AUDIO_RATE_HZ); g_clipJob = 1; }
        break;
#if TX_BUILD
    case T_CLIP_TX:
        if (!g_armed) { logf("clip TX refused: not armed"); return; }
        if (g_clipLen && !g_clipJob) { logf("clip: transmitting %.1fs", g_clipLen / (float)AUDIO_RATE_HZ); g_clipJob = 2; }
        break;
#else
    case T_CLIP_TX:
        logf("TX is compiled out of this build (TWR_TX_DISABLED)");
        break;
#endif
#if TX_BUILD
    case T_TX_ARM: {
        uint32_t magic = 0; if (len >= 4) memcpy(&magic, p, 4);
        if (magic == TX_ARM_MAGIC) { g_armed = true; g_armExpiry = millis() + TX_ARM_TTL_MS; logf("TX ARMED for %lu s", TX_ARM_TTL_MS / 1000); }
        else { unkey("disarm"); g_armed = false; logf("TX disarmed"); }
        sendStatus();
        break;
    }
    case T_PTT:
        if (len >= 1 && p[0]) key(true); else unkey("host");
        break;
#else
    case T_TX_ARM: case T_PTT:
        logf("TX is compiled out of this build (TWR_TX_DISABLED)");
        break;
#endif
    default:
        logf("unknown cmd 0x%02x", type);
    }
}

static void pollHost()
{
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

// ---- OLED / LED -------------------------------------------------------------
static void drawOled()
{
    if (!g_oledOk) return;
    char line[32];
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_6x12_tr);
    u8g2.drawStr(0, 10, TX_BUILD ? (g_tx ? "** TX **" : g_armed ? "TX ARMED" : "TX safe") : "RX ONLY");
    u8g2.drawStr(56, 10, Serial ? "USB" : "---");
    u8g2.setFont(u8g2_font_inb38_mr);
    char id[2] = { TWR_BOARD_ID, 0 };
    u8g2.drawStr(92, 44, id);
    u8g2.setFont(u8g2_font_10x20_tr);
    uint32_t f = radio.getStetting().recvFreq;
    snprintf(line, sizeof(line), "%3lu.%03lu", f / 1000000UL, (f % 1000000UL) / 1000);
    u8g2.drawStr(0, 32, line);
    u8g2.setFont(u8g2_font_6x12_tr);
    snprintf(line, sizeof(line), "SQL %s %s", TWRClass::isReceiving ? "OPEN" : "----", g_sink ? "MIC" : "SPK");
    u8g2.drawStr(0, 48, line);
    snprintf(line, sizeof(line), "RSSI %d %umV", g_rssi, twr.getBattVoltage());
    u8g2.drawStr(0, 62, line);
    u8g2.sendBuffer();
}

static void updateLed(bool sql)
{
    uint32_t c;
    if (g_tx)       c = pixel.Color(80, 0, 0);                              // red: transmitting
    else if (sql)   c = pixel.Color(0, 60, 0);                              // green: receiving
    else if (TWR_BOARD_ID == 'A') c = pixel.Color(0, 0, 40);                // blue: A idle
    else            c = pixel.Color(40, 0, 40);                             // magenta: B idle
    pixel.setPixelColor(0, c); pixel.show();
}

// ---- setup / loop ---------------------------------------------------------
void setup()
{
    Serial.setRxBufferSize(32768);           // host streams 32 kB/s of audio; loop can stall ~50 ms
    Serial.begin(115200);
    Serial.setTxTimeoutMs(5);
    delay(300);

    pixel.begin(); updateLed(false);
    bool ok = twr.begin(LILYGO_TWR_REV2_1);     // auto-detect samples IO2 and misreads; both boards are Rev2.1
    if (!ok) { while (1) { logf("PMU/board init failed"); delay(1000); } }
    // Keep USB draw inside a hub port's budget: the PA sags VBAT on key-up and the charger
    // ramping to the library's 2 A VBUS limit trips the port (seen as CDC disconnects on PTT).
    twr.setVbusCurrentLimit(XPOWERS_AXP2101_VBUS_CUR_LIM_500MA);
    twr.setChargerConstantCurr(XPOWERS_AXP2101_CHG_CUR_200MA);

    uint8_t addr = twr.getOLEDAddress();
    if (addr != 0xFF) { u8g2.setI2CAddress(addr << 1); g_oledOk = u8g2.begin(); }

#if TX_BUILD
    pinMode(BUTTON_PTT_PIN, INPUT_PULLUP);
#endif
    radio.setPins(SA868_PTT_PIN, SA868_PD_PIN);
    g_radioOk = radio.begin(RadioSerial, twr.getBandDefinition());   // leaves PTT high (idle)
    if (!g_radioOk) {
        while (!g_radioOk) {
            logf("SA868 not responding (VBAT ok?)"); logPmu(); drawOled();
            delay(5000);
            g_radioOk = radio.begin(RadioSerial, twr.getBandDefinition());
        }
        logf("SA868 came up after retry");
    }

    radio.lowPower();
    radio.setBandWidth(12500);
    radio.setGroup(false, DEFAULT_RX_HZ, DEFAULT_RX_HZ, E_CXCSS_NONE, DEFAULT_SQ, E_CXCSS_NONE);
    radio.setFilter(false, false, false);
    radio.setVolume(4);
    twr.routingSpeakerChannel(TWRClass::TWR_RADIO_TO_SPK);
    twr.routingMicrophoneChannel(TWRClass::TWR_MIC_TO_RADIO);

    audioQ = xQueueCreate(8, AUDIO_FRAME_SAMPLES * sizeof(int16_t));
    playQ  = xQueueCreate(PLAY_QUEUE_FRAMES, AUDIO_FRAME_SAMPLES * sizeof(int16_t));
    if (!adcStart()) logf("ADC continuous init failed");
    if (!i2sStart()) logf("I2S PDM init failed");
    xTaskCreatePinnedToCore(adcTask,  "adc",  4096, nullptr, configMAX_PRIORITIES - 2, nullptr, 1);
    xTaskCreatePinnedToCore(playTask, "play", 4096, nullptr, configMAX_PRIORITIES - 3, nullptr, 1);

    logf("thinkie-%s-%c up: band=%s rx=%lu", TX_BUILD ? "tx" : "rx", TWR_BOARD_ID,
         twr.getBandDefinition() == SA8X8_VHF ? "VHF" : "UHF", radio.getStetting().recvFreq);
    sendStatus();
}

void loop()
{
    static int16_t frame[AUDIO_FRAME_SAMPLES];
    static uint16_t seq = 0;
    static uint32_t lastStatus = 0, lastOled = 0, lastRssi = 0;
    static bool lastSql = false;
    static uint8_t rssiFails = 0;

    twr.tick();
    pollHost();

    while (xQueueReceive(audioQ, frame, 0) == pdTRUE) {
        if (g_tx) continue;                                   // nothing useful on the ADC while keyed
        uint8_t pkt[2 + AUDIO_FRAME_SAMPLES * 2];
        memcpy(pkt, &seq, 2); seq++;
        memcpy(pkt + 2, frame, sizeof(frame));
        sendFrame(T_AUDIO_RX, pkt, sizeof(pkt));
    }

    uint32_t now = millis();
    bool sql = TWRClass::isReceiving;
    if (sql != lastSql) { lastSql = sql; sendStatus(); }

#if TX_BUILD
    pollPttButton();
    if (g_tx && (now - g_txStart > TX_MAX_KEY_MS)) unkey("max key time");
    if (g_tx && !Serial && g_clipJob != 2) unkey("host gone");   // autonomous clip TX finishes on its own (bounded by TX_MAX_KEY_MS)
    if (g_armed && (int32_t)(now - g_armExpiry) > 0) { unkey("arm expired"); g_armed = false; logf("TX arm expired"); sendStatus(); }
#endif

    uint32_t rssiPeriod = rssiFails >= 3 ? 5000 : 500;
    if (!g_tx && now - lastRssi >= rssiPeriod) {
        lastRssi = now;
        int r = radio.getRSSI();
        if (r > 0) { g_rssi = r; rssiFails = 0; }
        else {
            g_rssi = 0;
            if (rssiFails < 3) rssiFails++;
            if (rssiFails >= 3 && twr.getBattVoltage() > 3300) {
                logf("radio silent; re-init (batt=%umV)", twr.getBattVoltage());
                if (radio.begin(RadioSerial, twr.getBandDefinition())) {
                    radio.setGroup(false, DEFAULT_RX_HZ, DEFAULT_RX_HZ, E_CXCSS_NONE, DEFAULT_SQ, E_CXCSS_NONE);
                    rssiFails = 0; logf("radio back");
                }
            }
        }
    }
    if (now - lastStatus >= 1000) { lastStatus = now; sendStatus(); }
    if (now - lastOled >= 250)  { lastOled = now; drawOled(); updateLed(sql); }
    delay(1);
}
