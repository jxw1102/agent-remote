#pragma once
#include <Arduino.h>

namespace power {

struct Status {
  int percent;      // 0–100, -1 unknown
  bool charging;
  bool usb;
  float voltage;    // V
};

void begin();
Status read();
// Dim backlight then deep sleep. Wake on BOOT or keyboard INT.
void deepSleepSeconds(uint32_t sec);
// Soft idle tick — call from main loop with last activity ms.
void idleTick(uint32_t lastActivityMs, uint8_t idleSleepMin, uint8_t backlight);

}  // namespace power
