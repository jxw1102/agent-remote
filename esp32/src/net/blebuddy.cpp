#include "net/blebuddy.h"
#include "hw/dlog.h"
#include "net/timesync.h"

#include <ArduinoJson.h>
#include <NimBLEDevice.h>

namespace blebuddy {
namespace {

const char *kSvcUuid = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
const char *kRxUuid = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";  // desktop→us
const char *kTxUuid = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";  // us→desktop

bool g_active = false;
bool g_connected = false;
uint32_t g_gen = 0;
Snap g_snap;
String g_owner;
uint32_t g_lastRxAt = 0;

NimBLEServer *g_server = nullptr;
NimBLECharacteristic *g_tx = nullptr;

// RX bytes land on the NimBLE host task — hand them to loop() untouched.
portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;
String g_rxBuf;

class ServerCb : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *) override {
    g_connected = true;
    dlog::logf("[ble] desktop connected");
  }
  void onDisconnect(NimBLEServer *) override {
    g_connected = false;
    g_snap = Snap{};
    g_gen++;
    dlog::logf("[ble] disconnected — advertising again");
    NimBLEDevice::startAdvertising();
  }
};

class RxCb : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *ch) override {
    std::string v = ch->getValue();
    portENTER_CRITICAL(&g_mux);
    if (g_rxBuf.length() < 24 * 1024)
      g_rxBuf.concat(v.c_str(), v.length());
    portEXIT_CRITICAL(&g_mux);
  }
};

void parseLine(const String &line) {
  if (line.length() < 2) return;
  JsonDocument doc;
  if (deserializeJson(doc, line)) {
    dlog::logf("[ble] bad json (%d bytes)", (int)line.length());
    return;
  }
  if (doc["cmd"] == "owner") {
    g_owner = (const char *)(doc["name"] | "");
    dlog::logf("[ble] owner: %s", g_owner.c_str());
    return;
  }
  if (!doc["time"].isNull()) {
    JsonArray t = doc["time"].as<JsonArray>();
    if (t.size() >= 2) timesync::setFromBle(t[0] | 0, t[1] | 0);
    return;
  }
  if (doc["evt"] == "turn") {
    // Turn events ride alongside snapshots; snapshot diffing does the
    // chimes, so just surface the first text block as a ticker line.
    for (JsonObject c : doc["content"].as<JsonArray>()) {
      if (c["type"] == "text") {
        String t = (const char *)(c["text"] | "");
        t.replace("\n", " ");
        g_snap.msg = t.substring(0, 96);
        g_gen++;
        break;
      }
    }
    return;
  }
  if (doc["total"].isNull()) return;  // not a heartbeat

  Snap s;
  s.total = doc["total"] | 0;
  s.running = doc["running"] | 0;
  s.waiting = doc["waiting"] | 0;
  s.msg = (const char *)(doc["msg"] | "");
  s.tokensToday = doc["tokens_today"] | 0;
  int n = 0;
  for (JsonVariant e : doc["entries"].as<JsonArray>()) {
    if (n >= 3) break;
    s.entries[n++] = (const char *)(e | "");
  }
  s.entryCount = n;
  JsonObject p = doc["prompt"].as<JsonObject>();
  if (!p.isNull()) {
    s.hasPrompt = true;
    s.promptId = (const char *)(p["id"] | "");
    s.promptTool = (const char *)(p["tool"] | "");
    s.promptHint = (const char *)(p["hint"] | "");
  }
  g_snap = s;
  g_gen++;
}

}  // namespace

void begin() {
  if (g_active) return;
  String name = "Claude Pager";
  NimBLEDevice::init(name.c_str());
  NimBLEDevice::setMTU(185);
  // Suffix the name with MAC bytes so multiple devices stay distinguishable
  // in the desktop picker (per the protocol reference).
  std::string mac = NimBLEDevice::getAddress().toString();
  name += " " + String(mac.substr(mac.length() - 5, 2).c_str()) +
          String(mac.substr(mac.length() - 2).c_str());
  NimBLEDevice::deinit(true);
  NimBLEDevice::init(name.c_str());
  NimBLEDevice::setMTU(185);

  g_server = NimBLEDevice::createServer();
  static ServerCb serverCb;
  g_server->setCallbacks(&serverCb);

  NimBLEService *svc = g_server->createService(kSvcUuid);
  NimBLECharacteristic *rx = svc->createCharacteristic(
      kRxUuid, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  static RxCb rxCb;
  rx->setCallbacks(&rxCb);
  g_tx = svc->createCharacteristic(kTxUuid, NIMBLE_PROPERTY::NOTIFY);
  svc->start();

  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(svc->getUUID());
  adv->setScanResponse(true);
  adv->start();

  g_active = true;
  g_lastRxAt = millis();
  dlog::logf("[ble] advertising as \"%s\"", name.c_str());
}

void stop() {
  if (!g_active) return;
  NimBLEDevice::getAdvertising()->stop();
  NimBLEDevice::deinit(true);
  g_active = false;
  g_connected = false;
  g_server = nullptr;
  g_tx = nullptr;
  g_snap = Snap{};
  g_gen++;
  dlog::logf("[ble] stopped");
}

void tick() {
  if (!g_active) return;
  String pending;
  portENTER_CRITICAL(&g_mux);
  if (g_rxBuf.length()) {
    pending = g_rxBuf;
    g_rxBuf = "";
  }
  portEXIT_CRITICAL(&g_mux);
  if (pending.isEmpty()) return;
  g_lastRxAt = millis();

  static String lineBuf;
  for (size_t i = 0; i < pending.length(); i++) {
    char c = pending[i];
    if (c == '\n') {
      parseLine(lineBuf);
      lineBuf = "";
    } else if (lineBuf.length() < 8 * 1024) {
      lineBuf += c;
    }
  }
}

bool active() { return g_active; }
bool connected() { return g_connected; }
const String &owner() { return g_owner; }
uint32_t generation() { return g_gen; }
const Snap &snap() { return g_snap; }

void sendPermission(const String &id, bool allow) {
  if (!g_tx || !g_connected || id.isEmpty()) return;
  String line = String("{\"cmd\":\"permission\",\"id\":\"") + id +
                "\",\"decision\":\"" + (allow ? "once" : "deny") + "\"}\n";
  g_tx->setValue((uint8_t *)line.c_str(), line.length());
  g_tx->notify();
  dlog::logf("[ble] permission %s → %s", id.c_str(), allow ? "once" : "deny");
}

}  // namespace blebuddy
