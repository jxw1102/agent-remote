#include "app.h"
#include "palette.h"

#include <stdio.h>
#include <string.h>

static Window *s_win;
static ActionBarLayer *s_bar;
static GBitmap *s_check;
static GBitmap *s_cross;
static char s_text[AR_PROMPT_LEN + 1];
static bool s_sending;
static ArConfirmKind s_kind;

static void click_select(ClickRecognizerRef rec, void *ctx) {
  char buf[AR_PROMPT_LEN + 1];
  (void)rec;
  (void)ctx;
  if (s_sending || !s_text[0]) {
    return;
  }
  if (s_kind == AR_CONFIRM_DENY) {
    utf8_copy(buf, sizeof(buf), s_text);
    s_sending = true;
    if (!msg_send_perm(false, buf)) {
      s_sending = false;
      return;
    }
    confirm_win_pop();
    perm_win_pop();
    return;
  }
  if (s_kind == AR_CONFIRM_QNOTE) {
    utf8_copy(buf, sizeof(buf), s_text);
    s_sending = true;
    confirm_win_pop();
    quest_win_accept_note(buf);
    return;
  }
  if (g_sess.sid[0]) {
    if (!msg_send_continue(g_sess.sid, s_text)) {
      return;
    }
  } else if (!msg_send_new(g_sess.prov, s_text)) {
    return;
  }
  s_sending = true;
  g_sess.busy = false;
  layer_mark_dirty(window_get_root_layer(s_win));
}

static void click_down(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (s_sending) {
    return;
  }
  if (s_kind == AR_CONFIRM_QNOTE) {
    confirm_win_pop();
    quest_win_accept_note("");
    return;
  }
  confirm_win_pop();
}

static void click_back(ClickRecognizerRef rec, void *ctx) {
  (void)rec;
  (void)ctx;
  if (s_sending) {
    return;
  }
  if (s_kind == AR_CONFIRM_DENY) {
    dictation_start_deny();
  } else if (s_kind == AR_CONFIRM_QNOTE) {
    dictation_start_qnote();
  } else {
    dictation_start();
  }
}

static void clicks(void *ctx) {
  (void)ctx;
  window_single_click_subscribe(BUTTON_ID_BACK, click_back);
  window_single_click_subscribe(BUTTON_ID_SELECT, click_select);
  window_single_click_subscribe(BUTTON_ID_DOWN, click_down);
}

static void update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  int16_t inner = (int16_t)(b.size.w - ACTION_BAR_WIDTH);
  char cap[AR_SID_LEN + 8];
  const char *right = "Talk";
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  if (s_kind == AR_CONFIRM_DENY) {
    right = "Deny";
  } else if (s_kind == AR_CONFIRM_QNOTE) {
    right = "Note";
  }
  ar_draw_status_bar(ctx, inner > 0 ? inner : b.size.w, right);

  graphics_context_set_fill_color(ctx, ar_provider_color(g_sess.prov));
  graphics_fill_rect(ctx, GRect(0, AR_STATUS_H, AR_STRIPE_W, b.size.h - AR_STATUS_H),
                     0, GCornerNone);

  if (s_kind == AR_CONFIRM_DENY) {
    snprintf(cap, sizeof(cap), "Deny note");
  } else if (s_kind == AR_CONFIRM_QNOTE) {
    snprintf(cap, sizeof(cap), "Add a note");
  } else {
    snprintf(cap, sizeof(cap), "To %s", g_sess.title[0] ? g_sess.title : g_sess.sid);
  }
  graphics_context_set_text_color(ctx, GColorLightGray);
  graphics_draw_text(ctx, cap, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(14, 28, inner > 22 ? inner - 22 : 148, 20),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);

  graphics_context_set_stroke_color(ctx, GColorDarkGray);
  graphics_context_set_stroke_width(ctx, 1);
  graphics_draw_rect(ctx, GRect(8, 48, 154, 88));
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, s_text, ar_body_font(),
                     GRect(12, 50, 146, 84), GTextOverflowModeWordWrap,
                     GTextAlignmentLeft, NULL);
}

static void load(Window *window) {
  Layer *root = window_get_root_layer(window);
  layer_set_update_proc(root, update);
  s_check = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_AB_CHECK);
  s_cross = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_AB_CROSS);
  s_bar = action_bar_layer_create();
  action_bar_layer_set_background_color(s_bar, GColorBlack);
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

void confirm_win_show(const char *text) {
  confirm_win_show_kind(text, AR_CONFIRM_CONTINUE);
}

void confirm_win_show_kind(const char *text, ArConfirmKind kind) {
  utf8_copy(s_text, sizeof(s_text), text ? text : "");
  s_kind = kind;
  s_sending = false;
  if (!s_win) {
    s_win = window_create();
    window_set_background_color(s_win, GColorBlack);
    window_set_window_handlers(s_win, (WindowHandlers){
      .load = load,
      .unload = unload,
    });
  }
  if (!window_stack_contains_window(s_win)) {
    window_stack_push(s_win, true);
  } else {
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}

void confirm_win_pop(void) {
  s_sending = false;
  s_text[0] = '\0';
  if (s_win && window_stack_contains_window(s_win)) {
    window_stack_remove(s_win, true);
  }
}

void confirm_win_mark_dirty(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}

bool confirm_win_is_up(void) {
  return s_win && window_stack_contains_window(s_win);
}

bool confirm_win_is_sending(void) {
  return s_sending && confirm_win_is_up() && s_kind == AR_CONFIRM_CONTINUE;
}

void confirm_win_note_tx(int32_t tx) {
  if (s_kind != AR_CONFIRM_CONTINUE) {
    return;
  }
  if (tx == AR_TX_SENDING) {
    s_sending = true;
  } else {
    s_sending = false;
  }
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}

void confirm_win_deinit(void) {
  if (s_win) {
    if (window_stack_contains_window(s_win)) {
      window_stack_remove(s_win, false);
    }
    window_destroy(s_win);
    s_win = NULL;
  }
  s_sending = false;
  s_text[0] = '\0';
  s_kind = AR_CONFIRM_CONTINUE;
}
