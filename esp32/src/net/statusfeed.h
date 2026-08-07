#pragma once
// Real-time job status via each daemon's SSE stream (/sse/status).
// Up to AppConfig::kMaxDaemons feeds run in parallel; jobs() returns the
// merged snapshot with each job tagged by its daemon index.

#include <Arduino.h>
#include <vector>

namespace statusfeed {

constexpr int kMaxFeeds = 4;

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
  uint8_t daemon = 0;  // which feed this came from
};

enum class State : uint8_t { Off, Connecting, Live, Failed };

// Reset all feeds, then configure [0, count) individually.
void setCount(int count);
void configure(int idx, const String &apiBase, const String &token);
// Call every loop; non-blocking. Reads stream bytes, reconnects on drop.
void tick();
void stop();

State state(int idx);
// Any Live → Live; else any Connecting → Connecting; else Failed/Off.
State aggregate();
// Bumps every time any feed gets a new snapshot; cheap change detection.
uint32_t generation();
// Merged jobs across every feed (rebuilt lazily on generation change).
const std::vector<JobStat> &jobs();

}  // namespace statusfeed
