#pragma once
#include "app_config.h"
#include "net/agentapi.h"

#include <vector>

namespace ui {

enum class Screen : uint8_t {
  Boot,        // first-run only
  Beeper,      // home: live board of active sessions
  Menu,        // TrailMate-style icon grid
  Sessions,
  Compose,
  Status,
  WifiScan,    // pick network from scan results
  WifiManual,  // type hidden SSID
  WifiPass,    // password for pending network
  SetupDaemon,
  Power,       // shutdown / restart
};

void begin(AppConfig *cfg);
void setScreen(Screen s);
Screen screen();
void draw();
// Handle one keyboard event; returns true if consumed. `back` replaces the
// old Esc handling — this hardware has no Esc key (side button / serial ESC).
bool onKey(char ch, bool enter, bool backspace, bool back);
// Rotary detents: negative = up, positive = down.
void onRotary(int delta);
void onTick();  // periodic refresh + status feed diffing
void markActivity();
uint32_t lastActivityMs();

}  // namespace ui
