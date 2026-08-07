#pragma once
// One mutex for the shared I2C bus: the loop task polls the keyboard and
// battery while the chime task drives the amp rail and haptic — Wire is not
// thread-safe, so every user takes this lock around its transaction(s).

#include <Arduino.h>

inline SemaphoreHandle_t i2cMutex() {
  static SemaphoreHandle_t m = xSemaphoreCreateMutex();
  return m;
}

struct I2cLock {
  I2cLock() { xSemaphoreTake(i2cMutex(), portMAX_DELAY); }
  ~I2cLock() { xSemaphoreGive(i2cMutex()); }
};
