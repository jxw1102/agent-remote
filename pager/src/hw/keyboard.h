#pragma once
#include <Arduino.h>

namespace keyboard {

struct Event {
  char ch;       // printable, or 0
  uint8_t code;  // raw
  bool enter;
  bool backspace;
  bool escape;
  bool tab;
  bool function; // F-keys / special
};

void begin();
// Non-blocking; returns true if an event was produced.
bool poll(Event *out);
// Call regularly so INT is drained.
void tick();

}  // namespace keyboard
