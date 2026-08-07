#include "hw/lvgl_glue.h"
#include "board_pins.h"
#include "hw/display.h"
#include "hw/keyboard.h"
#include "hw/rotary.h"

#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <lvgl.h>

namespace lvgl_glue {
namespace {

lgfx::LGFX_Device *lcd = nullptr;
lv_display_t *disp = nullptr;
lv_indev_t *encoder = nullptr;
lv_indev_t *keypad = nullptr;
lv_group_t *curGroup = nullptr;
void (*backCb)() = nullptr;
bool (*rotaryCb)(int) = nullptr;
uint32_t lastInput = 0;

// Key event ring buffer: keyboard events arrive between reads.
constexpr int kQueueLen = 16;
uint32_t keyQueue[kQueueLen];
volatile int qHead = 0, qTail = 0;
uint32_t lastKey = 0;
bool keyDown = false;  // report PRESSED once, RELEASED on the next read

void pushKey(uint32_t key) {
  int next = (qHead + 1) % kQueueLen;
  if (next == qTail) return;  // full — drop
  keyQueue[qHead] = key;
  qHead = next;
}

void flushCb(lv_display_t *d, const lv_area_t *area, uint8_t *px_map) {
  if (lcd) {
    int w = area->x2 - area->x1 + 1;
    int h = area->y2 - area->y1 + 1;
    lcd->pushImage(area->x1, area->y1, w, h,
                   reinterpret_cast<lgfx::rgb565_t *>(px_map));
  }
  lv_display_flush_ready(d);
}

void encoderRead(lv_indev_t *, lv_indev_data_t *data) {
  int d = rotary::readDelta();
  if (d != 0) {
    lastInput = millis();
    // Sign flipped: clockwise must move down/right on this hardware.
    if (rotaryCb && rotaryCb(-d)) d = 0;  // consumed by the screen
  }
  data->enc_diff = (int16_t)-d;
  bool down = rotary::buttonDown();
  if (down) lastInput = millis();
  data->state = down ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
}

void keypadRead(lv_indev_t *, lv_indev_data_t *data) {
  if (keyDown) {
    // Release the previous key before reporting the next one.
    keyDown = false;
    data->key = lastKey;
    data->state = LV_INDEV_STATE_RELEASED;
    data->continue_reading = qTail != qHead;
    return;
  }
  if (qTail == qHead) {
    data->key = lastKey;
    data->state = LV_INDEV_STATE_RELEASED;
    return;
  }
  lastKey = keyQueue[qTail];
  qTail = (qTail + 1) % kQueueLen;
  keyDown = true;
  data->key = lastKey;
  data->state = LV_INDEV_STATE_PRESSED;
  data->continue_reading = true;
}

}  // namespace

void begin() {
  lcd = static_cast<lgfx::LGFX_Device *>(display::raw());
  lv_init();
  lv_tick_set_cb([]() -> uint32_t { return (uint32_t)millis(); });

  int w = display::width(), h = display::height();
  disp = lv_display_create(w, h);
  lv_display_set_flush_cb(disp, flushCb);
  // Draw buffers live in PSRAM: 2×42 KB of internal RAM matters more to
  // TLS handshakes (https daemons were failing with HTTP -1 under memory
  // pressure) than to render speed at this resolution.
  size_t bufSz = (size_t)w * (h / 5) * 2;
  static uint8_t *buf1 = (uint8_t *)heap_caps_malloc(
      bufSz, psramFound() ? MALLOC_CAP_SPIRAM : MALLOC_CAP_DEFAULT);
  static uint8_t *buf2 = (uint8_t *)heap_caps_malloc(
      bufSz, psramFound() ? MALLOC_CAP_SPIRAM : MALLOC_CAP_DEFAULT);
  lv_display_set_buffers(disp, buf1, buf2, bufSz,
                         LV_DISPLAY_RENDER_MODE_PARTIAL);

  // TrailMate look: default theme, dark, purple/teal accents.
  lv_theme_default_init(disp, lv_palette_main(LV_PALETTE_PURPLE),
                        lv_palette_main(LV_PALETTE_TEAL), true,
                        &lv_font_montserrat_14);

  encoder = lv_indev_create();
  lv_indev_set_type(encoder, LV_INDEV_TYPE_ENCODER);
  lv_indev_set_read_cb(encoder, encoderRead);

  keypad = lv_indev_create();
  lv_indev_set_type(keypad, LV_INDEV_TYPE_KEYPAD);
  lv_indev_set_read_cb(keypad, keypadRead);

  lastInput = millis();
  Serial.println("[lvgl] ready");
}

void setGroup(void *lv_group) {
  lv_group_t *g = static_cast<lv_group_t *>(lv_group);
  curGroup = g;
  if (encoder) lv_indev_set_group(encoder, g);
  if (keypad) lv_indev_set_group(keypad, g);
}

void onBack(void (*cb)()) { backCb = cb; }

uint32_t inactiveMs() { return millis() - lastInput; }

void pokeActivity() { lastInput = millis(); }

void setRotaryHandler(bool (*cb)(int)) { rotaryCb = cb; }

void loop() {
  // Side button = back, outside LVGL focus logic on purpose: it must work
  // whatever has focus.
  if (rotary::backPressed()) {
    lastInput = millis();
    if (backCb) backCb();
  }

  keyboard::Event ev{};
  while (keyboard::poll(&ev)) {
    lastInput = millis();
    if (ev.function) continue;  // caps/sym toggles — modifiers only
    if (ev.escape) {
      if (backCb) backCb();
    } else if (ev.enter) {
      pushKey(LV_KEY_ENTER);
    } else if (ev.backspace) {
      // Delete inside a non-empty text field; everywhere else (and on an
      // empty field) backspace means "back", like the pre-LVGL firmware.
      lv_obj_t *f = curGroup ? lv_group_get_focused(curGroup) : nullptr;
      bool editingText = f && lv_obj_check_type(f, &lv_textarea_class) &&
                         lv_textarea_get_text(f)[0] != '\0';
      if (editingText) {
        pushKey(LV_KEY_BACKSPACE);
      } else if (backCb) {
        backCb();
      }
    } else if (ev.tab) {
      pushKey(LV_KEY_NEXT);
    } else if (ev.ch >= 32 && ev.ch < 127) {
      pushKey((uint32_t)ev.ch);
    }
  }

  lv_timer_handler();
}

}  // namespace lvgl_glue
