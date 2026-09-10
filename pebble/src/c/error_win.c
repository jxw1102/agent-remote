#include "app.h"

static Window *s_win;
static char s_msg[AR_ERR_LEN + 1];

static void update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  ar_draw_status_bar(ctx, b.size.w, ar_conn_label(g_ar.conn));
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, s_msg, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD),
                     GRect(8, 48, b.size.w - 16, 80), GTextOverflowModeWordWrap,
                     GTextAlignmentCenter, NULL);
}

static void load(Window *window) {
  layer_set_update_proc(window_get_root_layer(window), update);
}

static void unload(Window *window) {
  (void)window;
}

void error_win_show(const char *msg) {
  utf8_copy(s_msg, sizeof(s_msg), msg ? msg : "");
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

void error_win_hide(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    window_stack_remove(s_win, true);
  }
}

void error_win_mark_dirty(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}

void error_win_deinit(void) {
  error_win_hide();
  if (s_win) {
    window_destroy(s_win);
    s_win = NULL;
  }
}
