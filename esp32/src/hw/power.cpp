#include "hw/power.h"
#include "hw/display.h"
#include "board_pins.h"
#include "hw/i2c_lock.h"

#include <WiFi.h>
#include <Wire.h>
#include <esp_sleep.h>

// BQ25896 charger (7-bit 0x6B) + BQ27220 fuel gauge (7-bit 0x55), both on
// the shared I2C bus. Soft-fail if absent.
#ifndef BQ25896_ADDR
#define BQ25896_ADDR 0x6B
#endif
#ifndef BQ27220_ADDR
#define BQ27220_ADDR 0x55
#endif

namespace power {
namespace {

uint8_t dimmed = 0;
bool charger_ok = false;
bool gauge_ok = false;

bool i2cRead(uint8_t addr, uint8_t reg, uint8_t *val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)addr, 1) != 1) return false;
  *val = Wire.read();
  return true;
}

// BQ27220 standard commands are 16-bit little-endian.
bool gaugeRead16(uint8_t cmd, uint16_t *val) {
  Wire.beginTransmission(BQ27220_ADDR);
  Wire.write(cmd);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)BQ27220_ADDR, 2) != 2) return false;
  uint16_t lo = Wire.read();
  uint16_t hi = Wire.read();
  *val = (uint16_t)(lo | (hi << 8));
  return true;
}

constexpr uint8_t kGaugeVoltage = 0x08;        // mV
constexpr uint8_t kGaugeStateOfCharge = 0x2C;  // %

}  // namespace

void begin() {
#if HAS_CHARGER_BQ25896
  uint8_t id = 0;
  charger_ok = i2cRead(BQ25896_ADDR, 0x14, &id);
#endif
#if HAS_GAUGE_BQ27220
  uint16_t mv = 0;
  gauge_ok = gaugeRead16(kGaugeVoltage, &mv) && mv > 2000 && mv < 5000;
  Serial.printf("[power] BQ25896 %s, BQ27220 %s (%u mV)\n",
                charger_ok ? "present" : "absent",
                gauge_ok ? "present" : "absent", (unsigned)mv);
#endif
#if HAS_BATTERY_ADC
  analogReadResolution(12);
  Serial.println("[power] battery via ADC divider");
#endif
  // Wake on BOOT (active low). Keyboard INT can be added later.
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_BOOT, 0);
}

Status read() {
  I2cLock lock;
  Status s{};
  s.percent = -1;
  s.voltage = 0;
  s.charging = false;
  s.usb = false;
#if HAS_CHARGER_BQ25896
  if (charger_ok) {
    uint8_t vbus = 0;
    if (i2cRead(BQ25896_ADDR, 0x11, &vbus)) {
      s.usb = (vbus & 0xE0) != 0;
      s.charging = s.usb;
    }
  }
#endif
#if HAS_GAUGE_BQ27220
  if (gauge_ok) {
    uint16_t soc = 0, mv = 0;
    if (gaugeRead16(kGaugeStateOfCharge, &soc) && soc <= 100)
      s.percent = (int)soc;
    if (gaugeRead16(kGaugeVoltage, &mv)) s.voltage = mv / 1000.0f;
  }
#endif
#if HAS_BATTERY_ADC
  {
    // Divider halves the cell voltage; linear 3.3–4.2 V → 0–100 %.
    uint32_t mv = analogReadMilliVolts(PIN_BAT_ADC) * 2;
    s.voltage = mv / 1000.0f;
    int pct = (int)(((int)mv - 3300) * 100 / (4200 - 3300));
    s.percent = pct < 0 ? 0 : (pct > 100 ? 100 : pct);
  }
#endif
  return s;
}

void deepSleepSeconds(uint32_t sec) {
  display::setBacklight(0);
  Serial.printf("[power] deep sleep %u s\n", (unsigned)sec);
  Serial.flush();
  if (sec > 0) esp_sleep_enable_timer_wakeup((uint64_t)sec * 1000000ULL);
  esp_deep_sleep_start();
}

void powerOff() {
  Serial.println("[power] off — wake with knob or side button");
  Serial.flush();
  display::sleepPanel();
#if HAS_XL9555
  // Keyboard backlight off, all XL9555 rails off (keyboard, LoRa, GPS, SD…).
  digitalWrite(PIN_KB_BL, LOW);
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(0x02);
  Wire.write((uint8_t)0x00);
  Wire.endTransmission();
  Wire.beginTransmission(XL9555_ADDR);
  Wire.write(0x03);
  Wire.write((uint8_t)0x00);
  Wire.endTransmission();
#endif
#if HAS_PERIPH_POWERON
  digitalWrite(PIN_PERIPH_POWERON, LOW);  // one switch cuts everything
#endif
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  pinMode(PIN_BOOT, INPUT_PULLUP);
#ifdef PIN_ROT_BTN
  pinMode(PIN_ROT_BTN, INPUT_PULLUP);
  const uint64_t wakePins = (1ULL << PIN_BOOT) | (1ULL << PIN_ROT_BTN);
#else
  const uint64_t wakePins = (1ULL << PIN_BOOT);
#endif
  esp_sleep_enable_ext1_wakeup(wakePins, ESP_EXT1_WAKEUP_ANY_LOW);
  delay(100);
  esp_deep_sleep_start();
}

void restart() {
  Serial.println("[power] restart");
  Serial.flush();
  delay(50);
  esp_restart();
}

void idleTick(uint32_t lastActivityMs, uint8_t idleSleepMin, uint8_t backlight) {
  uint32_t idle = millis() - lastActivityMs;
  // Dim fast (15 s); chimes count as activity so events light the screen.
  uint32_t sleepAt = (uint32_t)idleSleepMin * 60UL * 1000UL;
  uint32_t dimAt = 15000UL;
  if (idleSleepMin && idle > sleepAt) {
    deepSleepSeconds(0);
  } else if (idle > dimAt) {
    if (!dimmed) {
      display::setBacklight((uint8_t)max(20, backlight / 4));
      dimmed = 1;
    }
  } else if (dimmed) {
    display::setBacklight(backlight);
    dimmed = 0;
  }
}

}  // namespace power
