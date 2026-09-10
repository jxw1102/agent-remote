#include "app.h"
#include "palette.h"

#include <stdio.h>
#include <string.h>

static Window *s_win;
static ActionBarLayer *s_bar;
static GBitmap *s_check;
static GBitmap *s_cross;

static void finish_mcp(bool allow) {
  msg_send_perm(allow, NULL);
  perm_win_pop();
}

static void finish_proceed(const char *label) {
  if (!label || !label[0]) {
    return;
  }
  if (!msg_send_question(false, label, NULL)) {
    error_win_show("open on phone");
  }
  perm_win_pop();
}

static void click_up(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (confirm_win_is_up()) {
    return;
  }
  if (g_take.qkind == 1) {
    finish_proceed(g_take.yes);
  } else {
    finish_mcp(true);
  }
}

static void click_down(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (confirm_win_is_up()) {
    return;
  }
  if (g_take.qkind == 1) {
    finish_proceed(g_take.no);
  } else {
    finish_mcp(false);
  }
}

static void click_select(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (confirm_win_is_up()) {
    return;
  }
  if (g_take.qkind == 1) {
    finish_proceed(g_take.always[0] ? g_take.always : g_take.yes);
  } else {
    finish_mcp(true);
  }
}

static void click_long(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (confirm_win_is_up()) {
    return;
  }
  /* Pebble swallows the single-click once a long-press is recognized.
     QKIND=1 has no deny-note; Long-Select must still post don't-ask / Yes. */
  if (g_take.qkind == 1) {
    click_select(rec, ctx);
    return;
  }
  dictation_start_deny();
}

static void click_back(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (confirm_win_is_up()) {
    return;
  }
  if (g_take.qkind == 1) {
    finish_proceed(g_take.no);
  } else {
    finish_mcp(false);
  }
}

static void clicks(void *ctx) {
  (void)ctx;
  window_single_click_subscribe(BUTTON_ID_BACK, click_back);
  window_single_click_subscribe(BUTTON_ID_UP, click_up);
  window_single_click_subscribe(BUTTON_ID_SELECT, click_select);
  window_single_click_subscribe(BUTTON_ID_DOWN, click_down);
  window_long_click_subscribe(BUTTON_ID_SELECT, 500, click_long, NULL);
}

static void update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  int16_t inner = (int16_t)(b.size.w - ACTION_BAR_WIDTH);
  char title[48];
  const char *right;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  if (g_ar.conn == AR_CONN_OK) {
    right = ar_provider_label(g_sess.prov[0] ? g_sess.prov : "");
    if (!right[0]) {
      right = "AR";
    }
  } else {
    right = ar_conn_label(g_ar.conn);
  }
  ar_draw_status_bar(ctx, inner > 0 ? inner : b.size.w, right);

  graphics_context_set_fill_color(ctx, GColorOrange);
  graphics_fill_rect(ctx, GRect(0, AR_STATUS_H, AR_STRIPE_W, b.size.h - AR_STATUS_H),
                     0, GCornerNone);

  if (g_take.qkind == 1) {
    utf8_copy(title, sizeof(title), "Permission");
  } else {
    snprintf(title, sizeof(title), "Allow %s?", g_take.tool[0] ? g_take.tool : "tool");
  }
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, title, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD),
                     GRect(8, 28, inner - 16, 28), GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);
  graphics_context_set_text_color(ctx, GColorLightGray);
  graphics_draw_text(ctx, g_take.qkind == 1 ? g_take.qtext : g_take.detail,
                     fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(8, 56, inner - 16, 140), GTextOverflowModeWordWrap,
                     GTextAlignmentLeft, NULL);
}

static void appear(Window *window) {
  (void)window;
  light_attention();
  chime_remind_start();
}

static void disappear(Window *window) {
  (void)window;
  /* Confirm/dictation can cover us; leave only in perm_win_pop. */
}

static void load(Window *window) {
  Layer *root = window_get_root_layer(window);
  layer_set_update_proc(root, update);
  s_check = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_AB_CHECK);
  s_cross = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_AB_CROSS);
  s_bar = action_bar_layer_create();
  action_bar_layer_set_background_color(s_bar, GColorBlack);
  action_bar_layer_set_icon(s_bar, BUTTON_ID_UP, s_check);
  action_bar_layer_set_icon(s_bar, BUTTON_ID_SELECT, s_check);
  action_bar_layer_set_icon(s_bar, BUTTON_ID_DOWN, s_cross);
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
  if (s_check) {
    gbitmap_destroy(s_check);
    s_check = NULL;
  }
  if (s_cross) {
    gbitmap_destroy(s_cross);
    s_cross = NULL;
  }
}

void perm_win_show(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
    return;
  }
  ar_dictation_stop();
  if (quest_win_is_up()) {
    quest_win_pop();
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
}

void perm_win_pop(void) {
  if (confirm_win_is_up()) {
    confirm_win_pop();
  }
  if (s_win && window_stack_contains_window(s_win)) {
    window_stack_remove(s_win, true);
  }
  takeover_leave();
}

void perm_win_mark_dirty(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}

bool perm_win_is_up(void) {
  return s_win && window_stack_contains_window(s_win);
}

void perm_win_deinit(void) {
  if (s_win) {
    if (window_stack_contains_window(s_win)) {
      window_stack_remove(s_win, false);
    }
    window_destroy(s_win);
    s_win = NULL;
  }
}
