#include "hw/wifi_mgr.h"

#include <WiFi.h>

namespace wifi_mgr {
namespace {

State st = State::Off;
uint32_t started = 0;
String wantSsid;

}  // namespace

void begin() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);  // power friendly on battery
  st = State::Off;
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
