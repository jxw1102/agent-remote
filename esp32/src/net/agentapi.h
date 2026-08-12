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
  uint8_t daemon = 0;   // which configured daemon owns this session
  String lastActive;    // ISO timestamp — lexicographic sort works
  // Focus: is this a project the human enrolled, and what does it want.
  // focusState is "" outside Focus, else one of the four focus states.
  bool focus = false;
  String focusState;
  // Cosmetic: a finished turn is only worth flagging until you have opened it.
  bool focusUnread = false;
};

struct UsageBucket {
  String title;
  String resets;
  String severity;  // normal / warn / …
  int percent = 0;
  String provider;   // claude / grok / codex
  String account;    // email or stable id label
  String accountId;  // stable seat id for multi-host dedup
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

// Register daemon [idx] (0-based); count gates every per-daemon call.
void setDaemonCount(int count);
int daemonCount();
void configure(int idx, const String &baseUrl, const String &token);
bool ping(int daemon, String *versionOut = nullptr, String *errOut = nullptr);
bool fetchSessions(int daemon, std::vector<SessionRow> *out,
                   String *errOut = nullptr);
// Focus rows only (GET /api/focus), most urgent first. The pager shows
// one or two rows, so asking the daemon to filter and rank beats fetching the
// recent list and hoping the forgotten project is in it.
bool fetchFocus(int daemon, std::vector<SessionRow> *out,
                String *errOut = nullptr);
// Short tag for a focus state, or "" when it has none.
const char *focusStateLabel(const String &state);
bool sendPrompt(int daemon, const String &sessionId, const String &prompt,
                String *errOut = nullptr);
bool newSession(int daemon, const String &cwd, const String &prompt,
                const String &provider, String *errOut = nullptr);
// Lightweight fallback status poll for one daemon.
StatusSnap pollStatus(int daemon);

// Last status signature for chime de-dup.
String statusSignature(const StatusSnap &s);

// Upload raw text to POST /api/attachments?name=… → host path (diag logs).
bool uploadText(int daemon, const String &name, const String &text,
                String *pathOut, String *errOut);

// GET /api/usage — subscription buckets across the daemon's providers.
// Each bucket may carry provider/account/accountId for cross-host dedup.
bool fetchUsage(int daemon, std::vector<UsageBucket> *out, String *errOut);

// GET /api/jobs/<id>?since=N — terminal status for end-chime decisions.
// Returns "done" | "error" | "stopped" | "running" | "starting" | "" (unknown).
String fetchJobStatus(int daemon, const String &jobId);

// GET /api/sessions/<id>/tui — live host TUI pane (plain text).
bool fetchTui(int daemon, const String &sessionId, String *textOut,
              bool *attachedOut, String *errOut);

}  // namespace agentapi
