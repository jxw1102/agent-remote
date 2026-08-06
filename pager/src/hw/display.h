#pragma once
#include <Arduino.h>

namespace display {

void begin(uint8_t backlight = 180);
void setBacklight(uint8_t level);  // 0–255
void clear(uint16_t color = 0x0000);
void fillRect(int x, int y, int w, int h, uint16_t color);
void drawText(int x, int y, const char *text, uint16_t color, uint8_t size = 1);
void drawTextTrunc(int x, int y, int maxW, const char *text, uint16_t color, uint8_t size = 1);
void hLine(int y, uint16_t color);
int width();
int height();
void flush();

// RGB565 helpers
constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}
constexpr uint16_t COL_BG = 0x10A2;      // near black
constexpr uint16_t COL_FG = 0xE71C;      // light gray
constexpr uint16_t COL_DIM = 0x8410;
constexpr uint16_t COL_ACCENT = 0xA81F;  // purple multi
constexpr uint16_t COL_OK = 0x07E0;
constexpr uint16_t COL_WARN = 0xFD20;
constexpr uint16_t COL_ERR = 0xF800;
constexpr uint16_t COL_BAR = 0x2104;

}  // namespace display
