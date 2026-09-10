#include "app.h"

static DictationSession *s_session;
static bool s_stopping;
static ArConfirmKind s_kind;

static void voice_err(const char *msg) {
  APP_LOG(APP_LOG_LEVEL_INFO, "dictation %s", msg ? msg : "");
  chime_play(AR_CHIME_ERROR);
  error_win_show(msg);
}

static void callback(DictationSession *session, DictationSessionStatus status,
                     char *transcription, void *context) {
  (void)session;
  (void)context;
  APP_LOG(APP_LOG_LEVEL_INFO, "dictation status %d", (int)status);
  if (s_stopping) {
    return;
  }
  if (s_kind == AR_CONFIRM_CONTINUE && (perm_win_is_up() || quest_win_is_up())) {
    return;
  }
  if (s_kind == AR_CONFIRM_DENY && !perm_win_is_up()) {
    return;
  }
  if (s_kind == AR_CONFIRM_QNOTE && !quest_win_is_up()) {
    return;
  }
  if (status == DictationSessionStatusSuccess) {
    if (transcription && transcription[0]) {
      confirm_win_show_kind(transcription, s_kind);
    }
    return;
  }
  if (status == DictationSessionStatusFailureDisabled) {
    voice_err("voice off");
    return;
  }
  if (status == DictationSessionStatusFailureConnectivityError) {
    voice_err("voice net");
    return;
  }
  /* reject / no-speech / abort: firmware retried; stay, do not send */
}

static bool ensure_session(void) {
  if (s_session) {
    return true;
  }
  s_session = dictation_session_create(AR_PROMPT_LEN, callback, NULL);
  if (!s_session) {
    voice_err("voice off");
    return false;
  }
  dictation_session_enable_confirmation(s_session, false);
  dictation_session_enable_error_dialogs(s_session, true);
  return true;
}

static void start_kind(ArConfirmKind kind) {
  if (kind == AR_CONFIRM_CONTINUE && (perm_win_is_up() || quest_win_is_up())) {
    return;
  }
  if (kind == AR_CONFIRM_DENY && !perm_win_is_up()) {
    return;
  }
  if (kind == AR_CONFIRM_QNOTE && !quest_win_is_up()) {
    return;
  }
  if (!ensure_session()) {
    return;
  }
  s_kind = kind;
  s_stopping = false;
  dictation_session_enable_confirmation(s_session, false);
  dictation_session_enable_error_dialogs(s_session, true);
  dictation_session_start(s_session);
}

void dictation_start(void) {
  start_kind(AR_CONFIRM_CONTINUE);
}

void dictation_start_deny(void) {
  start_kind(AR_CONFIRM_DENY);
}

void dictation_start_qnote(void) {
  start_kind(AR_CONFIRM_QNOTE);
}

void ar_dictation_stop(void) {
  s_stopping = true;
  if (s_session) {
    dictation_session_stop(s_session);
  }
  confirm_win_pop();
}

void dictation_deinit(void) {
  s_stopping = true;
  if (s_session) {
    dictation_session_destroy(s_session);
    s_session = NULL;
  }
}
