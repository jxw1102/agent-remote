#include "hw/wifi_mgr.h"

#include <WiFi.h>

#include <algorithm>
#include <vector>

namespace wifi_mgr {
namespace {

State st = State::Off;
uint32_t started = 0;
String wantSsid;

bool scanInFlight = false;
bool scanDone = false;
std::vector<ScanItem> scanResults;
const ScanItem kEmptyItem{};

void collectScan() {
  int n = WiFi.scanComplete();
  if (n < 0) return;  // still running / not started
  scanResults.clear();
  for (int i = 0; i < n; i++) {
    String ssid = WiFi.SSID(i);
    if (!ssid.length()) continue;  // skip hidden
    bool secure = WiFi.encryptionType(i) != WIFI_AUTH_OPEN;
    int rssi = WiFi.RSSI(i);
    bool dup = false;
    for (auto &it : scanResults) {
      if (it.ssid == ssid) {  // keep strongest AP per SSID
        if (rssi > it.rssi) it.rssi = rssi;
        dup = true;
        break;
      }
    }
    if (!dup) scanResults.push_back({ssid, rssi, secure});
  }
  std::sort(scanResults.begin(), scanResults.end(),
            [](const ScanItem &a, const ScanItem &b) { return a.rssi > b.rssi; });
  WiFi.scanDelete();
  scanInFlight = false;
  scanDone = true;
  Serial.printf("[wifi] scan done: %d networks\n", (int)scanResults.size());
}

}  // namespace

void begin() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);  // power friendly on battery
  st = State::Off;
}

void startScan() {
  scanDone = false;
  scanInFlight = true;
  scanResults.clear();
  WiFi.scanNetworks(true /*async*/);
  Serial.println("[wifi] scan started");
}

bool scanning() { return scanInFlight; }

bool scanReady() {
  if (scanInFlight) collectScan();
  return scanDone;
}

int scanCount() { return (int)scanResults.size(); }

const ScanItem &scanItem(int i) {
  if (i < 0 || i >= (int)scanResults.size()) return kEmptyItem;
  return scanResults[(size_t)i];
}

void connect(const String &ssid, const String &pass) {
  wantSsid = ssid;
  st = State::Connecting;
  started = millis();
  WiFi.disconnect(true, true);
  delay(50);
  WiFi.begin(ssid.c_str(), pass.c_str());
  Serial.printf("[wifi] connecting to %s\n", ssid.c_str());
}

void tick() {
  if (st == State::Connecting) {
    if (WiFi.status() == WL_CONNECTED) {
      st = State::Connected;
      Serial.printf("[wifi] connected %s rssi=%d\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
    } else if (millis() - started > 20000) {
      st = State::Failed;
      Serial.println("[wifi] connect timeout");
    }
  } else if (st == State::Connected && WiFi.status() != WL_CONNECTED) {
    st = State::Failed;
  }
}

void disconnect() {
  WiFi.disconnect(true);
  st = State::Off;
}

State state() { return st; }

String ip() {
  return st == State::Connected ? WiFi.localIP().toString() : String();
}

int rssi() { return st == State::Connected ? WiFi.RSSI() : 0; }

const char *stateName() {
  switch (st) {
    case State::Off: return "off";
    case State::Connecting: return "connecting";
    case State::Connected: return "online";
    case State::Failed: return "failed";
  }
  return "?";
}

}  // namespace wifi_mgr
