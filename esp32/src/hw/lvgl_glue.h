#pragma once
// LVGL ↔ hardware glue: LovyanGFX flush, rotary encoder indev, TCA8418
// keypad indev, side-button back events. TrailMate-style stack (LVGL 9).

#include <Arduino.h>

namespace lvgl_glue {

// Call after display::begin(). Sets up lv display, buffers, indevs.
void begin();
// Pump input + lv_timer_handler. Call every loop.
void loop();
// Group the encoder + keypad act on (LVGL focus navigation).
void setGroup(void *lv_group);
// Fired when the side (BOOT) button is pressed — screen-level "back".
void onBack(void (*cb)());
// ms since the user last touched knob/keys (for backlight dimming).
uint32_t inactiveMs();
// Count an event (e.g. a chime) as activity so the screen lights up.
void pokeActivity();
// Screen-specific knob rotation (e.g. scrolling the Live TUI). Return true
// to consume the detents; nullptr restores normal focus navigation.
void setRotaryHandler(bool (*cb)(int delta));

}  // namespace lvgl_glue
