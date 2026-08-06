#pragma once
// Persistent NVS settings for Agent Remote on the pager.

#include <Arduino.h>
#include <Preferences.h>

struct AppConfig {
  String wifiSsid;
  String wifiPass;
  // Daemon: host:port or full http(s):// base (no trailing slash).
  String daemonUrl;
  String daemonToken;
  // Optional multi path prefix, e.g. "" or "/claude".
  String providerPath;
  bool soundCues = true;
  bool hapticCues = true;
  uint8_t backlight = 180;
  // Auto deep-sleep after idle minutes (0 = never). Default off: deep sleep
  // drops the USB-Serial-JTAG port, which reads as a dead device on the desk.
  uint8_t idleSleepMin = 0;

  bool load() {
    Preferences p;
    if (!p.begin("agentremote", true)) return false;
    wifiSsid = p.getString("ssid", "");
    wifiPass = p.getString("wpass", "");
    daemonUrl = p.getString("url", "");
    daemonToken = p.getString("token", "");
    providerPath = p.getString("ppath", "");
    soundCues = p.getBool("sound", true);
    hapticCues = p.getBool("haptic", true);
    backlight = p.getUChar("bl", 180);
    idleSleepMin = p.getUChar("sleepm", 5);
    p.end();
    return true;
  }

  bool save() const {
    Preferences p;
    if (!p.begin("agentremote", false)) return false;
    p.putString("ssid", wifiSsid);
    p.putString("wpass", wifiPass);
    p.putString("url", daemonUrl);
    p.putString("token", daemonToken);
    p.putString("ppath", providerPath);
    p.putBool("sound", soundCues);
    p.putBool("haptic", hapticCues);
    p.putUChar("bl", backlight);
    p.putUChar("sleepm", idleSleepMin);
    p.end();
    return true;
  }

  bool configured() const {
    return wifiSsid.length() > 0 && daemonUrl.length() > 0 && daemonToken.length() > 0;
  }

  // Build API base without trailing slash.
  String apiBase() const {
    String u = daemonUrl;
    while (u.endsWith("/")) u.remove(u.length() - 1);
    String path = providerPath;
    if (path.length() && !path.startsWith("/")) path = "/" + path;
    while (path.endsWith("/")) path.remove(path.length() - 1);
    return u + path;
  }
};
