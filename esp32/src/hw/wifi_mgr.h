#pragma once
#include <Arduino.h>

namespace wifi_mgr {

enum class State : uint8_t { Off, Connecting, Connected, Failed };

struct ScanItem {
  String ssid;
  int rssi;
  bool secure;
};

void begin();
// Saved networks for auto-connect (strongest in range wins; unseen ones —
// e.g. hidden SSIDs — are tried last). Call again whenever the list changes.
void setSaved(const String *ssids, const String *passes, int count);
// Scan, then join the best saved network; retries on failure/drop.
void autoConnect();
// Async scan: startScan(), then poll scanReady(); results deduped by SSID,
// sorted by RSSI.
void startScan();
bool scanning();
bool scanReady();
int scanCount();
const ScanItem &scanItem(int i);
// Non-blocking connect attempt; call tick() in loop.
void connect(const String &ssid, const String &pass);
void tick();
void disconnect();
State state();
String ip();
int rssi();
const char *stateName();

}  // namespace wifi_mgr
