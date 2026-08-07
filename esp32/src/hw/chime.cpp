#include "hw/chime.h"
#include "board_pins.h"
#include "hw/i2c_lock.h"

#include <Wire.h>
#include <driver/i2s.h>
#include <math.h>

// Real speaker path: I2S tones — through the ES8311 codec where the board
// has one, straight into a bare I2S amp otherwise. Haptic via DRV2605 when
// present. Cue pitches match the BB10 / Android chime family.
#if HAS_ES8311
#include "AudioBoard.h"
#endif

namespace chime {
namespace {

constexpr int kRate = 44100;
constexpr float G4 = 392.00f;
constexpr float C5 = 523.25f;
constexpr float E5 = 659.25f;
constexpr float G5 = 783.99f;

struct CueMsg {
  Cue cue;
  bool sound;
  bool haptic;
};
QueueHandle_t g_queue = nullptr;

bool g_sound = true;
bool g_haptic = true;
uint8_t g_volume = 50;
bool g_audioReady = false;
bool g_audioTried = false;

#if HAS_ES8311
audio_driver::DriverPins g_pins;
audio_driver::AudioBoard g_board(audio_driver::AudioDriverES8311, g_pins);
#endif

void amp(bool on) {
#if !HAS_XL9555
  (void)on;  // no amp-enable rail on this board
#else
  I2cLock lock;
  // Read-modify-write AMP_EN on XL9555 output port 0.
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(0x02);
  if (Wire.endTransmission(false) != 0) return;
  if (Wire.requestFrom((int)XL9555_ADDR, 1) != 1) return;
  uint8_t p0 = Wire.read();
  if (on) {
    p0 |= (1u << EXP_AMP_EN);
  } else {
    p0 &= ~(1u << EXP_AMP_EN);
  }
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(0x02);
  Wire.write(p0);
  Wire.endTransmission();
#endif
}

bool audioInit() {
  if (g_audioTried) return g_audioReady;
  g_audioTried = true;

#if HAS_ES8311
  I2cLock lock;  // codec bring-up talks I2C
  g_pins.addI2C(audio_driver::PinFunction::CODEC, PIN_I2C_SCL, PIN_I2C_SDA);
  g_pins.addI2S(audio_driver::PinFunction::CODEC, PIN_I2S_MCLK, PIN_I2S_BCK,
                PIN_I2S_WS, PIN_I2S_DOUT, PIN_I2S_DIN);

  audio_driver::CodecConfig cfg;
  cfg.input_device = audio_driver::ADC_INPUT_NONE;
  cfg.output_device = audio_driver::DAC_OUTPUT_ALL;
  cfg.i2s.bits = audio_driver::BIT_LENGTH_16BITS;
  cfg.i2s.rate = audio_driver::RATE_44K;
  if (!g_board.begin(cfg)) {
    Serial.println("[chime] ES8311 init failed — haptic only");
    return false;
  }
  g_board.setVolume(g_volume);
#endif

  i2s_config_t i2scfg = {};
  i2scfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  i2scfg.sample_rate = kRate;
  i2scfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  i2scfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  i2scfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  i2scfg.intr_alloc_flags = 0;
  i2scfg.dma_buf_count = 4;
  i2scfg.dma_buf_len = 256;
  i2scfg.use_apll = true;
  i2scfg.tx_desc_auto_clear = true;
  i2scfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
  if (i2s_driver_install(I2S_NUM_0, &i2scfg, 0, NULL) != ESP_OK) {
    Serial.println("[chime] i2s install failed");
    return false;
  }
  i2s_pin_config_t pins = {};
#ifdef PIN_I2S_MCLK
  pins.mck_io_num = PIN_I2S_MCLK;
#else
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
#endif
  pins.bck_io_num = PIN_I2S_BCK;
  pins.ws_io_num = PIN_I2S_WS;
  pins.data_out_num = PIN_I2S_DOUT;
  pins.data_in_num = I2S_PIN_NO_CHANGE;
  i2s_set_pin(I2S_NUM_0, &pins);

  g_audioReady = true;
  Serial.println("[chime] ES8311 + I2S ready");
  return true;
}

// One tone with a short attack/decay envelope so it clicks less.
void tone(float freq, int ms, float gain = 0.5f) {
  const int total = kRate * ms / 1000;
  const int ramp = kRate * 6 / 1000;  // 6 ms fade in/out
  static int16_t buf[512];            // 256 stereo frames
  int written = 0;
  float phase = 0, step = 2.0f * PI * freq / kRate;
  while (written < total) {
    int frames = min(256, total - written);
    for (int i = 0; i < frames; i++) {
      int n = written + i;
      float env = 1.0f;
      if (n < ramp) env = (float)n / ramp;
      int left = total - n;
      if (left < ramp) env = (float)left / ramp;
#if !HAS_ES8311
      // No codec volume control — scale the samples instead.
      float vgain = gain * (g_volume / 100.0f);
#else
      float vgain = gain;
#endif
      int16_t s = (int16_t)(sinf(phase) * 32767.0f * vgain * env);
      phase += step;
      buf[i * 2] = s;
      buf[i * 2 + 1] = s;
    }
    size_t done = 0;
    i2s_write(I2S_NUM_0, buf, (size_t)frames * 4, &done, portMAX_DELAY);
    written += frames;
  }
}

void gap(int ms) {
  static const int16_t zeros[128] = {0};
  int total = kRate * ms / 1000;
  while (total > 0) {
    size_t done = 0;
    int frames = min(64, total);
    i2s_write(I2S_NUM_0, zeros, (size_t)frames * 4, &done, portMAX_DELAY);
    total -= frames;
  }
}

// Sequences copied from flipper-claude-buddy notifications.c:
//   alert (status)      = E5 50 ms
//   success (done)      = C5-E5-G5, 100 ms each, legato
//   error               = G4 100, 50 off, G4 100
//   permission (attn)   = C5 100, E5 100, legato
void beepSeries(Cue cue) {
  switch (cue) {
    case Cue::Status:
      // A plain, unmissable beep — short blips vanish in the amp ramp.
      tone(E5, 160);
      break;
    case Cue::Done:
      tone(C5, 100);
      tone(E5, 100);
      tone(G5, 100);
      break;
    case Cue::Error:
      tone(G4, 100);
      gap(50);
      tone(G4, 100);
      break;
    case Cue::Attention:
      // Urgent ring, clearly distinct from the single-beep Status cue.
      tone(E5, 80);
      tone(G5, 80);
      tone(E5, 80);
      tone(G5, 170);
      break;
    default:
      break;
  }
  i2s_zero_dma_buffer(I2S_NUM_0);
}

bool g_drvReady = false;

// DRV2605 needs real initialization before effects play at full strength —
// poking GO on an unconfigured chip gave the "no vibration" symptom.
// Sequence mirrors LilyGoLib initDrv: library A, internal trigger, ERM
// open-loop (the pager's motor is ERM).
void drvInit() {
  const uint8_t addr = 0x5A;
  auto wr = [&](uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
  };
  auto rd = [&](uint8_t reg) -> uint8_t {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return 0;
    if (Wire.requestFrom((int)addr, 1) != 1) return 0;
    return Wire.read();
  };
  wr(0x01, 0x00);  // MODE: exit standby, internal trigger
  wr(0x02, 0x00);  // RTP input 0
  wr(0x0D, 0x00);  // overdrive
  wr(0x0E, 0x00);  // sustain pos
  wr(0x0F, 0x00);  // sustain neg
  wr(0x10, 0x00);  // brake
  wr(0x13, 0x64);  // audio-to-vibe max input
  wr(0x03, 0x01);  // effect library A (ERM)
  wr(0x1A, rd(0x1A) & 0x7F);  // FEEDBACK: ERM mode
  wr(0x1D, rd(0x1D) | 0x20);  // CONTROL3: ERM open loop
  g_drvReady = true;
  Serial.println("[chime] DRV2605 ready (ERM)");
}

void hapticPulse(Cue cue) {
  I2cLock lock;
  if (!g_drvReady) drvInit();
  const uint8_t addr = 0x5A;
  auto wr = [&](uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
  };
  uint8_t effect = 1;                     // strong click 100%
  if (cue == Cue::Done) effect = 10;      // double click
  if (cue == Cue::Error) effect = 12;     // triple click
  if (cue == Cue::Attention) effect = 15; // 750 ms alert buzz
  if (cue == Cue::Tick) effect = 26;      // sharp tick, subtle
  wr(0x01, 0x00);  // wake (chip may have re-entered standby)
  wr(0x04, effect);
  wr(0x05, 0x00);  // end of sequence
  wr(0x0C, 0x01);  // GO
}

}  // namespace

namespace {
void chimeTask(void *);  // defined below; anonymous namespaces merge
}

void begin() {
  // Codec init is lazy: the first audible cue pays it, on the chime task.
  g_queue = xQueueCreate(8, sizeof(CueMsg));
  xTaskCreate(chimeTask, "chime", 6144, NULL, 1, NULL);
  Serial.println("[chime] task ready (codec lazy-init)");
}

void setEnabled(bool sound, bool haptic) {
  g_sound = sound;
  g_haptic = haptic;
}

void setVolume(uint8_t pct) {
  g_volume = pct > 100 ? 100 : pct;
#if HAS_ES8311
  if (g_audioReady) g_board.setVolume(g_volume);
#endif
}

namespace {

// The full cue (amp pre-roll + tones) takes ~0.5 s — far too long for the
// loop task, where it froze LVGL on every event. Runs here instead.
void playSync(const CueMsg &m) {
  if (m.haptic && g_haptic) {
    hapticPulse(m.cue);
  }
  if (m.cue == Cue::Tick) return;  // haptic-only, never beeps
  if (m.sound && g_sound && g_volume > 0) {
    if (!audioInit()) return;
    amp(true);
    // Prime with silence: the amp's soft-start eats ~170 ms of audio after
    // enable (field-tested: 70 ms still lost the first note of the Done
    // triad). The pre-roll trades a slight delay for complete first notes.
    gap(180);
    beepSeries(m.cue);
    gap(15);
    amp(false);
  }
}

void chimeTask(void *) {
  CueMsg m;
  for (;;) {
    if (xQueueReceive(g_queue, &m, portMAX_DELAY) == pdTRUE) playSync(m);
  }
}

}  // namespace

void play(Cue cue, bool sound, bool haptic) {
  if (!g_queue) return;
  CueMsg m{cue, sound, haptic};
  xQueueSend(g_queue, &m, 0);  // full queue: drop rather than block the UI
}

}  // namespace chime
