#include "hw/board_init.h"
#include "board_pins.h"

#include <Arduino.h>
#include <Wire.h>

// Minimal XL9555 (TI / Shanghai Belling style) — output port 0 only.
namespace {

bool xlWritePort0(uint8_t value) {
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(0x02);  // output port 0
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool xlConfigOutputs() {
  // Config port 0 = all outputs (0 = output)
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(0x06);
  Wire.write(0x00);
  if (Wire.endTransmission() != 0) return false;
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(0x07);
  Wire.write(0x00);
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
  // KB_EN + DRV_EN + GPIO_EN high; AMP low until chime; LORA/GPS high (off path).
  if (xlConfigOutputs()) {
    uint8_t p0 = 0;
    p0 |= (1u << EXP_DRV_EN);
    p0 |= (1u << EXP_KB_EN);
    p0 |= (1u << EXP_GPIO_EN);
    p0 |= (1u << EXP_LORA_EN);
    p0 |= (1u << EXP_GPS_EN);
    p0 |= (1u << EXP_SD_EN);
    // AMP off
    p0 &= ~(1u << EXP_AMP_EN);
    if (xlWritePort0(p0)) {
      Serial.println("[board] XL9555 rails OK");
    } else {
      Serial.println("[board] XL9555 write failed");
    }
  } else {
    Serial.println("[board] XL9555 not found — continuing");
  }

  // Keyboard backlight default on
  pinMode(PIN_KB_BL, OUTPUT);
  digitalWrite(PIN_KB_BL, HIGH);

  delay(20);
}
