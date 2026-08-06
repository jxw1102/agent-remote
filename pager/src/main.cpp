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
#include "hw/rotary.h"
#include "hw/wifi_mgr.h"
#include "net/agentapi.h"
#include "net/statusfeed.h"
#include "ui/ui.h"

static AppConfig g_cfg;

void setup() {
  // USB CDC can take a moment after reset — print early and often.
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("=== Agent Remote / T-LoRa Pager ===");
  Serial.printf("build: %s %s\n", __DATE__, __TIME__);
  // Reset reason distinguishes panic/WDT bootloop from clean power-on;
  // PSRAM line confirms the qio_qspi memory config actually initialized.
  Serial.printf("[boot] reset reason=%d wakeup=%d\n", (int)esp_reset_reason(),
                (int)esp_sleep_get_wakeup_cause());
  Serial.printf("[boot] chip rev=%d psram=%s (%u KB) heap=%u KB\n",
                ESP.getChipRevision(), psramFound() ? "ok" : "MISSING",
                (unsigned)(ESP.getPsramSize() / 1024),
                (unsigned)(ESP.getHeapSize() / 1024));
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

  Serial.println("[boot] rotary");
  rotary::begin();

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
    statusfeed::configure(g_cfg.apiBase(), g_cfg.daemonToken);
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
  statusfeed::tick();
  keyboard::tick();

  // Rotary knob: rotate = navigate, press = select. Side (BOOT) button = back.
  int rd = rotary::readDelta();
  if (rd != 0) {
    ui::onRotary(rd);
  }
  if (rotary::pressed()) {
    ui::onKey(0, true, false, false);
  }
  if (rotary::backPressed()) {
    ui::onKey(0, false, false, true);
  }

  keyboard::Event ev{};
  while (keyboard::poll(&ev)) {
    ui::markActivity();
    if (ev.function) {
      ui::draw();  // refresh CAP/SYM indicator
      continue;
    }
    // ev.escape only comes from the serial fallback — route it to "back".
    ui::onKey(ev.ch, ev.enter, ev.backspace, ev.escape);
  }

  ui::onTick();
  power::idleTick(ui::lastActivityMs(), g_cfg.idleSleepMin, g_cfg.backlight);
  delay(10);
}
