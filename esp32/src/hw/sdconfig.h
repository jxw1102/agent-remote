#pragma once
// Config backup on the SD card (shared SPI bus, CS 21): every save mirrors
// the settings to /agentremote.json, and a fresh flash (empty NVS) imports
// it back — no re-entering Wi-Fi and daemon credentials after flashing.
// The file is plain JSON, so it can also be written from a PC to provision
// a device.

#include <Arduino.h>

struct AppConfig;

namespace sdconfig {

// Mount once at boot (before the display starts using the shared bus).
void begin();
bool present();

// Read /agentremote.json into cfg. True if a config was loaded.
bool importConfig(AppConfig *cfg);
// Write cfg to /agentremote.json. Best-effort.
bool exportConfig(const AppConfig &cfg);

}  // namespace sdconfig
