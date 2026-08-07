#include "hw/wifi_mgr.h"

#include "hw/dlog.h"
#include <WiFi.h>

#include <algorithm>
#include <vector>

namespace wifi_mgr {
namespace {

State st = State::Off;
uint32_t started = 0;
String wantSsid;

// Saved networks + auto-connect walker.
constexpr int kMaxSaved = 4;
String savedSsid[kMaxSaved];
String savedPass[kMaxSaved];
int savedCount = 0;
enum class AutoPhase : uint8_t { Idle, Scan, Join };
AutoPhase autoPhase = AutoPhase::Idle;
bool autoMode = false;
int candOrder[kMaxSaved];
int candCount = 0;
int candIdx = 0;
uint32_t autoRetryAt = 0;

bool scanInFlight = false;
bool scanDone = false;
bool scanRetried = false;
uint32_t scanStartedAt = 0;
uint32_t scanRetryAt = 0;
std::vector<ScanItem> scanResults;
const ScanItem kEmptyItem{};

// Our own deadline: Arduino's scanComplete() reports WIFI_SCAN_FAILED after
// a hard 6 s watchdog (max_ms_per_chan × 20) even though the radio is still
// sweeping — the field log showed FAILED at exactly 6006 ms and the late
// results surfacing right after. So: outlast their timeout, and only retry
// with real spacing.
constexpr uint32_t kScanDeadlineMs = 12000;

void finishScan(int found) {
  scanInFlight = false;
  scanDone = true;
  WiFi.setSleep(true);
  dlog::logf("[wifi] scan done: %d networks", found);
}

void beginScanAttempt() {
  scanStartedAt = millis();
  scanRetryAt = 0;
  // 150 ms per channel: full sweep ≈ 2.1 s, comfortably under the 3 s
  // watchdog this dwell implies — the default 300 ms sweep could not finish
  // inside its own 6 s limit on busy air.
  WiFi.scanNetworks(true /*async*/, false, false, 150);
}

void collectScan() {
  // A retry that fires while the old sweep is still active is a silent
  // no-op — space it out instead.
  if (scanRetryAt && millis() >= scanRetryAt) {
    dlog::logf("[wifi] scan retrying now");
    WiFi.scanDelete();
    beginScanAttempt();
    return;
  }

  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_RUNNING) return;
  if (n == WIFI_SCAN_FAILED) {
    if (millis() - scanStartedAt < kScanDeadlineMs) return;  // outlast it
    if (!scanRetried) {
      scanRetried = true;
      scanRetryAt = millis() + 400;
      dlog::logf("[wifi] scan failed — retry queued");
      return;
    }
    scanResults.clear();
    finishScan(-1);
    return;
  }
  if (n < 0) return;
  // Zero APs within ~1.2 s of starting is the aborted-scan artifact, not a
  // real empty neighborhood — retry once before believing it.
  if (n == 0 && millis() - scanStartedAt < 1200 && !scanRetried) {
    scanRetried = true;
    scanRetryAt = millis() + 400;
    dlog::logf("[wifi] early empty result — retry queued");
    return;
  }
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
  WiFi.setSleep(true);  // modem sleep back on after the scan window
  dlog::logf("[wifi] scan done: %d networks", (int)scanResults.size());
}

}  // namespace

void begin() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);  // power friendly on battery
  st = State::Off;
}

void setSaved(const String *ssids, const String *passes, int count) {
  savedCount = count > kMaxSaved ? kMaxSaved : (count < 0 ? 0 : count);
  for (int i = 0; i < savedCount; i++) {
    savedSsid[i] = ssids[i];
    savedPass[i] = passes[i];
  }
}

namespace {
void joinCandidate() {
  if (candIdx >= candCount) {
    st = State::Failed;
    autoPhase = AutoPhase::Idle;
    autoRetryAt = millis() + 60000;
    dlog::logf("[wifi] auto-connect: no saved network reachable");
    return;
  }
  int i = candOrder[candIdx];
  wantSsid = savedSsid[i];
  st = State::Connecting;
  autoPhase = AutoPhase::Join;
  // Kill the previous in-flight attempt first: WiFi.begin() while the stack
  // is still retrying the old SSID gets stomped by those retries, and the
  // walker never actually reaches the next network. Auto-reconnect stays off
  // during the walk for the same reason.
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(false);
  delay(100);
  WiFi.begin(savedSsid[i].c_str(), savedPass[i].c_str());
  started = millis();
  dlog::logf("[wifi] auto-connect trying %s (%d/%d)", savedSsid[i].c_str(),
             candIdx + 1, candCount);
}
}  // namespace

void autoConnect() {
  if (savedCount == 0) return;
  autoMode = true;
  if (savedCount == 1) {
    // One network: skip the scan entirely.
    candOrder[0] = 0;
    candCount = 1;
    candIdx = 0;
    joinCandidate();
    return;
  }
  autoPhase = AutoPhase::Scan;
  startScan();
  st = State::Connecting;  // startScan resets state; scanning IS connecting here
}

void startScan() {
  scanDone = false;
  scanInFlight = true;
  scanRetried = false;
  scanResults.clear();
  // Scans while associated come back empty/failed on this stack — drop the
  // connection first (setup context; ui reconnects on exit/join). Kill
  // auto-reconnect too: the stack otherwise starts rejoining the AP the
  // moment we disconnect, and a scan during (re)connection FAILS → the
  // "zero SSIDs found" symptom.
  WiFi.setAutoReconnect(false);
  if (st == State::Connected || st == State::Connecting) {
    WiFi.disconnect(false);
    st = State::Off;
    delay(150);
  }
  WiFi.setSleep(false);
  WiFi.scanNetworks(true /*async*/);
  dlog::logf("[wifi] scan started (disconnected, no autoreconnect)");
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
  autoPhase = AutoPhase::Idle;
  autoMode = true;  // fall back to the saved list if this drops later
  wantSsid = ssid;
  st = State::Connecting;
  started = millis();
  WiFi.disconnect(true, true);
  delay(50);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), pass.c_str());
  dlog::logf("[wifi] connecting to %s", ssid.c_str());
}

void tick() {
  // Auto-connect: scan finished → rank saved networks (seen ones by RSSI,
  // unseen ones — hidden SSIDs — afterwards) and walk the list.
  if (autoPhase == AutoPhase::Scan && scanReady()) {
    candCount = 0;
    for (int r = 0; r < (int)scanResults.size(); r++) {
      for (int i = 0; i < savedCount; i++) {
        if (scanResults[r].ssid == savedSsid[i]) {
          bool dup = false;
          for (int c = 0; c < candCount; c++)
            if (candOrder[c] == i) dup = true;
          if (!dup) candOrder[candCount++] = i;
        }
      }
    }
    for (int i = 0; i < savedCount; i++) {
      bool present = false;
      for (int c = 0; c < candCount; c++)
        if (candOrder[c] == i) present = true;
      if (!present) candOrder[candCount++] = i;
    }
    candIdx = 0;
    joinCandidate();
  }

  if (st == State::Connecting && autoPhase != AutoPhase::Scan) {
    wl_status_t ws = WiFi.status();
    if (ws == WL_CONNECTED) {
      st = State::Connected;
      autoPhase = AutoPhase::Idle;
      WiFi.setAutoReconnect(true);
      dlog::logf("[wifi] connected %s (%s) rssi=%d", wantSsid.c_str(),
                 WiFi.localIP().toString().c_str(), WiFi.RSSI());
    } else {
      // Hard failures end an attempt early: NO_SSID ≈ 3 s instead of the
      // full timeout. The 1.5 s grace keeps the PREVIOUS attempt's stale
      // status from failing a brand-new one.
      bool hardFail = (ws == WL_NO_SSID_AVAIL || ws == WL_CONNECT_FAILED) &&
                      millis() - started > 1500;
      if (hardFail || millis() - started > 15000) {
        if (hardFail)
          dlog::logf("[wifi] %s: %s", wantSsid.c_str(),
                     ws == WL_NO_SSID_AVAIL ? "not found" : "auth failed");
        if (autoPhase == AutoPhase::Join) {
          candIdx++;
          joinCandidate();  // next saved network
        } else {
          st = State::Failed;
          dlog::logf("[wifi] connect failed/timeout");
        }
      }
    }
  } else if (st == State::Connected && WiFi.status() != WL_CONNECTED) {
    st = State::Failed;
    if (autoMode) autoRetryAt = millis() + 5000;
    dlog::logf("[wifi] connection lost");
  }

  // Dropped or exhausted: try the whole list again later.
  if (st == State::Failed && autoMode && autoRetryAt &&
      millis() >= autoRetryAt) {
    autoRetryAt = 0;
    autoConnect();
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
