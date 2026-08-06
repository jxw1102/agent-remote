#include "hw/display.h"
#include "board_pins.h"

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

namespace {

class LGFX_Pager : public lgfx::LGFX_Device {
  lgfx::Panel_ST7796 _panel;
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
      cfg.memory_width = 320;
      cfg.memory_height = 480;
      cfg.panel_width = 222;
      cfg.panel_height = 480;
      cfg.offset_x = 49;
      cfg.offset_y = 0;
      cfg.offset_rotation = 3;
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
  lcd->setRotation(1);
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

int width() { return ready && lcd ? lcd->width() : TFT_WIDTH_PX; }
int height() { return ready && lcd ? lcd->height() : TFT_HEIGHT_PX; }

void flush() {}

}  // namespace display
