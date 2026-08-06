#include "net/agentapi.h"

#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>

namespace agentapi {
namespace {

String g_base;
String g_token;

String urlJoin(const String &path) {
  String b = g_base;
  while (b.endsWith("/")) b.remove(b.length() - 1);
  if (!path.startsWith("/")) return b + "/" + path;
  return b + path;
}

bool httpGet(const String &path, String *body, int *code, String *err,
             uint32_t timeoutMs = 15000) {
  if (WiFi.status() != WL_CONNECTED) {
    if (err) *err = "wifi down";
    return false;
  }
  HTTPClient http;
  String url = urlJoin(path);
  bool https = url.startsWith("https://");
  if (https) {
    static WiFiClientSecure tls;
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
  http.addHeader("Authorization", "Bearer " + g_token);
  http.addHeader("X-Auth-Token", g_token);
  int c = http.GET();
  if (code) *code = c;
  if (c < 200 || c >= 300) {
    if (err) *err = "HTTP " + String(c);
    http.end();
    return false;
  }
  if (body) *body = http.getString();
  http.end();
  return true;
}

bool httpPostJson(const String &path, const String &json, String *body,
                  String *err) {
  if (WiFi.status() != WL_CONNECTED) {
    if (err) *err = "wifi down";
    return false;
  }
  HTTPClient http;
  String url = urlJoin(path);
  bool https = url.startsWith("https://");
  if (https) {
    static WiFiClientSecure tls;
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
  http.setTimeout(30000);
  http.addHeader("Authorization", "Bearer " + g_token);
  http.addHeader("X-Auth-Token", g_token);
  http.addHeader("Content-Type", "application/json");
  int c = http.POST(json);
  if (c < 200 || c >= 300) {
    if (err) *err = "HTTP " + String(c) + " " + http.getString().substring(0, 80);
    http.end();
    return false;
  }
  if (body) *body = http.getString();
  http.end();
  return true;
}

}  // namespace

void configure(const String &baseUrl, const String &token) {
  g_base = baseUrl;
  g_token = token;
}

bool ping(String *versionOut, String *errOut) {
  String body;
  if (!httpGet("/api/ping", &body, nullptr, errOut, 5000)) return false;
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    if (errOut) *errOut = "bad json";
    return false;
  }
  if (versionOut) *versionOut = doc["version"] | "";
  return doc["ok"] | false;
}

bool fetchSessions(std::vector<SessionRow> *out, String *errOut) {
  if (!out) return false;
  out->clear();
  String body;
  if (!httpGet("/api/sessions?limit=30", &body, nullptr, errOut)) return false;
  JsonDocument doc;
  if (deserializeJson(doc, body)) {
    if (errOut) *errOut = "bad json";
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
    if (r.id.length()) out->push_back(r);
  }
  return true;
}

bool sendPrompt(const String &sessionId, const String &prompt, String *errOut) {
  JsonDocument doc;
  doc["prompt"] = prompt;
  String json;
  serializeJson(doc, json);
  // Prefer continue; fall back to jobs/input paths used by daemons.
  String path = "/api/sessions/" + sessionId + "/continue";
  String body;
  if (httpPostJson(path, json, &body, errOut)) return true;
  // Alternate: some builds use /api/jobs with session
  path = "/api/sessions/" + sessionId + "/prompt";
  return httpPostJson(path, json, &body, errOut);
}

bool newSession(const String &cwd, const String &prompt, const String &provider,
                String *errOut) {
  JsonDocument doc;
  doc["cwd"] = cwd;
  doc["prompt"] = prompt;
  doc["permission_mode"] = "bypassPermissions";
  if (provider.length()) doc["provider"] = provider;
  String json;
  serializeJson(doc, json);
  String body;
  return httpPostJson("/api/sessions/new", json, &body, errOut);
}

StatusSnap pollStatus() {
  StatusSnap snap;
  String body, err;
  if (!httpGet("/api/sessions?limit=40", &body, nullptr, &err, 4000)) {
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

}  // namespace agentapi
