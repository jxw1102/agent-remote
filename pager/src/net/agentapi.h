#pragma once
// Minimal Agent Remote HTTP client for the pager (ESP32).

#include <Arduino.h>
#include <vector>

struct SessionRow {
  String id;
  String title;
  String cwd;
  String provider;
  bool working = false;
};

struct StatusSnap {
  bool ok = false;
  int working = 0;
  bool needsYou = false;   // permission / question
  String phase;
  String tool;
  String error;
};

namespace agentapi {

void configure(const String &baseUrl, const String &token);
bool ping(String *versionOut = nullptr, String *errOut = nullptr);
bool fetchSessions(std::vector<SessionRow> *out, String *errOut = nullptr);
bool sendPrompt(const String &sessionId, const String &prompt, String *errOut = nullptr);
bool newSession(const String &cwd, const String &prompt, const String &provider,
                String *errOut = nullptr);
// Lightweight status: GET /api/sessions?limit=20 and scan working flags,
// plus optional /sse is too heavy — we poll.
StatusSnap pollStatus();

// Last status signature for chime de-dup.
String statusSignature(const StatusSnap &s);

}  // namespace agentapi
