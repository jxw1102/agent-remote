#include "net/statusfeed.h"
#include "board_pins.h"
#include "hw/dlog.h"

#include <ArduinoJson.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

namespace statusfeed {
namespace {

// Keepalives come every 15 s; a silent minute means the link is dead.
constexpr uint32_t kSilenceMs = 60000;
constexpr size_t kMaxLine = 12 * 1024;

// Feeds run on their own task: connect() blocks up to 1.5 s per attempt and
// payload parsing is steady work — neither belongs on the LVGL loop.
// cfgMx serializes reconfiguration against the ticking task; dataMx guards
// the jobs vectors + generation counters (short critical sections only).
SemaphoreHandle_t cfgMx = nullptr;
SemaphoreHandle_t dataMx = nullptr;
TaskHandle_t feedTask = nullptr;
// Lock-free pause: pause() only stamps a deadline — the feed task drops the
// connection itself. Taking cfgMx from the UI thread stalled it for however
// long a connect() was holding the lock (the "Web screen lags" report).
volatile uint32_t pauseUntil[kMaxFeeds] = {0, 0, 0};

struct Feed {
  int idx = 0;
  String host;
  String pathPrefix;
  String token;
  uint16_t port = 80;
  bool tls = false;
  bool configured = false;

  WiFiClient plainClient;
  WiFiClientSecure tlsClient;
  WiFiClient *cli = nullptr;

  State st = State::Off;
  uint32_t gen = 0;
  std::vector<JobStat> jobs;

  uint32_t nextConnectAt = 0;
  uint32_t lastByteAt = 0;
  uint8_t failStreak = 0;
  bool headersDone = false;
  String lineBuf;

  uint32_t backoffMs() const {
    uint32_t d = SSE_RECONNECT_MS << (failStreak > 4 ? 4 : failStreak);
    return d > 30000 ? 30000 : d;
  }

  bool parseBase(const String &base) {
    String rest = base;
    tls = false;
    if (rest.startsWith("https://")) {
      tls = true;
      rest = rest.substring(8);
    } else if (rest.startsWith("http://")) {
      rest = rest.substring(7);
    }
    int slash = rest.indexOf('/');
    String hostPort = slash >= 0 ? rest.substring(0, slash) : rest;
    pathPrefix = slash >= 0 ? rest.substring(slash) : "";
    while (pathPrefix.endsWith("/")) pathPrefix.remove(pathPrefix.length() - 1);
    int colon = hostPort.indexOf(':');
    if (colon >= 0) {
      host = hostPort.substring(0, colon);
      port = (uint16_t)hostPort.substring(colon + 1).toInt();
    } else {
      host = hostPort;
      port = tls ? 443 : 80;
    }
    return host.length() > 0 && port > 0;
  }

  void disconnect(uint32_t retryDelay) {
    if (cli) cli->stop();
    headersDone = false;
    lineBuf = "";
    if (st != State::Off) st = State::Failed;
    nextConnectAt = millis() + retryDelay;
  }

  void reset() {
    if (cli) cli->stop();
    cli = nullptr;
    configured = false;
    st = State::Off;
    jobs.clear();
    gen++;
  }

  void connectNow() {
    if (tls) {
      tlsClient.setInsecure();
      cli = &tlsClient;
    } else {
      cli = &plainClient;
    }
    st = State::Connecting;
    headersDone = false;
    lineBuf = "";
    if (!cli->connect(host.c_str(), port, 1500)) {
      if (failStreak < 250) failStreak++;
      dlog::logf("[feed%d] connect failed (retry in %u ms)", idx,
                 (unsigned)backoffMs());
      disconnect(backoffMs());
      return;
    }
    failStreak = 0;
    String req = "GET " + pathPrefix + "/sse/status?token=" + token +
                 " HTTP/1.1\r\nHost: " + host +
                 "\r\nAccept: text/event-stream\r\nConnection: keep-alive\r\n\r\n";
    cli->print(req);
    lastByteAt = millis();
    dlog::logf("[feed%d] SSE connecting", idx);
  }

  void applyPayload(const String &json) {
    JsonDocument doc;
    if (deserializeJson(doc, json)) {
      dlog::logf("[feed%d] bad json (skipped)", idx);
      return;
    }
    JsonArray active = doc["active"].as<JsonArray>();
    std::vector<JobStat> next;
    for (JsonObject a : active) {
      JobStat j;
      j.daemon = (uint8_t)idx;
      j.jobId = (const char *)(a["job_id"] | "");
      j.provider = (const char *)(a["provider"] | "");
      j.sessionId = (const char *)(a["session_id"] | "");
      if (j.sessionId.isEmpty())
        j.sessionId = (const char *)(a["new_session_id"] | "");
      j.prompt = (const char *)(a["prompt"] | "");
      j.tool = (const char *)(a["tool"] | "");
      j.toolDetail = (const char *)(a["tool_detail"] | "");
      j.phase = (const char *)(a["phase"] | "");
      j.phaseDetail = (const char *)(a["phase_detail"] | "");
      j.elapsedS = a["elapsed_s"] | 0;
      j.queued = a["queued_count"] | 0;
      j.pendingPermission = a["pending_permission"] | false;
      j.pendingQuestion = a["pending_question"] | false;
      next.push_back(j);
      if (next.size() >= 8) break;  // screen fits 4; keep memory bounded
    }
    xSemaphoreTake(dataMx, portMAX_DELAY);
    jobs.swap(next);
    gen++;
    xSemaphoreGive(dataMx);
  }

  void handleLine(const String &line) {
    if (!line.startsWith("data: ")) return;  // ":" keepalive or event name
    applyPayload(line.substring(6));
  }

  void tick() {
    if (!configured || token.isEmpty()) return;
    if (WiFi.status() != WL_CONNECTED) {
      if (st == State::Live || st == State::Connecting)
        disconnect(SSE_RECONNECT_MS);
      return;
    }

    if (!cli || !cli->connected()) {
      if (millis() >= nextConnectAt) connectNow();
      return;
    }

    // Drain available bytes; bounded per tick so the UI stays responsive.
    int budget = 4096;
    while (budget-- > 0 && cli->available()) {
      char c = (char)cli->read();
      lastByteAt = millis();
      if (c == '\n') {
        if (!headersDone) {
          String h = lineBuf;
          h.trim();
          if (h.isEmpty()) {
            headersDone = true;
            st = State::Live;
            dlog::logf("[feed%d] SSE live", idx);
          } else if (h.startsWith("HTTP/") && h.indexOf(" 200") < 0) {
            dlog::logf("[feed%d] SSE rejected: %s", idx, h.c_str());
            if (failStreak < 250) failStreak++;
            disconnect(SSE_RECONNECT_MS * 4);
            return;
          }
        } else {
          String l = lineBuf;
          if (l.endsWith("\r")) l.remove(l.length() - 1);
          handleLine(l);
        }
        lineBuf = "";
      } else if (c != '\r' || !headersDone) {
        if (lineBuf.length() < kMaxLine) lineBuf += c;
      }
    }

    if (millis() - lastByteAt > kSilenceMs) {
      dlog::logf("[feed%d] silent too long — reconnecting", idx);
      disconnect(SSE_RECONNECT_MS);
    }
  }
};

Feed feeds[kMaxFeeds];
int feedCount = 0;
std::vector<JobStat> mergedJobs;
uint32_t mergedGen = 0;

void feedTaskFn(void *) {
  for (;;) {
    for (int i = 0; i < feedCount; i++) {
      xSemaphoreTake(cfgMx, portMAX_DELAY);
      uint32_t until = pauseUntil[i];
      if (until && millis() < until) {
        // Paused: drop the connection (frees socket/TLS) and sit out.
        if (feeds[i].cli && feeds[i].cli->connected())
          feeds[i].disconnect(until - millis());
      } else {
        if (until) {
          pauseUntil[i] = 0;
          feeds[i].nextConnectAt = 0;  // resume immediately
        }
        feeds[i].tick();
      }
      xSemaphoreGive(cfgMx);
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void ensureTask() {
  if (feedTask) return;
  cfgMx = xSemaphoreCreateMutex();
  dataMx = xSemaphoreCreateMutex();
  xTaskCreate(feedTaskFn, "statusfeed", 12288, NULL, 1, &feedTask);
}

}  // namespace

void setCount(int count) {
  ensureTask();
  if (count < 0) count = 0;
  if (count > kMaxFeeds) count = kMaxFeeds;
  xSemaphoreTake(cfgMx, portMAX_DELAY);
  for (int i = count; i < kMaxFeeds; i++) feeds[i].reset();
  feedCount = count;
  xSemaphoreGive(cfgMx);
}

void configure(int idx, const String &apiBase, const String &token) {
  if (idx < 0 || idx >= kMaxFeeds) return;
  ensureTask();
  xSemaphoreTake(cfgMx, portMAX_DELAY);
  Feed &f = feeds[idx];
  f.reset();
  f.idx = idx;
  f.token = token;
  f.configured = apiBase.length() > 0 && f.parseBase(apiBase);
  f.nextConnectAt = 0;
  if (!f.configured && apiBase.length())
    dlog::logf("[feed%d] bad base url", idx);
  xSemaphoreGive(cfgMx);
}

void pause(int idx, uint32_t ms) {
  if (!feedTask || idx < 0 || idx >= feedCount) return;
  pauseUntil[idx] = ms <= 1 ? 1 : millis() + ms;  // 1 = resume next tick
}

void stop() {
  if (!feedTask) return;
  xSemaphoreTake(cfgMx, portMAX_DELAY);
  for (int i = 0; i < kMaxFeeds; i++) feeds[i].reset();
  xSemaphoreGive(cfgMx);
}

void tick() {
  // Feeds tick on their own task now; kept for call-site compatibility.
}

State state(int idx) {
  if (idx < 0 || idx >= feedCount) return State::Off;
  return feeds[idx].st;
}

State aggregate() {
  bool anyLive = false, anyConn = false, anyFail = false;
  for (int i = 0; i < feedCount; i++) {
    if (feeds[i].st == State::Live) anyLive = true;
    else if (feeds[i].st == State::Connecting) anyConn = true;
    else if (feeds[i].st == State::Failed) anyFail = true;
  }
  if (anyLive && !anyConn && !anyFail) return State::Live;
  if (anyLive) return State::Live;  // partial trouble still shows live data
  if (anyConn) return State::Connecting;
  if (anyFail) return State::Failed;
  return State::Off;
}

uint32_t generation() {
  if (!dataMx) return 0;
  xSemaphoreTake(dataMx, portMAX_DELAY);
  uint32_t g = 0;
  for (int i = 0; i < feedCount; i++) g += feeds[i].gen;
  xSemaphoreGive(dataMx);
  return g;
}

const std::vector<JobStat> &jobs() {
  static const std::vector<JobStat> kEmpty;
  if (!dataMx) return kEmpty;
  xSemaphoreTake(dataMx, portMAX_DELAY);
  uint32_t g = 0;
  for (int i = 0; i < feedCount; i++) g += feeds[i].gen;
  if (g != mergedGen) {
    mergedGen = g;
    mergedJobs.clear();
    for (int i = 0; i < feedCount; i++)
      for (const auto &j : feeds[i].jobs) mergedJobs.push_back(j);
  }
  xSemaphoreGive(dataMx);
  return mergedJobs;
}

}  // namespace statusfeed
