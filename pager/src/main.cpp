// Agent Remote — LILYGO T-LoRa Pager (SX1262) firmware client.

#include <Arduino.h>
#include <Wire.h>
#include <esp_task_wdt.h>

#include "app_config.h"
#include "board_pins.h"
#include "hw/board_init.h"
#include "hw/chime.h"
#include "hw/display.h"
#include "hw/keyboard.h"
#include "hw/power.h"
#include "hw/wifi_mgr.h"
#include "net/agentapi.h"
#include "ui/ui.h"

static AppConfig g_cfg;

void setup() {
  // USB CDC can take a moment after reset — print early and often.
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("=== Agent Remote / T-LoRa Pager ===");
  Serial.println("build: safe-boot path");
  Serial.flush();

  // Watchdog: 30s so a stuck peripheral cannot freeze forever without reset.
  esp_task_wdt_init(30, true);
  esp_task_wdt_add(NULL);

  Serial.println("[boot] boardEarlyInit");
  Serial.flush();
  boardEarlyInit();
  esp_task_wdt_reset();

  Serial.println("[boot] config");
  g_cfg.load();

  Serial.println("[boot] display");
  Serial.flush();
  display::begin(g_cfg.backlight);
  esp_task_wdt_reset();

  Serial.println("[boot] keyboard");
  keyboard::begin();
  esp_task_wdt_reset();

  Serial.println("[boot] power");
  power::begin();

  Serial.println("[boot] chime");
  chime::begin();
  chime::setEnabled(g_cfg.soundCues, g_cfg.hapticCues);

  Serial.println("[boot] wifi");
  wifi_mgr::begin();
  if (g_cfg.wifiSsid.length()) {
    wifi_mgr::connect(g_cfg.wifiSsid, g_cfg.wifiPass);
  }
  if (g_cfg.configured()) {
    agentapi::configure(g_cfg.apiBase(), g_cfg.daemonToken);
  }

  ui::begin(&g_cfg);
  ui::draw();

  chime::play(chime::Cue::Status, g_cfg.soundCues, g_cfg.hapticCues);
  Serial.println("[boot] ready");
  Serial.flush();
}

void loop() {
  esp_task_wdt_reset();
  wifi_mgr::tick();
  keyboard::tick();

  keyboard::Event ev{};
  while (keyboard::poll(&ev)) {
    ui::markActivity();
    if (ev.function) continue;
    ui::onKey(ev.ch, ev.enter, ev.backspace, ev.escape);
  }

  ui::onTick();
  power::idleTick(ui::lastActivityMs(), g_cfg.idleSleepMin, g_cfg.backlight);
  delay(10);
}
