#include "hw/keyboard.h"
#include "board_pins.h"
#include "hw/i2c_lock.h"

#include <Wire.h>

#if HAS_TCA8418
#include <Adafruit_TCA8418.h>
#endif

namespace keyboard {
namespace {

// Serial console fallback shared by every board (desk testing).
bool serialPoll(Event *out) {
  if (!Serial.available()) return false;
  int c = Serial.read();
  if (c == '\r' || c == '\n') {
    out->enter = true;
    return true;
  }
  if (c == 0x7f || c == 0x08) {
    out->backspace = true;
    return true;
  }
  if (c == 0x1b) {
    out->escape = true;
    return true;
  }
  if (c >= 32 && c < 127) {
    out->ch = (char)c;
    return true;
  }
  return false;
}

}  // namespace
}  // namespace keyboard

#if HAS_TCA8418

// TCA8418 matrix map from LilyGoLib LilyGo_LoRa_Pager.cpp (factory layout).
// Event code = row*10 + col + 1; 4 rows x 10 cols.
//   row2 col0 = Alt (the orange triangle = Sym/number layer)
//   row2 col8 = Caps   row2 col9 = Backspace
//   row3 col0..9 = space bar contacts (the big oval hits col0!)
// There is no Esc key on this hardware — "back" comes from the side button /
// rotary, handled in ui.

namespace keyboard {
namespace {

Adafruit_TCA8418 kbd;
bool ready = false;
// BlackBerry-style modifiers: hold = active while held, tap = one-shot latch
// for the next key, tap again = cancel.
bool caps = false, capsDown = false, capsUsed = false;
bool sym = false, symDown = false, symUsed = false;

constexpr uint8_t kRows = 4;
constexpr uint8_t kCols = 10;
// Special key indices (after the -1 adjust): k = row*10 + col
constexpr uint8_t kKeyAlt = 20;   // row2 col0
constexpr uint8_t kKeyCaps = 28;  // row2 col8
constexpr uint8_t kKeyBksp = 29;  // row2 col9

const char kLower[kRows][kCols] = {
    {'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'},
    {'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', '\n'},
    {0, 'z', 'x', 'c', 'v', 'b', 'n', 'm', 0, 0},
    {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '},
};
const char kSymMap[kRows][kCols] = {
    {'1', '2', '3', '4', '5', '6', '7', '8', '9', '0'},
    {'*', '/', '+', '-', '=', ':', '\'', '"', '@', '\n'},
    {0, '_', '$', ';', '?', '!', ',', '.', 0, 0},
    {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '},
};

char mapKey(uint8_t row, uint8_t col) {
  if (row >= kRows || col >= kCols) return 0;
  char c = sym ? kSymMap[row][col] : kLower[row][col];
  if (!sym && caps && c) c = (char)toupper(c);
  return c;
}

}  // namespace

void begin() {
  Wire.begin(PIN_KB_SDA, PIN_KB_SCL);
  pinMode(PIN_KB_INT, INPUT_PULLUP);
  pinMode(PIN_KB_BL, OUTPUT);
  digitalWrite(PIN_KB_BL, HIGH);

  ready = kbd.begin(TCA8418_ADDR, &Wire);
  if (!ready) {
    Serial.println("[kbd] TCA8418 not found — serial console fallback");
    return;
  }
  kbd.matrix(kRows, kCols);
  kbd.flush();
  kbd.enableInterrupts();
  Serial.println("[kbd] TCA8418 ready (4x10)");
}

bool capsOn() { return caps; }
bool symOn() { return sym; }

void tick() {
  // INT pin drained in poll.
}

bool poll(Event *out) {
  if (!out) return false;
  memset(out, 0, sizeof(*out));
  if (serialPoll(out)) return true;
  if (!ready) return false;

  // Drain FIFO
  int ev;
  {
    I2cLock lock;
    ev = kbd.getEvent();
  }
  if (ev <= 0) return false;
  bool press = (ev & 0x80) != 0;  // bit 7: 1 = press, 0 = release
  int k = (ev & 0x7F) - 1;
  if (k < 0 || k >= kRows * 10) return press ? false : poll(out);
  int row = k / 10;
  int col = k % 10;

  // Modifier releases end hold-chording (if a key was typed while held).
  if (!press) {
    if (k == kKeyAlt) {
      symDown = false;
      if (symUsed) { sym = false; symUsed = false; }
    } else if (k == kKeyCaps) {
      capsDown = false;
      if (capsUsed) { caps = false; capsUsed = false; }
    }
    return poll(out);  // releases produce no event; try next
  }

  // Orange triangle: hold + letter = number/symbol (BB style); tap latches
  // the layer for one key; tap again cancels.
  if (k == kKeyAlt) {
    symDown = true;
    symUsed = false;
    sym = !sym;
    out->function = true;
    return true;
  }
  if (k == kKeyCaps) {
    capsDown = true;
    capsUsed = false;
    caps = !caps;
    out->function = true;
    return true;
  }
  if (k == kKeyBksp) {
    out->backspace = true;
    return true;
  }

  char ch = mapKey((uint8_t)row, (uint8_t)col);
  out->code = (uint8_t)(k + 1);
  if (ch == '\n') {
    out->enter = true;
    return true;
  }
  if (ch >= 32) {
    out->ch = ch;
    if (sym) {
      if (symDown) symUsed = true;  // held: stay in layer until release
      else sym = false;             // tapped: one-shot consumed
    }
    if (caps) {
      if (capsDown) capsUsed = true;
      else caps = false;  // tapped caps acts as one-shot shift
    }
    return true;
  }
  Serial.printf("[kbd] unmapped key %d (r%d c%d)\n", k + 1, row, col);
  return false;
}

}  // namespace keyboard

#elif HAS_TDECK_KEYBOARD

// T-Deck: an ESP32-C3 co-processor scans the BB-style keyboard and serves
// one ASCII byte per I2C read (0 = none). Modifiers are handled on the
// co-processor, so there is no caps/sym state to track here.
namespace keyboard {
namespace {

bool ready = false;

}  // namespace

void begin() {
  Wire.beginTransmission(TDECK_KB_ADDR);
  ready = Wire.endTransmission() == 0;
  Serial.println(ready ? "[kbd] T-Deck keyboard ready"
                       : "[kbd] T-Deck keyboard not found — serial fallback");
}

bool capsOn() { return false; }
bool symOn() { return false; }
void tick() {}

bool poll(Event *out) {
  if (!out) return false;
  memset(out, 0, sizeof(*out));
  if (serialPoll(out)) return true;
  if (!ready) return false;
  int c;
  {
    I2cLock lock;
    if (Wire.requestFrom((int)TDECK_KB_ADDR, 1) != 1) return false;
    c = Wire.read();
  }
  if (c <= 0) return false;
  if (c == '\r' || c == '\n') {
    out->enter = true;
    return true;
  }
  if (c == 0x08 || c == 0x7f) {
    out->backspace = true;
    return true;
  }
  if (c >= 32 && c < 127) {
    out->ch = (char)c;
    return true;
  }
  return false;
}

}  // namespace keyboard

#endif
