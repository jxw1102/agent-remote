#pragma once
#include <Arduino.h>

// Rotary encoder (GPIO 40/41, press on 7) + side BOOT button (GPIO 0).
// Rotation navigates, press selects, side button is "back" — this hardware
// has no Esc key.
namespace rotary {

void begin();
// Detents turned since last call: negative = up/left, positive = down/right.
int readDelta();
// Edge-triggered, debounced; true once per physical press.
bool pressed();      // rotary center press → select/enter
bool backPressed();  // side BOOT button → back

}  // namespace rotary
