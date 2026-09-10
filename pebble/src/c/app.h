#pragma once

#include <pebble.h>

#define AR_INBOX 1024
#define AR_OUTBOX 768
#define AR_STATUS_H 20
#define AR_FOOTER_H 28
#define AR_STRIPE_W 6
#define AR_ERR_LEN 48
#define AR_HOST_LEN 24
#define AR_VER_LEN 12
#define AR_PROVS_LEN 64

#define AR_FOCUS_MAX 12
#define AR_SID_LEN 64
#define AR_TITLE_LEN 40
#define AR_STATE_LEN 16
#define AR_PROV_LEN 16
#define AR_PHASE_LEN 24
#define AR_JID_LEN 48
#define AR_RID_LEN 48
#define AR_LAST_LEN 96
/* Fits one AppMessage (inbox 1024) with SID + ints; watch scrolls the rest. */
#define AR_MSG_LEN 700
#define AR_MSG_PAIR 2
#define AR_STATUS_LEN 12
#define AR_TOOL_LEN 24
#define AR_DETAIL_LEN 120
#define AR_QTEXT_LEN 80
#define AR_QOPT_LEN 80
#define AR_QNOTEFOR_LEN 40
#define AR_ANSWERS_LEN 400
#define AR_PROMPT_LEN 400
#define AR_QNOTE_LEN 200
#define AR_Q_MAX 4
#define AR_QOPT_MAX 12
#define AR_Q_VIS 4
#define AR_Q_ROW_H 28
#define AR_ROW_H 42
#define AR_TICK_W 6
#define AR_REMIND_MS 30000

#define AR_CMD_HELLO_REQ 1
#define AR_CMD_POLL_NOW 2
#define AR_CMD_OPEN 3
#define AR_CMD_CONTINUE 4
#define AR_CMD_NEW 5
#define AR_CMD_PERM 6
#define AR_CMD_QUESTION 7
#define AR_CMD_STOP 8
#define AR_CMD_CHUNK 9
#define AR_CMD_PROVIDERS_REQ 10

/* Must exceed Clay max idle (45 s) + non-ping XHR (15 s). */
#define AR_WATCHDOG_MS 70000

#define AR_KIND_HELLO 1
#define AR_KIND_FOCUS_ROW 2
#define AR_KIND_FOCUS_END 3
#define AR_KIND_SESS 4
#define AR_KIND_MSG 5
#define AR_KIND_PERM 6
#define AR_KIND_QUEST 7
#define AR_KIND_ERR 8
#define AR_KIND_CHIME 9
#define AR_KIND_TX 10
#define AR_KIND_JOB 11

#define AR_TX_SENDING 0
#define AR_TX_OK 1
#define AR_TX_FAIL 2

typedef enum {
  AR_CONFIRM_CONTINUE = 0,
  AR_CONFIRM_DENY = 1,
  AR_CONFIRM_QNOTE = 2
} ArConfirmKind;

#define AR_CHIME_STATUS 0
#define AR_CHIME_DONE 1
#define AR_CHIME_ERROR 2
#define AR_CHIME_ATTENTION 3

#define AR_SEP_RS '\x1e'
#define AR_SEP_US '\x1f'

typedef enum {
  AR_CONN_PHONE = 0,
  AR_CONN_DAEMON = 1,
  AR_CONN_OK = 2,
  AR_CONN_TOKEN = 3,
  AR_CONN_SETUP = 4,
  AR_CONN_UNKNOWN = 5
} ArConn;

typedef struct {
  ArConn conn;
  char host[AR_HOST_LEN + 1];
  char ver[AR_VER_LEN + 1];
  char provs[AR_PROVS_LEN + 1];
  int32_t multi;
  int32_t font;
} ArState;

typedef struct {
  char sid[AR_SID_LEN + 1];
  char title[AR_TITLE_LEN + 1];
  char state[AR_STATE_LEN + 1];
  char prov[AR_PROV_LEN + 1];
  char phase[AR_PHASE_LEN + 1];
  int32_t unread;
} FocusRow;

typedef struct {
  char text[AR_MSG_LEN + 1];
  int32_t role;
} SessMsg;

typedef struct {
  char sid[AR_SID_LEN + 1];
  char title[AR_TITLE_LEN + 1];
  char prov[AR_PROV_LEN + 1];
  char phase[AR_PHASE_LEN + 1];
  char last[AR_LAST_LEN + 1];
  char jid[AR_JID_LEN + 1];
  char status[AR_STATUS_LEN + 1];
  SessMsg msgs[AR_MSG_PAIR];
  int msg_shown;
  SessMsg rx[AR_MSG_PAIR];
  int32_t rx_k;
  int msg_got;
  bool msg_loaded;
  int32_t msg_i;
  int32_t msg_n;
  int32_t msg_more;
  int32_t started;
  int elapsed_frozen;
  bool busy;
} SessSnap;

typedef struct {
  char sid[AR_SID_LEN + 1];
  char jid[AR_JID_LEN + 1];
  char rid[AR_RID_LEN + 1];
  char tool[AR_TOOL_LEN + 1];
  char detail[AR_DETAIL_LEN + 1];
  char qtext[AR_QTEXT_LEN + 1];
  int32_t qkind;
  char yes[AR_QOPT_LEN + 1];
  char no[AR_QOPT_LEN + 1];
  char always[AR_QOPT_LEN + 1];
} TakeState;

extern ArState g_ar;
extern char g_time[8];

static inline GFont ar_body_font(void) {
  if (g_ar.font >= 2) {
    return fonts_get_system_font(FONT_KEY_GOTHIC_24);
  }
  if (g_ar.font == 1) {
    return fonts_get_system_font(FONT_KEY_GOTHIC_18);
  }
  return fonts_get_system_font(FONT_KEY_GOTHIC_14);
}

static inline GFont ar_body_font_bold(void) {
  if (g_ar.font >= 2) {
    return fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD);
  }
  if (g_ar.font == 1) {
    return fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD);
  }
  return fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD);
}

extern FocusRow g_focus[AR_FOCUS_MAX];
extern int g_focus_n;
extern int32_t g_focus_gen;
extern SessSnap g_sess;
extern TakeState g_take;

void ar_refresh_time(void);
void ar_set_conn(ArConn conn);
const char *ar_conn_label(ArConn conn);
void ar_mark_dirty(void);
void ar_draw_status_bar(GContext *ctx, int16_t width, const char *right);
void ar_watchdog_kick(void);
void ar_watchdog_stop(void);
void ar_update_tick(void);
void ar_dictation_stop(void);
void dictation_start(void);
void dictation_start_deny(void);
void dictation_start_qnote(void);
void dictation_deinit(void);

void utf8_copy(char *dst, size_t dst_sz, const char *src);

const char *msg_str(DictionaryIterator *iter, uint32_t key);
int32_t msg_int(DictionaryIterator *iter, uint32_t key, int32_t fallback);

void msg_init(void);
void msg_deinit(void);
void msg_send_hello(void);
void msg_send_open(const char *sid);
bool msg_send_continue(const char *sid, const char *prompt);
void msg_send_chunk(int32_t k);
bool msg_send_new(const char *prov, const char *prompt);
void msg_send_providers_req(void);
void msg_send_stop(const char *sid, const char *jid);
bool msg_send_perm(bool allow, const char *message);
bool msg_send_question(bool cancel, const char *answers, const char *qnote);

void error_win_show(const char *msg);
void error_win_hide(void);
void error_win_mark_dirty(void);
void error_win_deinit(void);

void focus_win_push(void);
void focus_win_deinit(void);
void focus_win_mark_dirty(void);
void focus_apply_row(int i, int32_t gen, const FocusRow *row);
void focus_apply_end(int n, int32_t gen);
void focus_apply_phase(const char *sid, const char *phase);

void sess_win_show(const FocusRow *row);
void sess_win_pop(void);
void sess_win_mark_dirty(void);
void sess_win_deinit(void);
bool sess_win_is_top(void);
bool sess_win_is_up(void);
bool sess_win_wants_seconds(void);

void new_win_begin(void);
void new_win_pop(void);
void new_win_mark_dirty(void);
void new_win_deinit(void);
bool new_win_is_up(void);

void confirm_win_show(const char *text);
void confirm_win_show_kind(const char *text, ArConfirmKind kind);
void confirm_win_pop(void);
void confirm_win_mark_dirty(void);
void confirm_win_deinit(void);
bool confirm_win_is_up(void);
bool confirm_win_is_sending(void);
void confirm_win_note_tx(int32_t tx);

void perm_win_show(void);
void perm_win_pop(void);
void perm_win_mark_dirty(void);
void perm_win_deinit(void);
bool perm_win_is_up(void);

void quest_win_reset(void);
void quest_win_store(int qi, int qn, int oi, int on, int multi, int qkind,
                     const char *text, const char *opt, const char *note_for);
bool quest_win_ready(void);
void quest_win_show(void);
void quest_win_pop(void);
void quest_win_mark_dirty(void);
void quest_win_deinit(void);
bool quest_win_is_up(void);
void quest_win_map_proceed(TakeState *out);
void quest_win_accept_note(const char *text);

void poll_ui_inbox(int32_t kind, DictionaryIterator *iter);
void takeover_leave(void);
void takeover_dismiss(const char *jid);

void chime_play(int32_t which);
void chime_remind_start(void);
void chime_remind_stop(void);
void chime_deinit(void);

void light_attention(void);
void light_leave(void);
