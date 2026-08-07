#pragma once
#include "app_config.h"

#include <Arduino.h>

namespace ui {

// LVGL-based UI (TrailMate-style stack). Input flows through lvgl_glue
// indevs; this module owns screens, navigation, and the beeper controller
// (feed diffing, chimes, reminders).
void begin(AppConfig *cfg);
// Periodic work: status-feed diffing, reminder chimes, label refresh,
// fallback polling. Call every loop after lvgl_glue::loop().
void tick();
// Last user interaction (knob/keys), for backlight dimming.
uint32_t lastActivityMs();

}  // namespace ui
