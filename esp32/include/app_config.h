#pragma once
// Persistent NVS settings for Agent Remote on the pager.

#include <Arduino.h>
#include <Preferences.h>

struct AppConfig {
  // Saved Wi-Fi networks; auto-connect picks the strongest one in range.
  static constexpr int kMaxWifi = 4;
  struct WifiCred {
    String ssid;
    String pass;
  };
  WifiCred wifis[kMaxWifi];
  uint8_t wifiCount = 0;

  // Most-recently-used network goes to the front; known SSIDs update in
  // place. Oldest entry falls off when the list is full.
  void addWifi(const String &ssid, const String &pass) {
    int found = -1;
    for (int i = 0; i < wifiCount; i++)
      if (wifis[i].ssid == ssid) found = i;
    WifiCred c{ssid, pass};
    int from = found >= 0 ? found : (wifiCount < kMaxWifi ? wifiCount
                                                          : kMaxWifi - 1);
    if (found < 0 && wifiCount < kMaxWifi) wifiCount++;
    for (int i = from; i > 0; i--) wifis[i] = wifis[i - 1];
    wifis[0] = c;
  }

  void removeWifi(int idx) {
    if (idx < 0 || idx >= wifiCount) return;
    for (int i = idx; i + 1 < wifiCount; i++) wifis[i] = wifis[i + 1];
    wifiCount--;
  }

  // Multiple daemons (Android "profiles"): each is host:port or full
  // http(s):// base plus its token. Slot 0 is the primary. Three is the
  // measured ceiling: each daemon holds a socket (of lwIP's ~10) and, when
  // https, a ~25-45 KB TLS session — 3× https left ~50 KB heap in the field.
  static constexpr int kMaxDaemons = 3;
  struct DaemonCfg {
    String url;
    String token;
    bool enabled = true;
  };
  DaemonCfg daemons[kMaxDaemons];
  uint8_t daemonCount = 0;

  bool soundCues = true;
  bool hapticCues = true;
  uint8_t volume = 50;   // chime volume %, 0–100
  bool bleMode = false;  // claude-desktop-buddy BLE bridge
  uint8_t backlight = 180;
  // Auto deep-sleep after idle minutes (0 = never). Default off: deep sleep
  // drops the USB-Serial-JTAG port, which reads as a dead device on the desk.
  uint8_t idleSleepMin = 0;

  bool load() {
    Preferences p;
    if (!p.begin("agentremote", true)) return false;
    wifiCount = p.getUChar("wn", 255);
    if (wifiCount == 255) {
      // Migrate the single-network fields from earlier firmware.
      String ss = p.getString("ssid", "");
      wifiCount = ss.length() ? 1 : 0;
      wifis[0].ssid = ss;
      wifis[0].pass = p.getString("wpass", "");
    } else {
      if (wifiCount > kMaxWifi) wifiCount = kMaxWifi;
      for (int i = 0; i < wifiCount; i++) {
        wifis[i].ssid = p.getString(("ws" + String(i)).c_str(), "");
        wifis[i].pass = p.getString(("wp" + String(i)).c_str(), "");
      }
    }
    daemonCount = p.getUChar("dn", 255);
    if (daemonCount == 255) {
      // Migrate the single-daemon fields from earlier firmware.
      String u = p.getString("url", "");
      String t = p.getString("token", "");
      daemonCount = u.length() ? 1 : 0;
      daemons[0].url = u;
      daemons[0].token = t;
    } else {
      if (daemonCount > kMaxDaemons) daemonCount = kMaxDaemons;
      for (int i = 0; i < daemonCount; i++) {
        daemons[i].url = p.getString(("u" + String(i)).c_str(), "");
        daemons[i].token = p.getString(("t" + String(i)).c_str(), "");
        daemons[i].enabled = p.getBool(("e" + String(i)).c_str(), true);
      }
    }
    soundCues = p.getBool("sound", true);
    hapticCues = p.getBool("haptic", true);
    volume = p.getUChar("vol", 50);
    bleMode = p.getBool("ble", false);
    backlight = p.getUChar("bl", 180);
    idleSleepMin = p.getUChar("sleepm", 0);
    p.end();
    return true;
  }

  bool save() const {
    Preferences p;
    if (!p.begin("agentremote", false)) return false;
    p.putUChar("wn", wifiCount);
    for (int i = 0; i < wifiCount; i++) {
      p.putString(("ws" + String(i)).c_str(), wifis[i].ssid);
      p.putString(("wp" + String(i)).c_str(), wifis[i].pass);
    }
    p.putUChar("dn", daemonCount);
    for (int i = 0; i < daemonCount; i++) {
      p.putString(("u" + String(i)).c_str(), daemons[i].url);
      p.putString(("t" + String(i)).c_str(), daemons[i].token);
      p.putBool(("e" + String(i)).c_str(), daemons[i].enabled);
    }
    p.putBool("sound", soundCues);
    p.putBool("haptic", hapticCues);
    p.putUChar("vol", volume);
    p.putBool("ble", bleMode);
    p.putUChar("bl", backlight);
    p.putUChar("sleepm", idleSleepMin);
    p.end();
    return true;
  }

  bool configured() const {
    return wifiCount > 0 && daemonCount > 0 && daemons[0].url.length() > 0;
  }

  int enabledDaemons() const {
    int n = 0;
    for (int i = 0; i < daemonCount; i++)
      if (daemons[i].enabled) n++;
    return n;
  }

  // API base for one daemon, without trailing slash.
  String apiBase(int i = 0) const {
    if (i < 0 || i >= daemonCount) return String();
    String u = daemons[i].url;
    while (u.endsWith("/")) u.remove(u.length() - 1);
    return u;
  }

  // Short display name: host without scheme/port ("192.168.50.217", "mac").
  String daemonName(int i) const {
    String u = apiBase(i);
    int p = u.indexOf("://");
    if (p >= 0) u = u.substring(p + 3);
    p = u.indexOf(':');
    if (p >= 0) u = u.substring(0, p);
    p = u.indexOf('/');
    if (p >= 0) u = u.substring(0, p);
    return u;
  }
};
