#include "hw/rotary.h"
#include "board_pins.h"

// Navigation input behind one API: rotary encoder + side button on the
// T-LoRa Pager, trackball pulses on the T-Deck.

namespace rotary {
namespace {

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

}  // namespace

#if HAS_ROTARY

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
bool buttonDown() { return digitalRead(PIN_ROT_BTN) == LOW; }

#elif HAS_TRACKBALL

namespace {

// Each trackball roll emits pulses on the axis pin; count them as detents.
volatile int steps = 0;
volatile int leftPulses = 0;

void IRAM_ATTR isrUp() { steps--; }
void IRAM_ATTR isrDown() { steps++; }
void IRAM_ATTR isrLeft() { leftPulses++; }

Debounced btnSel{PIN_TB_CLICK};
uint32_t lastBackAt = 0;

}  // namespace

void begin() {
  pinMode(PIN_TB_UP, INPUT_PULLUP);
  pinMode(PIN_TB_DOWN, INPUT_PULLUP);
  pinMode(PIN_TB_LEFT, INPUT_PULLUP);
  pinMode(PIN_TB_RIGHT, INPUT_PULLUP);
  pinMode(PIN_TB_CLICK, INPUT_PULLUP);
  attachInterrupt(PIN_TB_UP, isrUp, FALLING);
  attachInterrupt(PIN_TB_DOWN, isrDown, FALLING);
  attachInterrupt(PIN_TB_LEFT, isrLeft, FALLING);
  Serial.println("[trackball] ready");
}

int readDelta() {
  noInterrupts();
  int d = steps;
  steps = 0;
  interrupts();
  return d;
}

bool pressed() { return btnSel.poll(); }

// Rolling left = back (the T-Deck has no spare button for it).
bool backPressed() {
  if (leftPulses > 0 && millis() - lastBackAt > 250) {
    lastBackAt = millis();
    noInterrupts();
    leftPulses = 0;
    interrupts();
    return true;
  }
  return false;
}

bool buttonDown() { return digitalRead(PIN_TB_CLICK) == LOW; }

#endif

}  // namespace rotary
