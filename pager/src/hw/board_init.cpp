#include "hw/board_init.h"
#include "board_pins.h"

#include <Arduino.h>
#include <Wire.h>

// Minimal XL9555 (PCA9555-style): output regs 0x02/0x03, config regs
// 0x06/0x07 (1 = input, 0 = output). Rail bits 8+ live on port 1.
namespace {

bool xlWriteReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

}  // namespace

void boardEarlyInit() {
  // Deselect all SPI slaves so a shared bus does not glitch during TFT init.
  pinMode(PIN_TFT_CS, OUTPUT);
  digitalWrite(PIN_TFT_CS, HIGH);
  pinMode(PIN_LORA_CS, OUTPUT);
  digitalWrite(PIN_LORA_CS, HIGH);
  pinMode(PIN_SD_CS, OUTPUT);
  digitalWrite(PIN_SD_CS, HIGH);
  pinMode(PIN_NFC_CS, OUTPUT);
  digitalWrite(PIN_NFC_CS, HIGH);

  pinMode(PIN_KB_INT, INPUT_PULLUP);
  pinMode(PIN_BOOT, INPUT_PULLUP);

  // I2C on the pager is GPIO 3/2 — wrong pins freeze many units.
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(100000);

  // Power rails via XL9555 (Meshtastic earlyInitVariant).
  // Port 0: DRV/LORA/GPS on, AMP off. KB_RST/NFC_EN/GPS_RST left as inputs —
  // driving KB_RST low holds the TCA8418 in reset (dead keyboard).
  // Port 1: KB_EN/GPIO_EN/SD_EN on; SD_DET/SD_PULLEN stay inputs.
  const uint8_t out0 = (1u << EXP_DRV_EN) | (1u << EXP_LORA_EN) |
                       (1u << EXP_GPS_EN);
  const uint8_t out1 = (1u << (EXP_KB_EN - 8)) | (1u << (EXP_GPIO_EN - 8)) |
                       (1u << (EXP_SD_EN - 8));
  // Output values first, then config — avoids glitching a pin low while it
  // flips from input to output (power-on output registers default to 0xFF).
  bool ok = xlWriteReg(0x02, out0) && xlWriteReg(0x03, out1) &&
            xlWriteReg(0x06, (uint8_t)~((1u << EXP_DRV_EN) |
                                        (1u << EXP_AMP_EN) |
                                        (1u << EXP_LORA_EN) |
                                        (1u << EXP_GPS_EN))) &&
            xlWriteReg(0x07, (uint8_t)~out1);
  Serial.println(ok ? "[board] XL9555 rails OK"
                    : "[board] XL9555 not found — continuing");

  // Keyboard backlight default on
  pinMode(PIN_KB_BL, OUTPUT);
  digitalWrite(PIN_KB_BL, HIGH);

  delay(20);
}
