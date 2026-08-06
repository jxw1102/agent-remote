#pragma once
// LILYGO T-LoRa Pager (K257 / SX1262) — pins from Meshtastic tlora-pager variant.

#ifndef BOARD_TLORA_PAGER
#define BOARD_TLORA_PAGER 1
#endif

// I2C bus: keyboard, charger, haptic, expander, fuel gauge (NOT 8/9!)
#define PIN_I2C_SDA    3
#define PIN_I2C_SCL    2
#define PIN_KB_SDA     PIN_I2C_SDA
#define PIN_KB_SCL     PIN_I2C_SCL
#define PIN_KB_INT     6
#define PIN_KB_BL      46
#define TCA8418_ADDR   0x34
#define XL9555_ADDR    0x20

// Shared SPI (display + LoRa + SD + NFC) — different CS lines
#define PIN_SPI_MOSI   34
#define PIN_SPI_MISO   33
#define PIN_SPI_SCK    35

// Display ST7796
#define PIN_TFT_CS     38
#define PIN_TFT_DC     37
#define PIN_TFT_BL     42
#define PIN_TFT_RST    -1
#define TFT_WIDTH_PX   480
#define TFT_HEIGHT_PX  222

// LoRa SX1262
#define PIN_LORA_CS    36
#define PIN_LORA_RST   47
#define PIN_LORA_DIO1  14
#define PIN_LORA_BUSY  48

// Rotary / boot
#define PIN_ROT_A      40
#define PIN_ROT_B      41
#define PIN_ROT_BTN    7
#define PIN_BOOT       0

// Audio ES8311 I2S
#define PIN_I2S_BCK    11
#define PIN_I2S_WS     18
#define PIN_I2S_DOUT   45
#define PIN_I2S_DIN    17
#define PIN_I2S_MCLK   10

// SD / NFC
#define PIN_SD_CS      21
#define PIN_NFC_CS     39
#define PIN_NFC_INT    5

// XL9555 expander bits
#define EXP_DRV_EN     0
#define EXP_AMP_EN     1
#define EXP_KB_RST     2
#define EXP_LORA_EN    3
#define EXP_GPS_EN     4
#define EXP_NFC_EN     5
#define EXP_GPS_RST    7
#define EXP_KB_EN      8
#define EXP_GPIO_EN    9
#define EXP_SD_DET     10
#define EXP_SD_PULLEN  11
#define EXP_SD_EN      12

#define BATT_DESIGN_MAH 1500
#define IDLE_SLEEP_MS          120000
#define BACKLIGHT_DEFAULT      180
#define STATUS_POLL_MS         1200
#define SSE_RECONNECT_MS       3000
