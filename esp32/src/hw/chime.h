#pragma once
// Progress cues shared with BB10 / Android / web Agent Remote clients.
// Pitches from flipper-claude-buddy: Status / Done / Error / Attention.

#include <Arduino.h>

namespace chime {

enum class Cue : uint8_t {
  Status,     // new phase / tool
  Done,       // turn finished
  Error,      // turn failed
  Attention,  // permission / question
  Tick,       // subtle haptic-only blip (tool call changed)
};

void begin();
// Enable amp, play cue, disable amp. Non-blocking best-effort.
void play(Cue cue, bool sound, bool haptic);
void setEnabled(bool sound, bool haptic);
// Chime volume 0–100 (codec DAC volume).
void setVolume(uint8_t pct);

}  // namespace chime
