#include "app.h"
#include "md_draw.h"
#include "palette.h"

#include <stdio.h>
#include <string.h>

SessSnap g_sess;

static Window *s_win;
static Layer *s_body;
static int16_t s_scroll;
static int32_t s_page = -1;

#if defined(PBL_TOUCH)
static bool s_touching;
static bool s_edge;
static bool s_swiped;
static GPoint s_touch_start;
static GPoint s_touch_last;
#endif

static int16_t s_h_cache;
static int16_t s_h_width;
static int32_t s_h_page;
static uint8_t s_h_font;
static uint16_t s_h_len0;
static uint16_t s_h_len1;
static bool s_h_loaded;

static bool job_running(void) {
  if (!g_sess.jid[0] || g_sess.started <= 0) {
    return false;
  }
  if (!g_sess.status[0] || strcmp(g_sess.status, "starting") == 0 ||
      strcmp(g_sess.status, "running") == 0) {
    return true;
  }
  return false;
}

static void format_phase(char *dst, size_t n) {
  int elapsed;
  int m;
  int s;
  const char *phase = g_sess.phase[0] ? g_sess.phase : "working";
  if (job_running()) {
    elapsed = (int)(time(NULL) - (time_t)g_sess.started);
    if (elapsed < 0) {
      elapsed = 0;
    }
    g_sess.elapsed_frozen = elapsed;
  } else {
    elapsed = g_sess.elapsed_frozen;
  }
  m = elapsed / 60;
  s = elapsed % 60;
  snprintf(dst, n, "%s · %d:%02d", phase, m, s);
}

static int16_t content_h(int16_t width) {
  int i;
  int16_t h = 0;
  int16_t part;
  uint16_t l0;
  uint16_t l1;
  if (!g_sess.msg_loaded) {
    return md_text_height(g_sess.last, width);
  }
  l0 = (uint16_t)strlen(g_sess.msgs[0].text);
  l1 = (uint16_t)strlen(g_sess.msgs[1].text);
  if (s_h_width == width && s_h_font == (uint8_t)g_ar.font && s_h_page == g_sess.msg_i &&
      s_h_loaded && s_h_len0 == l0 && s_h_len1 == l1) {
    return s_h_cache;
  }
  for (i = 0; i < g_sess.msg_shown && i < AR_MSG_PAIR; i++) {
    if (!g_sess.msgs[i].text[0]) {
      continue;
    }
    part = md_text_height(g_sess.msgs[i].text, width);
    if (h > 0) {
      h = (int16_t)(h + 8);
    }
    h = (int16_t)(h + part);
  }
  s_h_cache = h;
  s_h_width = width;
  s_h_font = (uint8_t)g_ar.font;
  s_h_page = g_sess.msg_i;
  s_h_loaded = true;
  s_h_len0 = l0;
  s_h_len1 = l1;
  return h;
}

static void clamp_scroll(int16_t view_h, int16_t body_h) {
  int16_t max = (int16_t)(body_h - view_h);
  if (max < 0) {
    max = 0;
  }
  if (s_scroll < 0) {
    s_scroll = 0;
  }
  if (s_scroll > max) {
    s_scroll = max;
  }
}

static void reset_scroll_if_page(void) {
  if (s_page != g_sess.msg_i) {
    s_page = g_sess.msg_i;
    s_scroll = 0;
  }
}

static void format_footer(char *dst, size_t n) {
  if (g_sess.msg_loaded && g_sess.msg_n > 1) {
    snprintf(dst, n, "%d/%d", (int)(g_sess.msg_n - g_sess.msg_i), (int)g_sess.msg_n);
    return;
  }
  format_phase(dst, n);
}

static void draw_status(GContext *ctx, int16_t width) {
  int16_t x;
  const char *title = g_sess.title[0] ? g_sess.title : g_sess.sid;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, GRect(0, 0, width, AR_STATUS_H), 0, GCornerNone);
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, g_time, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(4, 0, 44, AR_STATUS_H), GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);
  if (g_ar.conn != AR_CONN_OK) {
    graphics_draw_text(ctx, ar_conn_label(g_ar.conn),
                       fonts_get_system_font(FONT_KEY_GOTHIC_14),
                       GRect(width - 72, 0, 68, AR_STATUS_H),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentRight, NULL);
    graphics_draw_text(ctx, title, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                       GRect(48, 0, width - 124, AR_STATUS_H),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
  } else {
    graphics_draw_text(ctx, title, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                       GRect(48, 0, width - 52, AR_STATUS_H),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
  }
  graphics_context_set_stroke_color(ctx, GColorDarkGray);
  graphics_context_set_stroke_width(ctx, 1);
  for (x = 0; x < width; x += 4) {
    graphics_draw_line(ctx, GPoint(x, AR_STATUS_H), GPoint(x + 1, AR_STATUS_H));
  }
}

static void update_body(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  int16_t width = (int16_t)(b.size.w - 10);
  int16_t y;
  int16_t h;
  int i;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  reset_scroll_if_page();
  clamp_scroll(b.size.h, content_h(width));
  y = (int16_t)(-s_scroll);

  if (!g_sess.msg_loaded) {
    h = md_text_height(g_sess.last, width);
    md_draw(ctx, GRect(8, y, width, h), g_sess.last, GColorLightGray);
    return;
  }
  for (i = 0; i < g_sess.msg_shown && i < AR_MSG_PAIR; i++) {
    if (!g_sess.msgs[i].text[0]) {
      continue;
    }
    h = md_text_height(g_sess.msgs[i].text, width);
    md_draw(ctx, GRect(8, y, width, h), g_sess.msgs[i].text,
            g_sess.msgs[i].role == 0 ? GColorLightGray : GColorWhite);
    y = (int16_t)(y + h + 8);
  }
}

static void update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  char left[48];
  int16_t bot;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  draw_status(ctx, b.size.w);

  graphics_context_set_fill_color(ctx, ar_provider_color(g_sess.prov));
  graphics_fill_rect(ctx, GRect(0, AR_STATUS_H, AR_STRIPE_W, b.size.h - AR_STATUS_H),
                     0, GCornerNone);

  bot = (int16_t)(b.size.h - AR_FOOTER_H);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, GRect(AR_STRIPE_W, bot, b.size.w - AR_STRIPE_W, AR_FOOTER_H),
                     0, GCornerNone);
  graphics_context_set_stroke_color(ctx, GColorDarkGray);
  graphics_draw_line(ctx, GPoint(AR_STRIPE_W, bot), GPoint(b.size.w, bot));
  format_footer(left, sizeof(left));
  graphics_context_set_text_color(ctx, GColorChromeYellow);
  graphics_draw_text(ctx, left, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(10, bot, 100, AR_FOOTER_H),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, g_sess.busy ? "busy" : "SELECT talk",
                     fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD),
                     GRect(8, bot, b.size.w - 16, AR_FOOTER_H),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentRight, NULL);
}

static void click_back(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (s_win) {
    window_stack_remove(s_win, true);
  }
}

static void click_select(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  g_sess.busy = false;
  dictation_start();
}

static void click_up(ClickRecognizerRef rec, void *ctx) {
  int32_t k;
  (void)rec;
  (void)ctx;
  if (!g_sess.msg_loaded || g_sess.msg_i <= 0) {
    return;
  }
  k = g_sess.msg_i - 1;
  if (k < 0) {
    k = 0;
  }
  s_scroll = 0;
  s_page = -1;
  msg_send_chunk(k);
}

static void click_down(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (!g_sess.msg_loaded || !g_sess.msg_more) {
    return;
  }
  s_scroll = 0;
  s_page = -1;
  msg_send_chunk(g_sess.msg_i + 1);
}

static void click_stop(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (g_sess.jid[0]) {
    msg_send_stop(g_sess.sid, g_sess.jid);
  }
}

static void click_config(void *ctx) {
  (void)ctx;
  window_single_click_subscribe(BUTTON_ID_BACK, click_back);
  window_single_click_subscribe(BUTTON_ID_UP, click_up);
  window_single_click_subscribe(BUTTON_ID_SELECT, click_select);
  window_single_click_subscribe(BUTTON_ID_DOWN, click_down);
  window_long_click_subscribe(BUTTON_ID_SELECT, 500, click_stop, NULL);
}

#if defined(PBL_TOUCH)
static void on_touch(const TouchEvent *event, void *context) {
  GRect b;
  int16_t dx;
  int16_t dy;
  int16_t ady;
  (void)context;
  if (!s_body || window_stack_get_top_window() != s_win) {
    s_touching = false;
    s_swiped = false;
    return;
  }
  if (event->type == TouchEvent_Touchdown) {
    s_touching = true;
    s_swiped = false;
    s_touch_start = GPoint(event->x, event->y);
    s_touch_last = s_touch_start;
    s_edge = (event->x <= 32);
    return;
  }
  if (event->type == TouchEvent_Liftoff) {
    if (s_touching && s_edge && !s_swiped) {
      dx = (int16_t)(s_touch_last.x - s_touch_start.x);
      dy = (int16_t)(s_touch_last.y - s_touch_start.y);
      ady = dy < 0 ? (int16_t)-dy : dy;
      if (dx >= 48 && dx >= (int16_t)(ady + 8)) {
        s_touching = false;
        s_swiped = true;
        click_back(NULL, NULL);
        return;
      }
    }
    s_touching = false;
    s_edge = false;
    return;
  }
  if (event->type != TouchEvent_PositionUpdate || !s_touching || s_swiped) {
    return;
  }
  dx = (int16_t)(event->x - s_touch_start.x);
  dy = (int16_t)(event->y - s_touch_start.y);
  ady = dy < 0 ? (int16_t)-dy : dy;
  if (s_edge && dx >= 48 && dx >= (int16_t)(ady + 8)) {
    s_swiped = true;
    s_touching = false;
    click_back(NULL, NULL);
    return;
  }
  if (s_edge && ady > 20 && ady > dx) {
    s_edge = false;
  }
  if (s_edge) {
    s_touch_last = GPoint(event->x, event->y);
    return;
  }
  s_scroll = (int16_t)(s_scroll + (s_touch_last.y - event->y));
  s_touch_last = GPoint(event->x, event->y);
  b = layer_get_bounds(s_body);
  clamp_scroll(b.size.h, content_h((int16_t)(b.size.w - 10)));
  layer_mark_dirty(s_body);
}
#endif

static void appear(Window *window) {
  (void)window;
#if defined(PBL_TOUCH)
  if (touch_service_is_enabled()) {
    touch_service_subscribe(on_touch, NULL);
  }
#endif
  ar_update_tick();
}

static void disappear(Window *window) {
  (void)window;
#if defined(PBL_TOUCH)
  s_touching = false;
  s_edge = false;
  s_swiped = false;
  touch_service_unsubscribe();
#endif
  ar_update_tick();
}

static void load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect b = layer_get_bounds(root);
  int16_t body_h = (int16_t)(b.size.h - AR_STATUS_H - 1 - AR_FOOTER_H);
  if (body_h < 16) {
    body_h = 16;
  }
  layer_set_update_proc(root, update);
  window_set_click_config_provider(window, click_config);
  s_body = layer_create(GRect(AR_STRIPE_W, AR_STATUS_H + 1,
                              b.size.w - AR_STRIPE_W, body_h));
  layer_set_update_proc(s_body, update_body);
  layer_set_clips(s_body, true);
  layer_add_child(root, s_body);
}

static void unload(Window *window) {
  (void)window;
  if (s_body) {
    layer_destroy(s_body);
    s_body = NULL;
  }
}

void sess_win_show(const FocusRow *row) {
  if (row) {
    memset(&g_sess, 0, sizeof(g_sess));
    utf8_copy(g_sess.sid, sizeof(g_sess.sid), row->sid);
    utf8_copy(g_sess.title, sizeof(g_sess.title), row->title[0] ? row->title : row->sid);
    utf8_copy(g_sess.prov, sizeof(g_sess.prov), row->prov);
    utf8_copy(g_sess.phase, sizeof(g_sess.phase),
              row->phase[0] ? row->phase : ar_state_verb(row->state, row->phase));
    s_scroll = 0;
    s_page = -1;
  }
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
  if (g_sess.sid[0]) {
    msg_send_open(g_sess.sid);
  }
  ar_update_tick();
}

void sess_win_pop(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    window_stack_remove(s_win, true);
  }
}

void sess_win_mark_dirty(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
    if (s_body) {
      layer_mark_dirty(s_body);
    }
  }
}

bool sess_win_is_top(void) {
  return s_win && window_stack_get_top_window() == s_win;
}

bool sess_win_is_up(void) {
  return s_win && window_stack_contains_window(s_win);
}

bool sess_win_wants_seconds(void) {
  return sess_win_is_top() && job_running();
}

void sess_win_deinit(void) {
  if (s_win) {
    if (window_stack_contains_window(s_win)) {
      window_stack_remove(s_win, false);
    }
    window_destroy(s_win);
    s_win = NULL;
  }
}
