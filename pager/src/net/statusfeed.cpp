#include "net/statusfeed.h"
#include "board_pins.h"

#include <ArduinoJson.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

namespace statusfeed {
namespace {

String g_host;
String g_pathPrefix;  // e.g. "" or "/claude"
String g_token;
uint16_t g_port = 80;
bool g_tls = false;
bool g_configured = false;

WiFiClient plainClient;
WiFiClientSecure tlsClient;
WiFiClient *cli = nullptr;

State g_state = State::Off;
uint32_t g_gen = 0;
std::vector<JobStat> g_jobs;

uint32_t nextConnectAt = 0;
uint32_t lastByteAt = 0;
uint8_t failStreak = 0;
bool headersDone = false;
String lineBuf;

uint32_t backoffMs() {
  // 3 s, 6 s, 12 s, 24 s, then 30 s — a dead daemon must not keep the UI
  // loop stuck inside blocking connect() calls.
  uint32_t d = SSE_RECONNECT_MS << (failStreak > 4 ? 4 : failStreak);
  return d > 30000 ? 30000 : d;
}

// Keepalives come every 15 s; a silent minute means the link is dead.
constexpr uint32_t kSilenceMs = 60000;
constexpr size_t kMaxLine = 12 * 1024;

bool parseBase(const String &base) {
  String rest = base;
  g_tls = false;
  if (rest.startsWith("https://")) {
    g_tls = true;
    rest = rest.substring(8);
  } else if (rest.startsWith("http://")) {
    rest = rest.substring(7);
  }
  int slash = rest.indexOf('/');
  String hostPort = slash >= 0 ? rest.substring(0, slash) : rest;
  g_pathPrefix = slash >= 0 ? rest.substring(slash) : "";
  while (g_pathPrefix.endsWith("/")) g_pathPrefix.remove(g_pathPrefix.length() - 1);
  int colon = hostPort.indexOf(':');
  if (colon >= 0) {
    g_host = hostPort.substring(0, colon);
    g_port = (uint16_t)hostPort.substring(colon + 1).toInt();
  } else {
    g_host = hostPort;
    g_port = g_tls ? 443 : 80;
  }
  return g_host.length() > 0 && g_port > 0;
}

void disconnect(uint32_t retryDelay) {
  if (cli) cli->stop();
  headersDone = false;
  lineBuf = "";
  if (g_state != State::Off) g_state = State::Failed;
  nextConnectAt = millis() + retryDelay;
}

void connectNow() {
  if (g_tls) {
    tlsClient.setInsecure();  // LAN / self-signed, same policy as agentapi
    cli = &tlsClient;
  } else {
    cli = &plainClient;
  }
  g_state = State::Connecting;
  headersDone = false;
  lineBuf = "";
  if (!cli->connect(g_host.c_str(), g_port, 1500)) {
    if (failStreak < 250) failStreak++;
    Serial.printf("[feed] connect failed (retry in %u ms)\n",
                  (unsigned)backoffMs());
    disconnect(backoffMs());
    return;
  }
  failStreak = 0;
  String req = "GET " + g_pathPrefix + "/sse/status?token=" + g_token +
               " HTTP/1.1\r\nHost: " + g_host +
               "\r\nAccept: text/event-stream\r\nConnection: keep-alive\r\n\r\n";
  cli->print(req);
  lastByteAt = millis();
  Serial.println("[feed] SSE connecting");
}

void applyPayload(const String &json) {
  // Payloads carry ≤120-char prompts per job; filter to what we render.
  JsonDocument doc;
  if (deserializeJson(doc, json)) {
    Serial.println("[feed] bad json (skipped)");
    return;
  }
  JsonArray active = doc["active"].as<JsonArray>();
  std::vector<JobStat> next;
  for (JsonObject a : active) {
    JobStat j;
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
  g_jobs.swap(next);
  g_gen++;
}

void handleLine(const String &line) {
  if (!line.startsWith("data: ")) return;  // ":" keepalive or event name
  applyPayload(line.substring(6));
}

}  // namespace

void configure(const String &apiBase, const String &token) {
  g_token = token;
  g_configured = parseBase(apiBase);
  g_state = State::Off;
  nextConnectAt = 0;
  if (cli) cli->stop();
  if (!g_configured) Serial.println("[feed] bad base url");
}

void stop() {
  if (cli) cli->stop();
  g_state = State::Off;
  g_jobs.clear();
  g_gen++;
}

State state() { return g_state; }
uint32_t generation() { return g_gen; }
const std::vector<JobStat> &jobs() { return g_jobs; }

void tick() {
  if (!g_configured || g_token.isEmpty()) return;
  if (WiFi.status() != WL_CONNECTED) {
    if (g_state == State::Live || g_state == State::Connecting)
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
          g_state = State::Live;
          Serial.println("[feed] SSE live");
        } else if (h.startsWith("HTTP/") && h.indexOf(" 200") < 0) {
          Serial.printf("[feed] SSE rejected: %s\n", h.c_str());
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
    Serial.println("[feed] silent too long — reconnecting");
    disconnect(SSE_RECONNECT_MS);
  }
}

}  // namespace statusfeed
