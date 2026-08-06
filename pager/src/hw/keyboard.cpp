#include "hw/keyboard.h"
#include "board_pins.h"

#include <Adafruit_TCA8418.h>
#include <Wire.h>

// TCA8418 matrix map for T-LoRa Pager QWERTY is board-specific.
// We ship a compact ASCII map; CAPS/SYM modifiers handled lightly.
// If keys are wrong on your unit, adjust kMap[] from LILYGO examples.

namespace keyboard {
namespace {

Adafruit_TCA8418 kbd;
bool ready = false;
bool caps = false;
bool sym = false;

// Row-major simplified 4×10-ish map (placeholder indices 0–39).
// Real map should match LILYGO factory firmware; this gets basic input working.
const char kLower[] =
    "qwertyuiop"
    "asdfghjkl\n"
    "zxcvbnm,.?"
    "  \b\x1b  ";  // space, backspace, esc fillers
const char kUpper[] =
    "QWERTYUIOP"
    "ASDFGHJKL\n"
    "ZXCVBNM<>/"
    "  \b\x1b  ";
const char kSym[] =
    "1234567890"
    "!@#$%^&*()\n"
    "-_=+[]{};:"
    "  \b\x1b  ";

char mapKey(uint8_t row, uint8_t col) {
  int idx = (int)row * 10 + (int)col;
  const char *table = sym ? kSym : (caps ? kUpper : kLower);
  int n = (int)strlen(table);
  if (idx < 0 || idx >= n) return 0;
  return table[idx];
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
  // 8x10 matrix typical for this board; adjust if needed.
  kbd.matrix(7, 10);
  kbd.enableInterrupts();
  Serial.println("[kbd] TCA8418 ready");
}

void tick() {
  // INT pin drained in poll.
}

bool poll(Event *out) {
  if (!out) return false;
  memset(out, 0, sizeof(*out));

  // Serial fallback for desk testing without keyboard.
  if (Serial.available()) {
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
  }

  if (!ready) return false;

  // Drain FIFO
  int ev = kbd.getEvent();
  if (ev <= 0) return false;
  // Bit 7: 1 = press, 0 = release. We only handle presses.
  bool press = (ev & 0x80) != 0;
  if (!press) return poll(out);  // skip releases, try next

  int key = ev & 0x7F;
  // TCA8418 key numbers are 1-based row/col encoding: key = row*10 + col + 1
  int key0 = key - 1;
  int row = key0 / 10;
  int col = key0 % 10;

  // Hardware modifier keys (approx positions — CAPS / SYM)
  if (row == 3 && col == 0) {
    caps = !caps;
    out->function = true;
    return true;
  }
  if (row == 3 && col == 1) {
    sym = !sym;
    out->function = true;
    return true;
  }

  char ch = mapKey((uint8_t)row, (uint8_t)col);
  out->code = (uint8_t)key;
  if (ch == '\n') {
    out->enter = true;
    return true;
  }
  if (ch == '\b') {
    out->backspace = true;
    return true;
  }
  if (ch == 0x1b) {
    out->escape = true;
    return true;
  }
  if (ch == '\t') {
    out->tab = true;
    return true;
  }
  if (ch >= 32) {
    out->ch = ch;
    // One-shot sym mode like phone keyboards
    if (sym) sym = false;
    return true;
  }
  return false;
}

}  // namespace keyboard
