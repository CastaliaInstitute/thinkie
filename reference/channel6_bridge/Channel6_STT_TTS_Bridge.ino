/**
 * Channel 6 USB audio bridge for LILYGO T-TWR Plus UHF.
 *
 * Serial protocol at 921600 baud:
 *   - Board sends RX audio frames as: "AUD <n>\n" followed by n unsigned 8-bit samples.
 *   - Host sends TX audio as: "TX <n>\n" followed by n unsigned 8-bit samples.
 *   - Host may send "RX\n" to force receive mode.
 *
 * This firmware only bridges narrowband analog audio. Run STT/TTS on the host.
 */
#include "LilyGo_TWR.h"

static constexpr uint32_t CHANNEL6_HZ = 462687500UL;
static constexpr uint32_t SAMPLE_RATE_HZ = 8000;
static constexpr uint32_t SERIAL_BAUD = 921600;
static constexpr uint8_t AUDIO_PWM_CHANNEL = 0;
static constexpr uint32_t AUDIO_PWM_HZ = 62500;
static constexpr uint8_t AUDIO_PWM_BITS = 8;
static constexpr size_t AUDIO_FRAME_SAMPLES = 160;

static uint8_t rxFrame[AUDIO_FRAME_SAMPLES];

static bool initRadio()
{
    bool ok = false;

    twr.begin();

    if (twr.getVersion() == TWRClass::TWR_REV2V1) {
        radio.setPins(SA868_PTT_PIN, SA868_PD_PIN);
        ok = radio.begin(RadioSerial, twr.getBandDefinition());
    } else {
        radio.setPins(SA868_PTT_PIN, SA868_PD_PIN, SA868_RF_PIN);
        ok = radio.begin(RadioSerial, SA8X8_UHF);
    }

    if (!ok) {
        return false;
    }

    radio.setBandWidth(12500);
    radio.lowPower();
    radio.setSquelchLevel(4);
    radio.setTxCXCSS(E_CXCSS_NONE);
    radio.setRxCXCSS(E_CXCSS_NONE);
    radio.setTxFreq(CHANNEL6_HZ);
    radio.setRxFreq(CHANNEL6_HZ);
    radio.setVolume(5);
    radio.receive();

    return true;
}

static void setupAudioRouting()
{
    twr.routingMicrophoneChannel(TWRClass::TWR_MIC_TO_ESP);
    twr.routingSpeakerChannel(TWRClass::TWR_RADIO_TO_SPK);

    analogReadResolution(8);
    analogSetPinAttenuation(SA8682ESP_AUDIO, ADC_11db);

    ledcSetup(AUDIO_PWM_CHANNEL, AUDIO_PWM_HZ, AUDIO_PWM_BITS);
    ledcAttachPin(ESP2SA868_MIC, AUDIO_PWM_CHANNEL);
    ledcWrite(AUDIO_PWM_CHANNEL, 128);
}

static void sendRxAudioFrame()
{
    static uint32_t nextSampleAt = micros();

    for (size_t i = 0; i < AUDIO_FRAME_SAMPLES; ++i) {
        while ((int32_t)(micros() - nextSampleAt) < 0) {
            delayMicroseconds(10);
        }
        nextSampleAt += 1000000UL / SAMPLE_RATE_HZ;
        rxFrame[i] = analogRead(SA8682ESP_AUDIO);
    }

    Serial.printf("AUD %u\n", static_cast<unsigned>(AUDIO_FRAME_SAMPLES));
    Serial.write(rxFrame, AUDIO_FRAME_SAMPLES);
}

static int readLine(char *buf, size_t len)
{
    size_t used = 0;
    const uint32_t deadline = millis() + 1000;

    while (millis() < deadline && used + 1 < len) {
        if (!Serial.available()) {
            delay(1);
            continue;
        }
        char ch = static_cast<char>(Serial.read());
        if (ch == '\n') {
            buf[used] = '\0';
            return used;
        }
        if (ch != '\r') {
            buf[used++] = ch;
        }
    }

    buf[used] = '\0';
    return used;
}

static void transmitPcm(size_t bytes)
{
    const uint32_t samplePeriodUs = 1000000UL / SAMPLE_RATE_HZ;

    twr.routingMicrophoneChannel(TWRClass::TWR_MIC_TO_ESP);
    radio.transmit();
    delay(120);

    uint32_t nextSampleAt = micros();
    for (size_t i = 0; i < bytes; ++i) {
        while (!Serial.available()) {
            delayMicroseconds(50);
        }
        const uint8_t sample = Serial.read();

        while ((int32_t)(micros() - nextSampleAt) < 0) {
            delayMicroseconds(10);
        }
        nextSampleAt += samplePeriodUs;
        ledcWrite(AUDIO_PWM_CHANNEL, sample);
    }

    ledcWrite(AUDIO_PWM_CHANNEL, 128);
    delay(80);
    radio.receive();
}

static void handleHostCommand()
{
    if (!Serial.available()) {
        return;
    }

    char line[32];
    int n = readLine(line, sizeof(line));
    if (n <= 0) {
        return;
    }

    if (strcmp(line, "RX") == 0) {
        radio.receive();
        return;
    }

    unsigned txBytes = 0;
    if (sscanf(line, "TX %u", &txBytes) == 1 && txBytes > 0) {
        transmitPcm(txBytes);
    }
}

void setup()
{
    Serial.begin(SERIAL_BAUD);
    delay(1500);

    if (!initRadio()) {
        while (true) {
            Serial.println("ERR SA868 offline");
            delay(1000);
        }
    }

    setupAudioRouting();
    Serial.println("READY FRS/GMRS_CH6 462.6875MHz BW=12.5kHz CTCSS=none");
}

void loop()
{
    handleHostCommand();

    if (!radio.isTransmit()) {
        sendRxAudioFrame();
    }
}
