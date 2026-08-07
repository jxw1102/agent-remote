#pragma once
// Wall-clock for the header: NTP over Wi-Fi, or the Claude desktop's BLE
// time-sync when there is no network. System clock stays UTC; the display
// offset is applied only when formatting.

#include <Arduino.h>

namespace timesync {

// Start SNTP (idempotent). Call once Wi-Fi is connected.
void beginNtp();
// {"time":[epoch, tz_offset_s]} from the desktop-buddy BLE bridge.
void setFromBle(uint32_t epochUtc, int32_t offsetSec);
bool valid();
// Local HH:MM using the configured offset (default UTC+8; BLE overrides).
void nowLocal(int *hourOut, int *minOut);

}  // namespace timesync
