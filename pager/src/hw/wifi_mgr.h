#pragma once
#include <Arduino.h>

namespace wifi_mgr {

enum class State : uint8_t { Off, Connecting, Connected, Failed };

void begin();
// Non-blocking connect attempt; call tick() in loop.
void connect(const String &ssid, const String &pass);
void tick();
void disconnect();
State state();
String ip();
int rssi();
const char *stateName();

}  // namespace wifi_mgr
