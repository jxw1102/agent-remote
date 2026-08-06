#include "hw/chime.h"
#include "board_pins.h"

#include <Wire.h>
#include <math.h>

// Prefer LEDC tone on a safe path — full ES8311 codec init is complex and
// I2S without codec clocks can hang some units at boot. Haptic is primary
// "feel"; audio is best-effort via ledc if SPEAKER path exists later.

namespace chime {
namespace {

constexpr int kRate = 22050;
constexpr float G4 = 392.00f;
constexpr float C5 = 523.25f;
constexpr float E5 = 659.25f;
constexpr float G5 = 783.99f;

bool g_sound = true;
bool g_haptic = true;

void amp(bool on) {
  // Read-modify-write AMP_EN on output port 0 so the rail bits set by
  // board_init stay untouched.
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
}

void hapticPulse(Cue cue) {
  const uint8_t addr = 0x5A;
  auto wr = [&](uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
  };
  uint8_t effect = 1;
  if (cue == Cue::Done) effect = 10;
  if (cue == Cue::Error) effect = 12;
  if (cue == Cue::Attention) effect = 14;
  if (cue == Cue::Tick) effect = 26;  // sharp tick, subtle
  wr(0x01, 0x00);
  wr(0x04, effect);
  wr(0x0C, 0x01);
}

// Simple blocking square via delayMicroseconds on KB backlight pin is wrong.
// Use ledc on a dedicated channel if we had a buzzer GPIO — for now haptic
// + serial log is safe. Optional: PWM on I2S DOUT is not a speaker.

void beepSeries(Cue cue) {
  // LEDC soft beeps on pin that is often unused for radio CS high —
  // Use PIN_KB_BL briefly as crude click only if no DRV2605 (last resort).
  // Prefer haptic; skip electrical speaker path until ES8311 driver lands.
  (void)cue;
  Serial.printf("[chime] cue %d\n", (int)cue);
}

}  // namespace

void begin() {
  // Do NOT install I2S at boot — that was a hang risk without ES8311 setup.
  Serial.println("[chime] haptic-first (I2S deferred)");
}

void setEnabled(bool sound, bool haptic) {
  g_sound = sound;
  g_haptic = haptic;
}

void play(Cue cue, bool sound, bool haptic) {
  if (haptic && g_haptic) {
    hapticPulse(cue);
  }
  if (cue == Cue::Tick) return;  // haptic-only, never beeps
  if (sound && g_sound) {
    amp(true);
    beepSeries(cue);
    // short amp pulse so hardware path is exercised
    delay(30);
    amp(false);
  }
}

}  // namespace chime
