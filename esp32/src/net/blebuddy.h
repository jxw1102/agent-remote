#pragma once
// claude-desktop-buddy BLE bridge: Nordic UART Service peripheral speaking
// the documented wire protocol (newline-delimited JSON). The Claude desktop
// app pushes heartbeat snapshots and permission prompts; we can answer
// permission decisions from the pager. Works with no Wi-Fi / daemon at all.

#include <Arduino.h>

namespace blebuddy {

struct Snap {
  int total = 0;
  int running = 0;
  int waiting = 0;
  String msg;
  String entries[3];
  int entryCount = 0;
  uint32_t tokensToday = 0;
  bool hasPrompt = false;
  String promptId;
  String promptTool;
  String promptHint;
};

void begin();  // init BLE + advertise "Claude Pager xxxx"
void stop();   // stop advertising / disconnect
void tick();   // parse received lines (call from loop)

bool active();
bool connected();
const String &owner();
uint32_t generation();
const Snap &snap();

// {"cmd":"permission","id":…,"decision":"once"|"deny"}
void sendPermission(const String &id, bool allow);

}  // namespace blebuddy
