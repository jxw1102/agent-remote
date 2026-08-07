#include "hw/sdconfig.h"
#include "board_pins.h"
#include "hw/dlog.h"
#include "app_config.h"

#include <ArduinoJson.h>
#include <SD.h>
#include <SPI.h>
#include <esp_random.h>
#include <esp_system.h>
#include <mbedtls/gcm.h>
#include <mbedtls/sha256.h>

#include <memory>

namespace sdconfig {
namespace {

bool mounted = false;
// Encrypted config (AES-256-GCM, device-unique key). The plaintext
// agentremote.json is accepted once as a provisioning file, then replaced.
const char *kEncPath = "/agentremote.cfg";
const char *kPlainPath = "/agentremote.json";
const uint8_t kMagic[5] = {'A', 'G', 'R', 'M', '1'};

// Key = SHA-256(eFuse base MAC || salt). The MAC is silicon, so the key
// survives erase + reflash — this device always reads its own card, while
// the file is opaque to any other device or a PC.
void deviceKey(uint8_t out[32]) {
  uint8_t buf[6 + 24];
  esp_efuse_mac_get_default(buf);
  memcpy(buf + 6, "agentremote-sd-config-v1", 24);
  mbedtls_sha256(buf, sizeof(buf), out, 0);
}

bool writeEncrypted(const String &plain) {
  uint8_t key[32];
  deviceKey(key);
  uint8_t iv[12];
  esp_fill_random(iv, sizeof(iv));
  uint8_t tag[16];
  std::unique_ptr<uint8_t[]> ct(new (std::nothrow) uint8_t[plain.length()]);
  if (!ct) return false;

  mbedtls_gcm_context g;
  mbedtls_gcm_init(&g);
  mbedtls_gcm_setkey(&g, MBEDTLS_CIPHER_ID_AES, key, 256);
  int rc = mbedtls_gcm_crypt_and_tag(
      &g, MBEDTLS_GCM_ENCRYPT, plain.length(), iv, sizeof(iv), nullptr, 0,
      (const uint8_t *)plain.c_str(), ct.get(), sizeof(tag), tag);
  mbedtls_gcm_free(&g);
  if (rc != 0) return false;

  SD.remove(kEncPath);
  File f = SD.open(kEncPath, FILE_WRITE);
  if (!f) return false;
  f.write(kMagic, sizeof(kMagic));
  f.write(iv, sizeof(iv));
  f.write(tag, sizeof(tag));
  f.write(ct.get(), plain.length());
  f.close();
  return true;
}

// Returns the decrypted JSON, or "" if absent/corrupt/foreign-device.
String readEncrypted() {
  File f = SD.open(kEncPath, FILE_READ);
  if (!f) return String();
  size_t total = f.size();
  const size_t head = sizeof(kMagic) + 12 + 16;
  if (total <= head) {
    f.close();
    return String();
  }
  uint8_t magic[5], iv[12], tag[16];
  f.read(magic, sizeof(magic));
  f.read(iv, sizeof(iv));
  f.read(tag, sizeof(tag));
  size_t n = total - head;
  std::unique_ptr<uint8_t[]> ct(new (std::nothrow) uint8_t[n]);
  std::unique_ptr<uint8_t[]> pt(new (std::nothrow) uint8_t[n + 1]);
  if (!ct || !pt || memcmp(magic, kMagic, sizeof(kMagic)) != 0) {
    f.close();
    return String();
  }
  f.read(ct.get(), n);
  f.close();

  uint8_t key[32];
  deviceKey(key);
  mbedtls_gcm_context g;
  mbedtls_gcm_init(&g);
  mbedtls_gcm_setkey(&g, MBEDTLS_CIPHER_ID_AES, key, 256);
  int rc = mbedtls_gcm_auth_decrypt(&g, n, iv, sizeof(iv), nullptr, 0, tag,
                                    sizeof(tag), ct.get(), pt.get());
  mbedtls_gcm_free(&g);
  if (rc != 0) {
    dlog::logf("[sd] %s: auth failed (different device?)", kEncPath);
    return String();
  }
  pt[n] = 0;
  return String((const char *)pt.get());
}

bool parseInto(const String &body, AppConfig *cfg) {
  JsonDocument doc;
  if (deserializeJson(doc, body)) return false;
  cfg->wifiCount = 0;
  for (JsonObject w : doc["wifi"].as<JsonArray>()) {
    if (cfg->wifiCount >= AppConfig::kMaxWifi) break;
    cfg->wifis[cfg->wifiCount].ssid = (const char *)(w["ssid"] | "");
    cfg->wifis[cfg->wifiCount].pass = (const char *)(w["pass"] | "");
    if (cfg->wifis[cfg->wifiCount].ssid.length()) cfg->wifiCount++;
  }
  cfg->daemonCount = 0;
  for (JsonObject d : doc["daemons"].as<JsonArray>()) {
    if (cfg->daemonCount >= AppConfig::kMaxDaemons) break;
    cfg->daemons[cfg->daemonCount].url = (const char *)(d["url"] | "");
    cfg->daemons[cfg->daemonCount].token = (const char *)(d["token"] | "");
    cfg->daemons[cfg->daemonCount].enabled = d["enabled"] | true;
    if (cfg->daemons[cfg->daemonCount].url.length()) cfg->daemonCount++;
  }
  cfg->soundCues = doc["sound"] | cfg->soundCues;
  cfg->hapticCues = doc["haptic"] | cfg->hapticCues;
  cfg->volume = doc["volume"] | cfg->volume;
  cfg->bleMode = doc["ble"] | cfg->bleMode;
  cfg->backlight = doc["backlight"] | cfg->backlight;
  return cfg->wifiCount > 0 || cfg->daemonCount > 0;
}

}  // namespace

void begin() {
  // Same pins as the display; LovyanGFX is configured bus_shared for this.
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SD_CS);
  mounted = SD.begin(PIN_SD_CS, SPI, 10000000);
  dlog::logf("[sd] %s",
             mounted ? "card mounted" : "no card (config stays in NVS only)");
}

bool present() { return mounted; }

bool importConfig(AppConfig *cfg) {
  if (!mounted || !cfg) return false;
  // Encrypted backup first (normal reflash path).
  String body = readEncrypted();
  if (body.length() && parseInto(body, cfg)) {
    dlog::logf("[sd] imported %d wifi + %d daemons (encrypted)",
               cfg->wifiCount, cfg->daemonCount);
    return true;
  }
  // Plaintext provisioning file (written from a PC). It is re-saved
  // encrypted and deleted on the next exportConfig so secrets don't linger.
  File f = SD.open(kPlainPath, FILE_READ);
  if (!f) return false;
  String plain = f.readString();
  f.close();
  if (!parseInto(plain, cfg)) {
    dlog::logf("[sd] %s: bad json", kPlainPath);
    return false;
  }
  dlog::logf("[sd] provisioned %d wifi + %d daemons from %s", cfg->wifiCount,
             cfg->daemonCount, kPlainPath);
  exportConfig(*cfg);  // writes encrypted + removes the plaintext file
  return true;
}

bool exportConfig(const AppConfig &cfg) {
  if (!mounted) return false;
  JsonDocument doc;
  JsonArray wifi = doc["wifi"].to<JsonArray>();
  for (int i = 0; i < cfg.wifiCount; i++) {
    JsonObject w = wifi.add<JsonObject>();
    w["ssid"] = cfg.wifis[i].ssid;
    w["pass"] = cfg.wifis[i].pass;
  }
  JsonArray daemons = doc["daemons"].to<JsonArray>();
  for (int i = 0; i < cfg.daemonCount; i++) {
    JsonObject d = daemons.add<JsonObject>();
    d["url"] = cfg.daemons[i].url;
    d["token"] = cfg.daemons[i].token;
    d["enabled"] = cfg.daemons[i].enabled;
  }
  doc["sound"] = cfg.soundCues;
  doc["haptic"] = cfg.hapticCues;
  doc["volume"] = cfg.volume;
  doc["ble"] = cfg.bleMode;
  doc["backlight"] = cfg.backlight;

  String plain;
  serializeJson(doc, plain);
  if (!writeEncrypted(plain)) {
    dlog::logf("[sd] encrypted write failed");
    return false;
  }
  // Provisioning plaintext must not outlive its first import.
  if (SD.exists(kPlainPath)) SD.remove(kPlainPath);
  return true;
}

}  // namespace sdconfig
