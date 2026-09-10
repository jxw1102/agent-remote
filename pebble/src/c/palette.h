#pragma once

#include <pebble.h>
#include <string.h>

#define AR_COLOR_HI GColorWindsorTan
#define AR_COLOR_SUB_HI GColorMelon
#define AR_COLOR_SUB GColorLightGray
#define AR_COLOR_NEW GColorPurple
#define AR_COLOR_RULE GColorDarkGray

static inline GColor ar_provider_color(const char *prov) {
  if (!prov || !prov[0]) {
    return GColorLightGray;
  }
  if (strcmp(prov, "claude") == 0) {
    return GColorOrange;
  }
  if (strcmp(prov, "grok") == 0) {
    return GColorVividCerulean;
  }
  if (strcmp(prov, "codex") == 0) {
    return GColorJaegerGreen;
  }
  if (strcmp(prov, "deepseek") == 0 || strcmp(prov, "dsh") == 0) {
    return GColorBlueMoon;
  }
  return GColorLightGray;
}

static inline const char *ar_provider_label(const char *prov) {
  if (!prov || !prov[0]) {
    return "";
  }
  if (strcmp(prov, "claude") == 0) {
    return "Claude";
  }
  if (strcmp(prov, "grok") == 0) {
    return "Grok";
  }
  if (strcmp(prov, "codex") == 0) {
    return "Codex";
  }
  if (strcmp(prov, "deepseek") == 0 || strcmp(prov, "dsh") == 0) {
    return "DeepSeek";
  }
  return prov;
}

static inline const char *ar_state_verb(const char *state, const char *phase) {
  if (!state) {
    return "";
  }
  if (strcmp(state, "needs_answer") == 0) {
    return "needs you";
  }
  if (strcmp(state, "failed") == 0) {
    return "failed";
  }
  if (strcmp(state, "working") == 0) {
    if (phase && phase[0]) {
      return phase;
    }
    return "working";
  }
  if (strcmp(state, "turn_finished") == 0) {
    return "finished";
  }
  return state;
}
