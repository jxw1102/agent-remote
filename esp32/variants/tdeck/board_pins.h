#pragma once
// LILYGO T-Deck / T-Deck Plus — pins from the LilyGo examples / Meshtastic
// t-deck variant. EXPERIMENTAL: compiles and follows the reference pinout,
// but has not been verified on hardware yet.

#ifndef BOARD_TDECK
#define BOARD_TDECK 1
#endif

// Peripheral master switch: keyboard co-processor, display, speaker.
#define PIN_PERIPH_POWERON 10

// I2C: keyboard co-processor (ESP32-C3 at 0x55), touch.
#define PIN_I2C_SDA    18
#define PIN_I2C_SCL    8
#define PIN_KB_SDA     PIN_I2C_SDA
#define PIN_KB_SCL     PIN_I2C_SCL
#define PIN_KB_INT     46
#define TDECK_KB_ADDR  0x55

// Shared SPI (display + SD)
#define PIN_SPI_MOSI   41
#define PIN_SPI_MISO   38
#define PIN_SPI_SCK    40

// Display ST7789 320x240
#define PIN_TFT_CS     12
#define PIN_TFT_DC     11
#define PIN_TFT_BL     42
#define PIN_TFT_RST    -1
#define TFT_WIDTH_PX   320
#define TFT_HEIGHT_PX  240

// Trackball (pulse outputs) + click (shared with BOOT strap)
#define PIN_TB_UP      3
#define PIN_TB_DOWN    15
#define PIN_TB_LEFT    1
#define PIN_TB_RIGHT   2
#define PIN_TB_CLICK   0
#define PIN_BOOT       0

// Speaker: bare I2S amp (no codec)
#define PIN_I2S_BCK    7
#define PIN_I2S_WS     5
#define PIN_I2S_DOUT   6

// SD
#define PIN_SD_CS      39

// Battery: ADC divider (no charger IC / fuel gauge readback)
#define PIN_BAT_ADC    4

#define BATT_DESIGN_MAH 2000
#define IDLE_SLEEP_MS          120000
#define BACKLIGHT_DEFAULT      180
#define STATUS_POLL_MS         1200
#define SSE_RECONNECT_MS       3000

// ---- capability flags (drive the hw module implementations) ----
#define HAS_TDECK_KEYBOARD 1  // I2C co-processor, one ASCII byte per read
#define HAS_TRACKBALL 1       // pulses mapped to encoder detents
#define HAS_BARE_I2S 1        // tones straight into the I2S amp
#define HAS_BATTERY_ADC 1
#define HAS_PERIPH_POWERON 1
