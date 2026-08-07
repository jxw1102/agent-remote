#include "net/agentapi.h"
#include "hw/dlog.h"

#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>

namespace agentapi {
namespace {

constexpr int kMax = 3;
String g_base[kMax];
String g_token[kMax];
int g_count = 0;

// One TLS client for every call, guarded by a mutex: each WiFiClientSecure
// costs ~40 KB of heap during a session, and three of them alongside NimBLE
// exhausted memory — connects then fail with HTTPC_ERROR_CONNECTION_REFUSED
// ("HTTP -1"). Calls also come from two tasks (loop + session fetch).
SemaphoreHandle_t httpMutex() {
  static SemaphoreHandle_t m = xSemaphoreCreateMutex();
  return m;
}
struct HttpLock {
  HttpLock() { xSemaphoreTake(httpMutex(), portMAX_DELAY); }
  ~HttpLock() { xSemaphoreGive(httpMutex()); }
};
WiFiClientSecure &sharedTls() {
  static WiFiClientSecure t;
  return t;
}

String urlJoin(int d, const String &path) {
  String b = (d >= 0 && d < kMax) ? g_base[d] : String();
  while (b.endsWith("/")) b.remove(b.length() - 1);
  if (!path.startsWith("/")) return b + "/" + path;
  return b + path;
}

bool httpGet(int d, const String &path, String *body, int *code, String *err,
             uint32_t timeoutMs = 15000) {
  HttpLock lock;
  if (WiFi.status() != WL_CONNECTED) {
    if (err) *err = "wifi down";
    return false;
  }
  HTTPClient http;
  String url = urlJoin(d, path);
  bool https = url.startsWith("https://");
  if (https) {
    WiFiClientSecure &tls = sharedTls();
    tls.setInsecure();  // LAN / self-signed OK for trusted daemon
    if (!http.begin(tls, url)) {
      if (err) *err = "begin https failed";
      return false;
    }
  } else {
    if (!http.begin(url)) {
      if (err) *err = "begin http failed";
      return false;
    }
  }
  // A dead host must not freeze the UI loop for 15 s — background polls pass
  // a short timeout; user-initiated calls keep the generous default.
  http.setConnectTimeout(3000);
  http.setTimeout(timeoutMs);
  // Server-side close: our lwIP table only has ~10 sockets, and client-side
  // TIME_WAIT from repeated polls exhausted it ("HTTP -1" with live feeds).
  http.setReuse(false);
  http.addHeader("Connection", "close");
  http.addHeader("Authorization", "Bearer " + g_token[d]);
  http.addHeader("X-Auth-Token", g_token[d]);
  int c = http.GET();
  if (code) *code = c;
  if (c < 200 || c >= 300) {
    if (err) *err = c < 0 ? ("HTTP " + String(c) + " " + HTTPClient::errorToString(c))
                          : ("HTTP " + String(c));
    dlog::logf("[api] GET %s -> %d (heap %u)", path.c_str(), c,
               (unsigned)ESP.getFreeHeap());
    http.end();
    return false;
  }
  if (body) *body = http.getString();
  http.end();
  return true;
}

bool httpPostJson(int d, const String &path, const String &json, String *body,
                  String *err) {
  HttpLock lock;
  if (WiFi.status() != WL_CONNECTED) {
    if (err) *err = "wifi down";
    return false;
  }
  HTTPClient http;
  String url = urlJoin(d, path);
  bool https = url.startsWith("https://");
  if (https) {
    WiFiClientSecure &tls = sharedTls();
    tls.setInsecure();
    if (!http.begin(tls, url)) {
      if (err) *err = "begin https failed";
      return false;
    }
  } else {
    if (!http.begin(url)) {
      if (err) *err = "begin http failed";
      return false;
    }
  }
  http.setConnectTimeout(3000);
  http.setTimeout(12000);
  http.setReuse(false);
  http.addHeader("Connection", "close");
  http.addHeader("Authorization", "Bearer " + g_token[d]);
  http.addHeader("X-Auth-Token", g_token[d]);
  http.addHeader("Content-Type", "application/json");
  int c = http.POST(json);
  if (c < 200 || c >= 300) {
    if (err) *err = "HTTP " + String(c) + " " + (c < 0 ? String(HTTPClient::errorToString(c)) : http.getString().substring(0, 80));
    dlog::logf("[api] POST %s -> %d (heap %u)", path.c_str(), c,
               (unsigned)ESP.getFreeHeap());
    http.end();
    return false;
  }
  if (body) *body = http.getString();
  http.end();
  return true;
}

}  // namespace

void setDaemonCount(int count) {
  g_count = count < 0 ? 0 : (count > kMax ? kMax : count);
}

int daemonCount() { return g_count; }

void configure(int idx, const String &baseUrl, const String &token) {
  if (idx < 0 || idx >= kMax) return;
  g_base[idx] = baseUrl;
  g_token[idx] = token;
}

bool ping(int daemon, String *versionOut, String *errOut) {
  String body;
  if (!httpGet(daemon, "/api/ping", &body, nullptr, errOut, 5000)) return false;
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    if (errOut) *errOut = "bad json";
    return false;
  }
  if (versionOut) *versionOut = doc["version"] | "";
  return doc["ok"] | false;
}

bool fetchSessions(int daemon, std::vector<SessionRow> *out, String *errOut) {
  if (!out) return false;
  out->clear();
  // Single-provider daemons omit "provider" on session rows (only the multi
  // root tags them) — the badge fell back to the gray unknown dot. Infer a
  // per-daemon default from /api/ping once and cache it.
  static String defProv[kMax];
  String body;
  // Small on purpose: a 30-session body (~40 KB of JSON) was heavy for the
  // heap that remains beside held TLS sessions; 6 recents per daemon is
  // what the pager's screen can use anyway.
  if (!httpGet(daemon, "/api/sessions?limit=6", &body, nullptr, errOut))
    return false;
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    if (errOut) *errOut = "bad json";
    // Forensics: what actually came back (HTML error page? truncated?).
    String head = body.substring(0, 120);
    head.replace("\n", " ");
    dlog::logf("[api] d%d sessions bad json (%u bytes): %s", daemon,
               (unsigned)body.length(), head.c_str());
    return false;
  }
  JsonArray arr = doc["sessions"].as<JsonArray>();
  if (arr.isNull()) arr = doc.as<JsonArray>();
  for (JsonObject s : arr) {
    SessionRow r;
    r.id = s["id"] | s["session_id"] | "";
    r.title = s["title"] | s["name"] | r.id.substring(0, 8);
    r.cwd = s["cwd"] | s["project"] | "";
    r.provider = s["provider"] | "";
    r.working = s["working"] | s["is_working"] | false;
    r.lastActive = (const char *)(s["last_active"] | "");
    r.daemon = (uint8_t)daemon;
    if (r.id.length()) out->push_back(r);
  }

  bool missing = false;
  for (auto &r : *out)
    if (r.provider.isEmpty()) missing = true;
  if (missing && daemon >= 0 && daemon < kMax) {
    if (defProv[daemon].isEmpty()) {
      String pingBody;
      if (httpGet(daemon, "/api/ping", &pingBody, nullptr, nullptr, 5000)) {
        JsonDocument pd;
        if (!deserializeJson(pd, pingBody)) {
          JsonArray provs = pd["providers"].as<JsonArray>();
          if (provs.size() == 1)
            defProv[daemon] = (const char *)(provs[0] | "");
          if (defProv[daemon].isEmpty())
            defProv[daemon] = (const char *)(pd["provider"] | "");
        }
      }
      if (defProv[daemon].isEmpty()) defProv[daemon] = "?";  // don't re-ping
    }
    if (defProv[daemon] != "?") {
      for (auto &r : *out)
        if (r.provider.isEmpty()) r.provider = defProv[daemon];
    }
  }
  return true;
}

bool sendPrompt(int daemon, const String &sessionId, const String &prompt,
                String *errOut) {
  JsonDocument doc;
  doc["prompt"] = prompt;
  String json;
  serializeJson(doc, json);
  // Prefer continue; fall back to jobs/input paths used by daemons.
  String path = "/api/sessions/" + sessionId + "/continue";
  String body;
  if (httpPostJson(daemon, path, json, &body, errOut)) return true;
  // Alternate: some builds use /api/jobs with session
  path = "/api/sessions/" + sessionId + "/prompt";
  return httpPostJson(daemon, path, json, &body, errOut);
}

bool newSession(int daemon, const String &cwd, const String &prompt,
                const String &provider, String *errOut) {
  JsonDocument doc;
  doc["cwd"] = cwd;
  doc["prompt"] = prompt;
  doc["permission_mode"] = "bypassPermissions";
  if (provider.length()) doc["provider"] = provider;
  String json;
  serializeJson(doc, json);
  String body;
  return httpPostJson(daemon, "/api/sessions/new", json, &body, errOut);
}

StatusSnap pollStatus(int daemon) {
  StatusSnap snap;
  String body, err;
  if (!httpGet(daemon, "/api/sessions?limit=12", &body, nullptr, &err, 4000)) {
    snap.error = err;
    return snap;
  }
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    snap.error = "bad json";
    return snap;
  }
  snap.ok = true;
  JsonArray arr = doc["sessions"].as<JsonArray>();
  if (arr.isNull()) arr = doc.as<JsonArray>();
  for (JsonObject s : arr) {
    bool working = s["working"] | s["is_working"] | false;
    if (working) {
      snap.working++;
      if (snap.phase.isEmpty()) {
        snap.phase = s["phase"] | "working";
        snap.tool = s["tool"] | "";
      }
    }
    if (s["needs_permission"] | s["permission_pending"] | false)
      snap.needsYou = true;
    if (s["needs_answer"] | s["question_pending"] | false)
      snap.needsYou = true;
  }
  // active list from multi status if present
  JsonArray active = doc["active"].as<JsonArray>();
  for (JsonObject a : active) {
    snap.working++;
    String ph = a["phase"] | "";
    if (ph.length()) snap.phase = ph;
    if ((a["pending_permission"] | false) || (a["pending_question"] | false))
      snap.needsYou = true;
  }
  return snap;
}

String statusSignature(const StatusSnap &s) {
  return String(s.working) + "|" + (s.needsYou ? "1" : "0") + "|" + s.phase +
         "|" + s.tool;
}

bool fetchUsage(int daemon, std::vector<UsageBucket> *out, String *errOut) {
  if (!out) return false;
  out->clear();
  String body;
  // Usage scrapes the provider CLIs on the host — give it a longer leash.
  if (!httpGet(daemon, "/api/usage", &body, nullptr, errOut, 20000))
    return false;
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    if (errOut) *errOut = "bad json";
    return false;
  }
  for (JsonObject sec : doc["sections"].as<JsonArray>()) {
    for (JsonObject b : sec["buckets"].as<JsonArray>()) {
      UsageBucket u;
      u.title = (const char *)(b["title"] | "");
      u.resets = (const char *)(b["resets_text"] | "");
      u.severity = (const char *)(b["severity"] | "normal");
      u.percent = b["percent"] | 0;
      out->push_back(u);
      if (out->size() >= 8) return true;
    }
  }
  return doc["ok"] | false;
}

bool fetchTui(int daemon, const String &sessionId, String *textOut,
              bool *attachedOut, String *errOut) {
  String body;
  if (!httpGet(daemon, "/api/sessions/" + sessionId + "/tui", &body, nullptr,
               errOut, 4000)) {
    return false;
  }
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    if (errOut) *errOut = "bad json";
    return false;
  }
  if (attachedOut) *attachedOut = doc["attached"] | false;
  if (textOut) *textOut = (const char *)(doc["text"] | "");
  if (!(doc["attached"] | false) && errOut)
    *errOut = (const char *)(doc["error"] | "no host TUI attached");
  return true;
}

bool uploadText(int daemon, const String &name, const String &text,
                String *pathOut, String *errOut) {
  HttpLock lock;
  if (WiFi.status() != WL_CONNECTED) {
    if (errOut) *errOut = "wifi down";
    return false;
  }
  HTTPClient http;
  String url = urlJoin(daemon, "/api/attachments?name=" + name);
  bool https = url.startsWith("https://");
  if (https) {
    WiFiClientSecure &tls = sharedTls();
    tls.setInsecure();
    if (!http.begin(tls, url)) {
      if (errOut) *errOut = "begin failed";
      return false;
    }
  } else if (!http.begin(url)) {
    if (errOut) *errOut = "begin failed";
    return false;
  }
  http.setConnectTimeout(3000);
  http.setTimeout(15000);
  http.addHeader("X-Auth-Token", g_token[daemon]);
  http.addHeader("Content-Type", "application/octet-stream");
  int c = http.POST((uint8_t *)text.c_str(), text.length());
  if (c < 200 || c >= 300) {
    if (errOut) *errOut = "HTTP " + String(c);
    http.end();
    return false;
  }
  String body = http.getString();
  http.end();
  JsonDocument doc;
  if (!deserializeJson(doc, body) && pathOut) *pathOut = doc["path"] | "";
  return true;
}

}  // namespace agentapi
