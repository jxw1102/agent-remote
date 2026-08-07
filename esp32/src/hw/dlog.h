#pragma once
// Diagnostic ring log: everything goes to Serial AND a ring buffer that the
// Diag screen can show and upload to the daemon (POST /api/attachments) so
// device-side failures can be debugged from a log file.

#include <Arduino.h>

namespace dlog {

void begin();  // hooks esp_log too (WiFi stack messages land in the ring)
void logf(const char *fmt, ...);
// Full ring contents, oldest first.
String dump();

}  // namespace dlog
