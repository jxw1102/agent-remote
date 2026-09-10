#include "app.h"

#include <stdio.h>
#include <string.h>

typedef struct {
  char text[AR_QTEXT_LEN + 1];
  char opt[AR_QOPT_MAX][AR_QOPT_LEN + 1];
  char note_for[AR_QNOTEFOR_LEN + 1];
  int n;
  int multi;
  bool got[AR_QOPT_MAX];
} QuestQ;

static Window *s_win;
static ActionBarLayer *s_bar;
static GBitmap *s_up;
static GBitmap *s_down;
static GBitmap *s_check;
static QuestQ s_q[AR_Q_MAX];
static int s_qn;
static int s_qkind;
static int s_qi;
static int s_sel;
static int s_scroll;
static bool s_picked[AR_QOPT_MAX];
static char s_answers[AR_Q_MAX][AR_ANSWERS_LEN + 1];
static char s_notes[AR_Q_MAX][AR_QNOTE_LEN + 1];

static bool starts_ci(const char *s, const char *pfx) {
  size_t n = strlen(pfx);
  size_t i;
  for (i = 0; i < n; i++) {
    char a = s[i];
    char b = pfx[i];
    if (!a) {
      return false;
    }
    if (a >= 'A' && a <= 'Z') {
      a = (char)(a + 32);
    }
    if (b >= 'A' && b <= 'Z') {
      b = (char)(b + 32);
    }
    if (a != b) {
      return false;
    }
  }
  return true;
}

static bool contains_ci(const char *s, const char *sub) {
  size_t n = strlen(sub);
  size_t i;
  if (!n) {
    return true;
  }
  for (i = 0; s[i]; i++) {
    size_t j;
    bool ok = true;
    for (j = 0; j < n; j++) {
      char a = s[i + j];
      char b = sub[j];
      if (!a) {
        return false;
      }
      if (a >= 'A' && a <= 'Z') {
        a = (char)(a + 32);
      }
      if (b >= 'A' && b <= 'Z') {
        b = (char)(b + 32);
      }
      if (a != b) {
        ok = false;
        break;
      }
    }
    if (ok) {
      return true;
    }
  }
  return false;
}

void quest_win_reset(void) {
  memset(s_q, 0, sizeof(s_q));
  memset(s_answers, 0, sizeof(s_answers));
  memset(s_notes, 0, sizeof(s_notes));
  memset(s_picked, 0, sizeof(s_picked));
  s_qn = 0;
  s_qkind = 0;
  s_qi = 0;
  s_sel = 0;
  s_scroll = 0;
}

void quest_win_store(int qi, int qn, int oi, int on, int multi, int qkind,
                     const char *text, const char *opt, const char *note_for) {
  QuestQ *q;
  if (qn < 1) {
    qn = 1;
  }
  if (qn > AR_Q_MAX) {
    qn = AR_Q_MAX;
  }
  if (qi < 0 || qi >= qn || oi < 0 || oi >= AR_QOPT_MAX) {
    return;
  }
  if (on > AR_QOPT_MAX) {
    on = AR_QOPT_MAX;
  }
  s_qn = qn;
  s_qkind = qkind;
  q = &s_q[qi];
  q->multi = multi;
  if (on > q->n) {
    q->n = on;
  }
  if (oi < on) {
    utf8_copy(q->opt[oi], sizeof(q->opt[oi]), opt ? opt : "");
    q->got[oi] = true;
  }
  if (oi == 0 && text && text[0]) {
    utf8_copy(q->text, sizeof(q->text), text);
  }
  if (note_for && note_for[0]) {
    utf8_copy(q->note_for, sizeof(q->note_for), note_for);
  }
}

bool quest_win_ready(void) {
  int qi;
  int oi;
  if (s_qn < 1) {
    return false;
  }
  for (qi = 0; qi < s_qn; qi++) {
    if (s_q[qi].n < 1) {
      return false;
    }
    for (oi = 0; oi < s_q[qi].n; oi++) {
      if (!s_q[qi].got[oi]) {
        return false;
      }
    }
  }
  return true;
}

void quest_win_map_proceed(TakeState *out) {
  int i;
  QuestQ *q;
  if (!out || s_qn < 1) {
    return;
  }
  q = &s_q[0];
  out->yes[0] = out->no[0] = out->always[0] = '\0';
  utf8_copy(out->qtext, sizeof(out->qtext), q->text);
  for (i = 0; i < q->n; i++) {
    if (!out->yes[0] && starts_ci(q->opt[i], "Yes") && !contains_ci(q->opt[i], "don't ask")) {
      utf8_copy(out->yes, sizeof(out->yes), q->opt[i]);
    }
  }
  for (i = 0; i < q->n; i++) {
    if (!out->no[0] && starts_ci(q->opt[i], "No")) {
      utf8_copy(out->no, sizeof(out->no), q->opt[i]);
    }
  }
  for (i = 0; i < q->n; i++) {
    if (!out->always[0] && contains_ci(q->opt[i], "don't ask")) {
      utf8_copy(out->always, sizeof(out->always), q->opt[i]);
    }
  }
  if (!out->yes[0] && q->n > 0) {
    utf8_copy(out->yes, sizeof(out->yes), q->opt[0]);
  }
  if (!out->no[0] && q->n > 1) {
    utf8_copy(out->no, sizeof(out->no), q->opt[q->n - 1]);
  }
}

static void pack_answers(char *dst, size_t dst_sz) {
  size_t n = 0;
  int qi;
  dst[0] = '\0';
  for (qi = 0; qi < s_qn; qi++) {
    if (qi) {
      if (n + 2 >= dst_sz) {
        break;
      }
      dst[n++] = AR_SEP_RS;
      dst[n] = '\0';
    }
    utf8_copy(dst + n, dst_sz - n, s_answers[qi]);
    n = strlen(dst);
  }
}

static bool notes_any(void) {
  int qi;
  for (qi = 0; qi < s_qn; qi++) {
    if (s_notes[qi][0]) {
      return true;
    }
  }
  return false;
}

static void pack_notes(char *dst, size_t dst_sz) {
  size_t n = 0;
  int qi;
  dst[0] = '\0';
  if (dst_sz == 0) {
    return;
  }
  for (qi = 0; qi < s_qn; qi++) {
    size_t reserved;
    size_t cap;
    if (qi) {
      /* Always emit RS so split() keeps one slot per question. */
      if (n + 1 >= dst_sz) {
        break;
      }
      dst[n++] = AR_SEP_RS;
      dst[n] = '\0';
    }
    reserved = (size_t)(s_qn - 1 - qi);
    if (n + reserved + 1 > dst_sz) {
      break;
    }
    cap = dst_sz - n - reserved;
    utf8_copy(dst + n, cap, s_notes[qi]);
    n = strlen(dst);
  }
}

static void submit(bool cancel) {
  char answers[AR_ANSWERS_LEN + 1];
  char notes[AR_QNOTE_LEN + 1];
  bool ok;
  if (cancel) {
    msg_send_question(true, NULL, NULL);
    quest_win_pop();
    return;
  }
  pack_answers(answers, sizeof(answers));
  if (notes_any()) {
    pack_notes(notes, sizeof(notes));
    ok = msg_send_question(false, answers, notes);
  } else {
    ok = msg_send_question(false, answers, NULL);
  }
  if (!ok) {
    error_win_show("open on phone");
  }
  quest_win_pop();
}

static bool pack_current_answers(void) {
  QuestQ *q = &s_q[s_qi];
  char buf[AR_ANSWERS_LEN + 1];
  int i;
  size_t n = 0;
  buf[0] = '\0';
  if (q->multi) {
    for (i = 0; i < q->n; i++) {
      if (!s_picked[i]) {
        continue;
      }
      if (n && n + 2 < sizeof(buf)) {
        buf[n++] = AR_SEP_US;
        buf[n] = '\0';
      }
      utf8_copy(buf + n, sizeof(buf) - n, q->opt[i]);
      n = strlen(buf);
    }
    if (!buf[0]) {
      return false;
    }
    utf8_copy(s_answers[s_qi], sizeof(s_answers[s_qi]), buf);
  } else {
    if (s_sel < 0 || s_sel >= q->n) {
      return false;
    }
    utf8_copy(s_answers[s_qi], sizeof(s_answers[s_qi]), q->opt[s_sel]);
  }
  return true;
}

static bool current_wants_note(void) {
  QuestQ *q = &s_q[s_qi];
  int i;
  if (!q->note_for[0]) {
    return false;
  }
  if (q->multi) {
    for (i = 0; i < q->n; i++) {
      if (s_picked[i] && strcmp(q->opt[i], q->note_for) == 0) {
        return true;
      }
    }
    return false;
  }
  if (s_sel < 0 || s_sel >= q->n) {
    return false;
  }
  return strcmp(q->opt[s_sel], q->note_for) == 0;
}

static void advance_or_submit(void) {
  if (s_qi + 1 >= s_qn) {
    submit(false);
    return;
  }
  s_qi++;
  s_sel = 0;
  s_scroll = 0;
  memset(s_picked, 0, sizeof(s_picked));
  quest_win_mark_dirty();
}

static void store_current_and_advance(void) {
  if (!pack_current_answers()) {
    return;
  }
  if (current_wants_note()) {
    dictation_start_qnote();
    return;
  }
  s_notes[s_qi][0] = '\0';
  advance_or_submit();
}

void quest_win_accept_note(const char *text) {
  if (!pack_current_answers()) {
    return;
  }
  utf8_copy(s_notes[s_qi], sizeof(s_notes[s_qi]), text ? text : "");
  advance_or_submit();
}

static void clamp_scroll(void) {
  QuestQ *q = &s_q[s_qi];
  if (s_sel < s_scroll) {
    s_scroll = s_sel;
  }
  if (s_sel >= s_scroll + AR_Q_VIS) {
    s_scroll = s_sel - AR_Q_VIS + 1;
  }
  if (s_scroll < 0) {
    s_scroll = 0;
  }
  if (q->n > AR_Q_VIS && s_scroll > q->n - AR_Q_VIS) {
    s_scroll = q->n - AR_Q_VIS;
  }
}

static void click_up(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (s_sel > 0) {
    s_sel--;
    clamp_scroll();
    quest_win_mark_dirty();
  }
}

static void click_down(ClickRecognizerRef rec, void *ctx) {
  QuestQ *q = &s_q[s_qi];
  (void)rec;
  (void)ctx;
  if (s_sel + 1 < q->n) {
    s_sel++;
    clamp_scroll();
    quest_win_mark_dirty();
  }
}

static void click_select(ClickRecognizerRef rec, void *ctx) {
  QuestQ *q = &s_q[s_qi];
  (void)rec;
  (void)ctx;
  if (q->multi) {
    if (s_sel >= 0 && s_sel < q->n) {
      s_picked[s_sel] = !s_picked[s_sel];
      quest_win_mark_dirty();
    }
    return;
  }
  store_current_and_advance();
}

static void click_hold(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (s_q[s_qi].multi) {
    store_current_and_advance();
  }
}

static void click_back(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  submit(true);
}

static void clicks(void *ctx) {
  (void)ctx;
  window_single_click_subscribe(BUTTON_ID_BACK, click_back);
  window_single_click_subscribe(BUTTON_ID_UP, click_up);
  window_single_click_subscribe(BUTTON_ID_SELECT, click_select);
  window_single_click_subscribe(BUTTON_ID_DOWN, click_down);
  window_long_click_subscribe(BUTTON_ID_SELECT, 500, click_hold, NULL);
}

static void update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  int16_t inner = (int16_t)(b.size.w - ACTION_BAR_WIDTH);
  char status[24];
  const char *right;
  QuestQ *q;
  int i;
  int y;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  if (s_qn > 1) {
    status[0] = 'A';
    status[1] = 's';
    status[2] = 'k';
    status[3] = ' ';
    status[4] = (char)('0' + (s_qi >= 0 && s_qi < 9 ? s_qi + 1 : 9));
    status[5] = '/';
    status[6] = (char)('0' + (s_qn >= 0 && s_qn < 10 ? s_qn : 9));
    status[7] = '\0';
    right = status;
  } else {
    right = "Ask";
  }
  if (g_ar.conn != AR_CONN_OK) {
    right = ar_conn_label(g_ar.conn);
  }
  ar_draw_status_bar(ctx, inner > 0 ? inner : b.size.w, right);

  graphics_context_set_fill_color(ctx, GColorRajah);
  graphics_fill_rect(ctx, GRect(0, AR_STATUS_H, AR_STRIPE_W, b.size.h - AR_STATUS_H),
                     0, GCornerNone);

  q = &s_q[s_qi];
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, q->text, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(8, 26, inner - 16, 36), GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);

  for (i = 0; i < AR_Q_VIS; i++) {
    int idx = s_scroll + i;
    bool sel;
    char line[AR_QOPT_LEN + 8];
    if (idx >= q->n) {
      break;
    }
    y = 62 + i * AR_Q_ROW_H;
    sel = (idx == s_sel);
    graphics_context_set_fill_color(ctx, sel ? GColorRajah : GColorBlack);
    graphics_fill_rect(ctx, GRect(6, y, inner - 10, AR_Q_ROW_H - 1), 0, GCornerNone);
    if (q->multi && s_picked[idx]) {
      snprintf(line, sizeof(line), "✓ %s", q->opt[idx]);
    } else {
      utf8_copy(line, sizeof(line), q->opt[idx]);
    }
    graphics_context_set_text_color(ctx, GColorWhite);
    graphics_draw_text(ctx, line, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                       GRect(10, y + 2, inner - 18, AR_Q_ROW_H - 4),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
  }

  if (q->multi) {
    graphics_context_set_text_color(ctx, GColorLightGray);
    graphics_draw_text(ctx, "HOLD ✓", fonts_get_system_font(FONT_KEY_GOTHIC_14),
                       GRect(8, b.size.h - 24, inner - 16, 20),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentRight, NULL);
  }
}

static void appear(Window *window) {
  (void)window;
  light_attention();
  chime_remind_start();
}

static void disappear(Window *window) {
  (void)window;
  /* Confirm/dictation can cover us; leave only in quest_win_pop. */
}

static void load(Window *window) {
  Layer *root = window_get_root_layer(window);
  layer_set_update_proc(root, update);
  s_up = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_AB_UP);
  s_down = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_AB_DOWN);
  s_check = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_AB_CHECK);
  s_bar = action_bar_layer_create();
  action_bar_layer_set_background_color(s_bar, GColorBlack);
  action_bar_layer_set_icon(s_bar, BUTTON_ID_UP, s_up);
  action_bar_layer_set_icon(s_bar, BUTTON_ID_SELECT, s_check);
  action_bar_layer_set_icon(s_bar, BUTTON_ID_DOWN, s_down);
  action_bar_layer_set_click_config_provider(s_bar, clicks);
  action_bar_layer_add_to_window(s_bar, window);
}

static void unload(Window *window) {
  (void)window;
  if (s_bar) {
    action_bar_layer_remove_from_window(s_bar);
    action_bar_layer_destroy(s_bar);
    s_bar = NULL;
  }
  if (s_up) {
    gbitmap_destroy(s_up);
    s_up = NULL;
  }
  if (s_down) {
    gbitmap_destroy(s_down);
    s_down = NULL;
  }
  if (s_check) {
    gbitmap_destroy(s_check);
    s_check = NULL;
  }
}

void quest_win_show(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
    return;
  }
  ar_dictation_stop();
  if (perm_win_is_up()) {
    perm_win_pop();
  }
  s_qi = 0;
  s_sel = 0;
  s_scroll = 0;
  memset(s_picked, 0, sizeof(s_picked));
  if (!s_win) {
    s_win = window_create();
    window_set_background_color(s_win, GColorBlack);
    window_set_window_handlers(s_win, (WindowHandlers){
      .load = load,
      .unload = unload,
      .appear = appear,
      .disappear = disappear,
    });
  }
  if (!window_stack_contains_window(s_win)) {
    window_stack_push(s_win, true);
  } else {
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}

void quest_win_pop(void) {
  if (confirm_win_is_up()) {
    confirm_win_pop();
  }
  if (s_win && window_stack_contains_window(s_win)) {
    window_stack_remove(s_win, true);
  }
  takeover_leave();
}

void quest_win_mark_dirty(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}

bool quest_win_is_up(void) {
  return s_win && window_stack_contains_window(s_win);
}

void quest_win_deinit(void) {
  if (s_win) {
    if (window_stack_contains_window(s_win)) {
      window_stack_remove(s_win, false);
    }
    window_destroy(s_win);
    s_win = NULL;
  }
}
