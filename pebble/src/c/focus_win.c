#include "app.h"
#include "palette.h"

#include <stdio.h>
#include <string.h>

FocusRow g_focus[AR_FOCUS_MAX];
int g_focus_n;
int32_t g_focus_gen;
static int32_t s_rx_gen;

static Window *s_win;
static MenuLayer *s_menu;
static Layer *s_status;
static int16_t s_menu_h;
static char s_keep_sid[AR_SID_LEN + 1];
static bool s_keep_new;

#if defined(PBL_TOUCH)
static bool s_touching;
static GPoint s_touch_last;
#endif

static void format_sub(char *dst, size_t dst_sz, const FocusRow *row) {
  const char *verb = ar_state_verb(row->state, row->phase);
  const char *lab = ar_provider_label(row->prov);
  if (lab[0]) {
    snprintf(dst, dst_sz, "%s · %s", verb, lab);
  } else {
    utf8_copy(dst, dst_sz, verb);
  }
}

static void status_update(Layer *layer, GContext *ctx) {
  const char *right;
  (void)layer;
  if (g_ar.conn == AR_CONN_OK) {
    right = "Focus";
  } else if (g_ar.conn == AR_CONN_TOKEN) {
    right = "401";
  } else {
    right = ar_conn_label(g_ar.conn);
  }
  ar_draw_status_bar(ctx, layer_get_bounds(layer).size.w, right);
}

static uint16_t get_num_rows(MenuLayer *ml, uint16_t section, void *ctx) {
  (void)ml;
  (void)section;
  (void)ctx;
  return (uint16_t)(g_focus_n + 1);
}

static int16_t get_cell_height(MenuLayer *ml, MenuIndex *index, void *ctx) {
  (void)ml;
  (void)index;
  (void)ctx;
  return AR_ROW_H;
}

static int16_t get_header_height(MenuLayer *ml, uint16_t section, void *ctx) {
  (void)ml;
  (void)section;
  (void)ctx;
  if (g_focus_n > 0) {
    return 0;
  }
  if (s_menu_h > AR_ROW_H) {
    return (int16_t)(s_menu_h - AR_ROW_H);
  }
  return 80;
}

static void draw_header(GContext *ctx, const Layer *cell_layer, uint16_t section, void *ctx_data) {
  GRect b = layer_get_bounds(cell_layer);
  GRect t;
  (void)section;
  (void)ctx_data;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  t = b;
  t.origin.y = (b.size.h - 20) / 2;
  t.size.h = 20;
  graphics_context_set_text_color(ctx, GColorLightGray);
  graphics_draw_text(ctx, "No sessions", fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     t, GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter, NULL);
}

static void draw_row(GContext *ctx, const Layer *cell_layer, MenuIndex *index, void *ctx_data) {
  GRect b = layer_get_bounds(cell_layer);
  MenuIndex sel_idx = menu_layer_get_selected_index(s_menu);
  bool sel = sel_idx.section == index->section && sel_idx.row == index->row;
  bool is_new = index->row >= g_focus_n;
  GColor tick;
  const char *title;
  char sub[48];
  GRect text;
  (void)ctx_data;

  graphics_context_set_fill_color(ctx, sel ? AR_COLOR_HI : GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  if (is_new) {
    tick = AR_COLOR_NEW;
    title = "New session";
    utf8_copy(sub, sizeof(sub), g_ar.host[0] ? g_ar.host : "daemon");
  } else {
    FocusRow *row = &g_focus[index->row];
    tick = ar_provider_color(row->prov);
    title = row->title[0] ? row->title : row->sid;
    format_sub(sub, sizeof(sub), row);
  }

  graphics_context_set_fill_color(ctx, tick);
  graphics_fill_rect(ctx, GRect(0, 0, AR_TICK_W, b.size.h), 0, GCornerNone);

  graphics_context_set_stroke_color(ctx, AR_COLOR_RULE);
  graphics_draw_line(ctx, GPoint(0, b.size.h - 1), GPoint(b.size.w - 1, b.size.h - 1));

  text = GRect(AR_TICK_W + 4, 0, b.size.w - AR_TICK_W - 8, 22);
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, title, fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD),
                     text, GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
  text.origin.y = 18;
  text.size.h = 22;
  graphics_context_set_text_color(ctx, sel ? AR_COLOR_SUB_HI : AR_COLOR_SUB);
  graphics_draw_text(ctx, sub, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     text, GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
}

#if defined(PBL_TOUCH)
static void on_touch(const TouchEvent *event, void *context) {
  ScrollLayer *sl;
  GPoint off;
  GSize content;
  GRect bounds;
  int16_t min_y;
  (void)context;
  if (!s_menu || window_stack_get_top_window() != s_win) {
    s_touching = false;
    return;
  }
  if (event->type == TouchEvent_Touchdown) {
    s_touching = true;
    s_touch_last = GPoint(event->x, event->y);
    return;
  }
  if (event->type == TouchEvent_Liftoff) {
    s_touching = false;
    return;
  }
  if (event->type != TouchEvent_PositionUpdate || !s_touching) {
    return;
  }
  sl = menu_layer_get_scroll_layer(s_menu);
  if (!sl) {
    return;
  }
  off = scroll_layer_get_content_offset(sl);
  off.x = 0;
  off.y = (int16_t)(off.y + (event->y - s_touch_last.y));
  s_touch_last = GPoint(event->x, event->y);
  if (off.y > 0) {
    off.y = 0;
  }
  content = scroll_layer_get_content_size(sl);
  bounds = layer_get_bounds(menu_layer_get_layer(s_menu));
  min_y = (int16_t)(bounds.size.h - content.h);
  if (min_y > 0) {
    min_y = 0;
  }
  if (off.y < min_y) {
    off.y = min_y;
  }
  scroll_layer_set_content_offset(sl, off, false);
}
#endif

static void select_click(MenuLayer *ml, MenuIndex *index, void *ctx) {
  (void)ml;
  (void)ctx;
  if (!index) {
    return;
  }
  if (index->row >= g_focus_n) {
    new_win_begin();
    return;
  }
  sess_win_show(&g_focus[index->row]);
}

static void remember_sel(void) {
  MenuIndex idx;
  /* Empty list only has New session — do not pin to that after the first fill. */
  if (!s_menu || g_focus_n <= 0) {
    s_keep_sid[0] = '\0';
    s_keep_new = false;
    return;
  }
  idx = menu_layer_get_selected_index(s_menu);
  if (idx.row < g_focus_n) {
    utf8_copy(s_keep_sid, sizeof(s_keep_sid), g_focus[idx.row].sid);
    s_keep_new = false;
  } else {
    s_keep_sid[0] = '\0';
    s_keep_new = true;
  }
}

static void restore_sel(void) {
  int i;
  if (!s_menu) {
    return;
  }
  if (!s_keep_new && s_keep_sid[0]) {
    for (i = 0; i < g_focus_n; i++) {
      if (strcmp(g_focus[i].sid, s_keep_sid) == 0) {
        menu_layer_set_selected_index(s_menu, (MenuIndex){.section = 0, .row = (uint16_t)i},
                                      MenuRowAlignNone, false);
        return;
      }
    }
  }
  if (s_keep_new) {
    menu_layer_set_selected_index(s_menu, (MenuIndex){.section = 0, .row = (uint16_t)g_focus_n},
                                  MenuRowAlignNone, false);
    return;
  }
  menu_layer_set_selected_index(s_menu, (MenuIndex){.section = 0, .row = 0},
                                MenuRowAlignTop, false);
}

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect b = layer_get_bounds(root);
  s_status = layer_create(GRect(0, 0, b.size.w, AR_STATUS_H + 1));
  layer_set_update_proc(s_status, status_update);
  layer_add_child(root, s_status);

  s_menu_h = (int16_t)(b.size.h - AR_STATUS_H - 1);
  s_menu = menu_layer_create(GRect(0, AR_STATUS_H + 1, b.size.w, s_menu_h));
  menu_layer_set_center_focused(s_menu, false);
  menu_layer_set_normal_colors(s_menu, GColorBlack, GColorWhite);
  menu_layer_set_highlight_colors(s_menu, AR_COLOR_HI, GColorWhite);
  menu_layer_set_callbacks(s_menu, NULL, (MenuLayerCallbacks){
    .get_num_rows = get_num_rows,
    .get_cell_height = get_cell_height,
    .get_header_height = get_header_height,
    .draw_header = draw_header,
    .draw_row = draw_row,
    .select_click = select_click,
  });
  menu_layer_set_click_config_onto_window(s_menu, window);
  layer_add_child(root, menu_layer_get_layer(s_menu));
}

#if defined(PBL_TOUCH)
static void window_appear(Window *window) {
  (void)window;
  if (touch_service_is_enabled()) {
    touch_service_subscribe(on_touch, NULL);
  }
}

static void window_disappear(Window *window) {
  (void)window;
  s_touching = false;
  touch_service_unsubscribe();
}
#endif

static void window_unload(Window *window) {
  (void)window;
  if (s_menu) {
    menu_layer_destroy(s_menu);
    s_menu = NULL;
  }
  if (s_status) {
    layer_destroy(s_status);
    s_status = NULL;
  }
}

void focus_win_push(void) {
  if (!s_win) {
    s_win = window_create();
    window_set_background_color(s_win, GColorBlack);
    window_set_window_handlers(s_win, (WindowHandlers){
      .load = window_load,
      .unload = window_unload,
#if defined(PBL_TOUCH)
      .appear = window_appear,
      .disappear = window_disappear,
#endif
    });
  }
  if (!window_stack_contains_window(s_win)) {
    window_stack_push(s_win, true);
  }
}

void focus_win_mark_dirty(void) {
  if (s_status) {
    layer_mark_dirty(s_status);
  }
  if (s_menu) {
    layer_mark_dirty(menu_layer_get_layer(s_menu));
  }
}

void focus_apply_row(int i, int32_t gen, const FocusRow *row) {
  if (!row || gen < g_focus_gen || gen < s_rx_gen) {
    return;
  }
  if (i < 0 || i >= AR_FOCUS_MAX) {
    return;
  }
  s_rx_gen = gen;
  g_focus[i] = *row;
  focus_win_mark_dirty();
}

void focus_apply_end(int n, int32_t gen) {
  int i;
  if (gen < g_focus_gen || gen < s_rx_gen) {
    return;
  }
  remember_sel();
  s_rx_gen = gen;
  g_focus_gen = gen;
  if (n < 0) {
    n = 0;
  }
  if (n > AR_FOCUS_MAX) {
    n = AR_FOCUS_MAX;
  }
  g_focus_n = n;
  for (i = n; i < AR_FOCUS_MAX; i++) {
    g_focus[i].sid[0] = '\0';
    g_focus[i].title[0] = '\0';
  }
  if (s_menu) {
    menu_layer_reload_data(s_menu);
    restore_sel();
  }
  focus_win_mark_dirty();
}

void focus_apply_phase(const char *sid, const char *phase) {
  int i;
  if (!sid || !sid[0] || !phase) {
    return;
  }
  for (i = 0; i < g_focus_n; i++) {
    if (strcmp(g_focus[i].sid, sid) == 0) {
      utf8_copy(g_focus[i].phase, sizeof(g_focus[i].phase), phase);
      focus_win_mark_dirty();
      return;
    }
  }
}

void focus_win_deinit(void) {
  if (s_win) {
    if (window_stack_contains_window(s_win)) {
      window_stack_remove(s_win, false);
    }
    window_destroy(s_win);
    s_win = NULL;
  }
}
