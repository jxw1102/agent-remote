#include "hw/power.h"
#include "hw/display.h"
#include "board_pins.h"

#include <Wire.h>
#include <esp_sleep.h>

// Optional BQ25896 registers (7-bit addr 0x6B). Soft-fail if absent.
#ifndef BQ25896_ADDR
#define BQ25896_ADDR 0x6B
#endif

namespace power {
namespace {

uint8_t dimmed = 0;
bool i2c_ok = false;

bool i2cRead(uint8_t addr, uint8_t reg, uint8_t *val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)addr, 1) != 1) return false;
  *val = Wire.read();
  return true;
}

}  // namespace

void begin() {
  // Probe charger presence
  uint8_t id = 0;
  i2c_ok = i2cRead(BQ25896_ADDR, 0x14, &id);  // PART_NUMBER / revision region
  Serial.printf("[power] BQ25896 %s\n", i2c_ok ? "present" : "absent (ok)");
  // Wake on BOOT (active low). Keyboard INT can be added later.
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_BOOT, 0);
}

Status read() {
  Status s{};
  s.percent = -1;
  s.voltage = 0;
  s.charging = false;
  s.usb = false;
  // Without a dedicated ADC pin mapping here, report unknown %.
  // When BQ25896 is present, REG0E bits carry rough VBUS state on some revs.
  if (i2c_ok) {
    uint8_t vbus = 0;
    if (i2cRead(BQ25896_ADDR, 0x11, &vbus)) {
      s.usb = (vbus & 0xE0) != 0;
      s.charging = s.usb;
    }
    // Placeholder mid battery so UI shows something when charging.
    s.percent = s.usb ? 85 : 50;
    s.voltage = s.usb ? 5.0f : 3.8f;
  }
  return s;
}

void deepSleepSeconds(uint32_t sec) {
  display::setBacklight(0);
  Serial.printf("[power] deep sleep %u s\n", (unsigned)sec);
  Serial.flush();
  if (sec > 0) esp_sleep_enable_timer_wakeup((uint64_t)sec * 1000000ULL);
  esp_deep_sleep_start();
}

void idleTick(uint32_t lastActivityMs, uint8_t idleSleepMin, uint8_t backlight) {
  if (idleSleepMin == 0) return;
  uint32_t idle = millis() - lastActivityMs;
  uint32_t dimAt = (uint32_t)idleSleepMin * 60UL * 1000UL / 2UL;
  uint32_t sleepAt = (uint32_t)idleSleepMin * 60UL * 1000UL;
  if (idle > sleepAt) {
    deepSleepSeconds(0);
  } else if (idle > dimAt) {
    if (!dimmed) {
      display::setBacklight((uint8_t)max(20, backlight / 4));
      dimmed = 1;
    }
  } else if (dimmed) {
    display::setBacklight(backlight);
    dimmed = 0;
  }
}

}  // namespace power
