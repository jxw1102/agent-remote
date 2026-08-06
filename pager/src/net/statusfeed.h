#pragma once
// Real-time job status via the daemon's SSE stream (/sse/status).
// The beeper runs on this: per-job tool call text, phase, and pending
// permission/question flags, pushed within ~1 s of a change.

#include <Arduino.h>
#include <vector>

namespace statusfeed {

struct JobStat {
  String jobId;
  String provider;   // "claude" / "grok" / "codex" ("" in single mode)
  String sessionId;
  String prompt;     // first 120 chars, identity fallback when no title
  String tool;       // current tool name, e.g. "Bash"
  String toolDetail; // e.g. "git status"
  String phase;      // thinking / tool / writing / waiting / asking …
  String phaseDetail;
  int elapsedS = 0;
  int queued = 0;
  bool pendingPermission = false;
  bool pendingQuestion = false;
};

enum class State : uint8_t { Off, Connecting, Live, Failed };

void configure(const String &apiBase, const String &token);
// Call every loop; non-blocking. Reads stream bytes, reconnects on drop.
void tick();
void stop();

State state();
// Bumps every time a new snapshot arrives; cheap change detection.
uint32_t generation();
const std::vector<JobStat> &jobs();

}  // namespace statusfeed
