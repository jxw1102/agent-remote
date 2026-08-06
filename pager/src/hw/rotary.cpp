#include "hw/rotary.h"
#include "board_pins.h"

namespace rotary {
namespace {

// Quadrature full-step decode: accumulate quarter steps via a Gray-code
// transition table; one detent = 4 valid quarter steps.
volatile int8_t qsum = 0;
volatile int steps = 0;
volatile uint8_t prevAB = 0;

const int8_t kQuad[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};

void IRAM_ATTR isrRot() {
  uint8_t ab = (uint8_t)((digitalRead(PIN_ROT_A) << 1) | digitalRead(PIN_ROT_B));
  int8_t d = kQuad[(prevAB << 2) | ab];
  prevAB = ab;
  if (!d) return;
  qsum += d;
  if (qsum >= 4) {
    steps++;
    qsum = 0;
  } else if (qsum <= -4) {
    steps--;
    qsum = 0;
  }
}

struct Debounced {
  uint8_t pin;
  bool lastLevel;   // pull-up, idle high
  uint32_t lastEdge;
  bool fired;

  explicit Debounced(uint8_t p)
      : pin(p), lastLevel(true), lastEdge(0), fired(false) {}

  bool poll() {
    bool level = digitalRead(pin);
    uint32_t now = millis();
    if (level != lastLevel) {
      lastLevel = level;
      lastEdge = now;
      fired = false;
    }
    if (!level && !fired && now - lastEdge > 30) {
      fired = true;  // stable low 30 ms → one press event
      return true;
    }
    return false;
  }
};

Debounced btnSel{PIN_ROT_BTN};
Debounced btnBack{PIN_BOOT};

}  // namespace

void begin() {
  pinMode(PIN_ROT_A, INPUT_PULLUP);
  pinMode(PIN_ROT_B, INPUT_PULLUP);
  pinMode(PIN_ROT_BTN, INPUT_PULLUP);
  pinMode(PIN_BOOT, INPUT_PULLUP);
  prevAB = (uint8_t)((digitalRead(PIN_ROT_A) << 1) | digitalRead(PIN_ROT_B));
  attachInterrupt(PIN_ROT_A, isrRot, CHANGE);
  attachInterrupt(PIN_ROT_B, isrRot, CHANGE);
  Serial.println("[rotary] ready");
}

int readDelta() {
  noInterrupts();
  int d = steps;
  steps = 0;
  interrupts();
  return d;
}

bool pressed() { return btnSel.poll(); }
bool backPressed() { return btnBack.poll(); }

}  // namespace rotary
