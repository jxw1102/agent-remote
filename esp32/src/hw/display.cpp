#include "hw/display.h"
#include "board_pins.h"

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

namespace {

class LGFX_Pager : public lgfx::LGFX_Device {
#if defined(BOARD_TDECK)
  lgfx::Panel_ST7789 _panel;
#else
  lgfx::Panel_ST7796 _panel;
#endif
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;

 public:
  LGFX_Pager() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI2_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = 40000000;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = true;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      // Shared bus with LoRa (CS separate) — same as Meshtastic pins_arduino.
      cfg.pin_sclk = PIN_SPI_SCK;   // 35
      cfg.pin_mosi = PIN_SPI_MOSI;  // 34
      cfg.pin_miso = PIN_SPI_MISO;  // 33
      cfg.pin_dc = PIN_TFT_DC;      // 37
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = PIN_TFT_CS;
      cfg.pin_rst = PIN_TFT_RST;
      cfg.pin_busy = -1;
#if defined(BOARD_TDECK)
      cfg.memory_width = 240;
      cfg.memory_height = 320;
      cfg.panel_width = 240;
      cfg.panel_height = 320;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 1;  // landscape, keyboard below
#else
      cfg.memory_width = 320;
      cfg.memory_height = 480;
      cfg.panel_width = 222;
      cfg.panel_height = 480;
      cfg.offset_x = 49;
      cfg.offset_y = 0;
      cfg.offset_rotation = 3;
#endif
      cfg.dummy_read_pixel = 8;
      cfg.readable = false;
      cfg.invert = true;
      cfg.rgb_order = false;
      cfg.dlen_16bit = false;
      cfg.bus_shared = true;
      _panel.config(cfg);
    }
    {
      auto cfg = _light.config();
      cfg.pin_bl = PIN_TFT_BL;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      _panel.setLight(&_light);
    }
    setPanel(&_panel);
  }
};

LGFX_Pager *lcd = nullptr;
bool ready = false;

}  // namespace

namespace display {

void begin(uint8_t backlight) {
  // Heap-allocate so a failed panel does not static-init crash path.
  if (!lcd) lcd = new LGFX_Pager();
  ready = false;
  bool ok = false;
  // init() can return false; never hang forever — caller continues.
  ok = lcd->init();
  if (!ok) {
    Serial.println("[display] init returned false — UI on serial only");
    return;
  }
  // offset_rotation=3 already maps the panel to landscape-with-keyboard-below
  // (Meshtastic device-ui uses rotation 0 on top of it). setRotation(1) here
  // composed to native portrait — screen came out 90° off.
  lcd->setRotation(0);
  lcd->setBrightness(backlight);
  lcd->setTextDatum(lgfx::textdatum_t::top_left);
  lcd->setFont(&fonts::Font0);
  lcd->fillScreen(COL_BG);
  ready = true;
  Serial.printf("[display] %dx%d ok\n", lcd->width(), lcd->height());
}

void setBacklight(uint8_t level) {
  if (ready && lcd) lcd->setBrightness(level);
  // Always drive BL pin as fallback if LGFX light failed
  pinMode(PIN_TFT_BL, OUTPUT);
  // crude PWM-less: HIGH if level>20
  digitalWrite(PIN_TFT_BL, level > 20 ? HIGH : LOW);
}

void clear(uint16_t color) {
  if (ready && lcd) lcd->fillScreen(color);
}

void fillRect(int x, int y, int w, int h, uint16_t color) {
  if (ready && lcd) lcd->fillRect(x, y, w, h, color);
}

void drawText(int x, int y, const char *text, uint16_t color, uint8_t size) {
  if (!ready || !lcd || !text) return;
  lcd->setTextSize(size);
  lcd->setTextColor(color, COL_BG);
  lcd->drawString(text, x, y);
}

void drawTextTrunc(int x, int y, int maxW, const char *text, uint16_t color,
                   uint8_t size) {
  if (!ready || !lcd || !text) return;
  lcd->setTextSize(size);
  lcd->setTextColor(color, COL_BG);
  lcd->setClipRect(x, y, maxW, 16 * size);
  lcd->drawString(text, x, y);
  lcd->clearClipRect();
}

void hLine(int y, uint16_t color) {
  if (ready && lcd) lcd->drawFastHLine(0, y, lcd->width(), color);
}

void fillRoundRect(int x, int y, int w, int h, int r, uint16_t color) {
  if (ready && lcd) lcd->fillRoundRect(x, y, w, h, r, color);
}

void drawRoundRect(int x, int y, int w, int h, int r, uint16_t color) {
  if (ready && lcd) lcd->drawRoundRect(x, y, w, h, r, color);
}

void fillCircle(int x, int y, int r, uint16_t color) {
  if (ready && lcd) lcd->fillCircle(x, y, r, color);
}

void drawCircle(int x, int y, int r, uint16_t color) {
  if (ready && lcd) lcd->drawCircle(x, y, r, color);
}

void drawLine(int x0, int y0, int x1, int y1, uint16_t color) {
  if (ready && lcd) lcd->drawLine(x0, y0, x1, y1, color);
}

void fillTriangle(int x0, int y0, int x1, int y1, int x2, int y2,
                  uint16_t color) {
  if (ready && lcd) lcd->fillTriangle(x0, y0, x1, y1, x2, y2, color);
}

void fillArc(int x, int y, int r0, int r1, float a0, float a1,
             uint16_t color) {
  if (ready && lcd) lcd->fillArc(x, y, r0, r1, a0, a1, color);
}

void sleepPanel() {
  if (ready && lcd) {
    lcd->setBrightness(0);
    lcd->sleep();
  }
  pinMode(PIN_TFT_BL, OUTPUT);
  digitalWrite(PIN_TFT_BL, LOW);
}

void *raw() { return ready ? lcd : nullptr; }

int width() { return ready && lcd ? lcd->width() : TFT_WIDTH_PX; }
int height() { return ready && lcd ? lcd->height() : TFT_HEIGHT_PX; }

void flush() {}

}  // namespace display
