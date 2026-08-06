#pragma once
#include "app_config.h"
#include "net/agentapi.h"

#include <vector>

namespace ui {

enum class Screen : uint8_t {
  Boot,
  SetupWifi,
  SetupDaemon,
  Home,
  Sessions,
  Compose,
  Status,
};

void begin(AppConfig *cfg);
void setScreen(Screen s);
Screen screen();
void draw();
// Handle one keyboard event; returns true if consumed.
bool onKey(char ch, bool enter, bool backspace, bool escape);
void onTick();  // periodic refresh
void markActivity();
uint32_t lastActivityMs();

}  // namespace ui
