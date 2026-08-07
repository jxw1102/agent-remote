// Agent Remote — LILYGO T-LoRa Pager (SX1262) firmware client.

#include <Arduino.h>
#include <Wire.h>
#include <esp_task_wdt.h>

#include "app_config.h"
#include "board_pins.h"
#include "hw/board_init.h"
#include "hw/chime.h"
#include "hw/display.h"
#include "hw/dlog.h"
#include "hw/keyboard.h"
#include "hw/lvgl_glue.h"
#include "hw/power.h"
#include "hw/rotary.h"
#include "hw/sdconfig.h"
#include "hw/wifi_mgr.h"
#include "net/agentapi.h"
#include "net/blebuddy.h"
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

  dlog::begin();

  Serial.println("[boot] boardEarlyInit");
  Serial.flush();
  boardEarlyInit();
  esp_task_wdt_reset();

  Serial.println("[boot] config");
  g_cfg.load();
  sdconfig::begin();
  // Fresh flash (empty NVS) with a provisioned SD card: import + persist.
  if (!g_cfg.configured() && sdconfig::importConfig(&g_cfg)) {
    g_cfg.save();
    Serial.println("[boot] config imported from SD card");
  }

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
  chime::setVolume(g_cfg.volume);

  Serial.println("[boot] wifi");
  wifi_mgr::begin();
  if (g_cfg.wifiCount > 0) {
    // ui::begin pushes the saved list; join the best network in range.
    String ss[AppConfig::kMaxWifi], pp[AppConfig::kMaxWifi];
    for (int i = 0; i < g_cfg.wifiCount; i++) {
      ss[i] = g_cfg.wifis[i].ssid;
      pp[i] = g_cfg.wifis[i].pass;
    }
    wifi_mgr::setSaved(ss, pp, g_cfg.wifiCount);
    wifi_mgr::autoConnect();
  }
  // Daemon wiring happens in ui::begin (applyDaemons) for all slots.

  Serial.println("[boot] lvgl");
  lvgl_glue::begin();

  ui::begin(&g_cfg);

  if (g_cfg.bleMode) {
    Serial.println("[boot] ble buddy");
    blebuddy::begin();
  }

  chime::play(chime::Cue::Status, g_cfg.soundCues, g_cfg.hapticCues);
  Serial.println("[boot] ready");
  Serial.flush();
}

void loop() {
  esp_task_wdt_reset();
  wifi_mgr::tick();
  statusfeed::tick();
  blebuddy::tick();
  keyboard::tick();

  // LVGL owns input now: glue pumps knob + keyboard into indevs and runs
  // lv_timer_handler; ui::tick does the beeper controller work.
  lvgl_glue::loop();
  ui::tick();

  power::idleTick(ui::lastActivityMs(), g_cfg.idleSleepMin, g_cfg.backlight);
  delay(5);
}
