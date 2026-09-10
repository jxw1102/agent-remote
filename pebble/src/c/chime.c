#include "app.h"

static AppTimer *s_remind;

static const uint32_t k_status[] = {18};
static const uint32_t k_done[] = {45};
static const uint32_t k_error[] = {55, 70, 55};
static const uint32_t k_attn[] = {35, 90, 35};

void chime_play(int32_t which) {
  VibePattern pat;
  switch (which) {
    case AR_CHIME_STATUS:
      pat.durations = k_status;
      pat.num_segments = 1;
      break;
    case AR_CHIME_DONE:
      pat.durations = k_done;
      pat.num_segments = 1;
      break;
    case AR_CHIME_ERROR:
      pat.durations = k_error;
      pat.num_segments = 3;
      break;
    case AR_CHIME_ATTENTION:
      pat.durations = k_attn;
      pat.num_segments = 3;
      break;
    default:
      return;
  }
  vibes_enqueue_custom_pattern(pat);
}

static void remind_cb(void *data) {
  (void)data;
  s_remind = NULL;
  if (!perm_win_is_up() && !quest_win_is_up()) {
    return;
  }
  chime_play(AR_CHIME_ATTENTION);
  s_remind = app_timer_register(AR_REMIND_MS, remind_cb, NULL);
}

void chime_remind_start(void) {
  if (s_remind) {
    app_timer_cancel(s_remind);
  }
  s_remind = app_timer_register(AR_REMIND_MS, remind_cb, NULL);
}

void chime_remind_stop(void) {
  if (s_remind) {
    app_timer_cancel(s_remind);
    s_remind = NULL;
  }
}

void chime_deinit(void) {
  chime_remind_stop();
}
