#include "app.h"
#include "palette.h"

#include <string.h>

#define AR_PROV_MAX 8
#define AR_NEW_ROW_H 48
#define AR_SWATCH 12

static Window *s_win;
static MenuLayer *s_menu;
static Layer *s_status;
static char s_ids[AR_PROV_MAX][AR_PROV_LEN + 1];
static int s_n;

#if defined(PBL_TOUCH)
static bool s_touching;
static GPoint s_touch_last;
static int16_t s_touch_acc;
#endif

static int parse_provs(void) {
  const char *p = g_ar.provs;
  int n = 0;
  while (*p && n < AR_PROV_MAX) {
    size_t len = 0;
    while (*p == ',') {
      p++;
    }
    if (!*p) {
      break;
    }
    while (p[len] && p[len] != ',') {
      len++;
    }
    {
      size_t copy = len > AR_PROV_LEN ? AR_PROV_LEN : len;
      memcpy(s_ids[n], p, copy);
      s_ids[n][copy] = '\0';
    }
    n++;
    p += len;
  }
  s_n = n;
  return n;
}

static void begin_dictate(const char *prov) {
  const char *lab;
  memset(&g_sess, 0, sizeof(g_sess));
  utf8_copy(g_sess.prov, sizeof(g_sess.prov), prov ? prov : "");
  lab = ar_provider_label(g_sess.prov);
  utf8_copy(g_sess.title, sizeof(g_sess.title), lab[0] ? lab : g_sess.prov);
  if (!g_sess.title[0]) {
    utf8_copy(g_sess.title, sizeof(g_sess.title), "New session");
  }
  dictation_start();
}

static void status_update(Layer *layer, GContext *ctx) {
  const char *right = "New";
  (void)layer;
  if (g_ar.conn != AR_CONN_OK) {
    right = ar_conn_label(g_ar.conn);
  }
  ar_draw_status_bar(ctx, layer_get_bounds(layer).size.w, right);
}

static uint16_t get_num_rows(MenuLayer *ml, uint16_t section, void *ctx) {
  (void)ml;
  (void)section;
  (void)ctx;
  return (uint16_t)s_n;
}

static int16_t get_cell_height(MenuLayer *ml, MenuIndex *index, void *ctx) {
  (void)ml;
  (void)index;
  (void)ctx;
  return AR_NEW_ROW_H;
}

static void draw_row(GContext *ctx, const Layer *cell_layer, MenuIndex *index, void *ctx_data) {
  GRect b = layer_get_bounds(cell_layer);
  MenuIndex sel_idx = menu_layer_get_selected_index(s_menu);
  bool sel = sel_idx.section == index->section && sel_idx.row == index->row;
  const char *id;
  const char *lab;
  int16_t sw_y;
  (void)ctx_data;
  if (index->row >= s_n) {
    return;
  }
  id = s_ids[index->row];
  lab = ar_provider_label(id);
  if (!lab[0]) {
    lab = id;
  }

  graphics_context_set_fill_color(ctx, sel ? AR_COLOR_HI : GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);

  sw_y = (int16_t)((b.size.h - AR_SWATCH) / 2);
  graphics_context_set_fill_color(ctx, ar_provider_color(id));
  graphics_fill_rect(ctx, GRect(8, sw_y, AR_SWATCH, AR_SWATCH), 0, GCornerNone);

  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, lab, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD),
                     GRect(8 + AR_SWATCH + 8, 8, b.size.w - 36, 32),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
}

static void select_click(MenuLayer *ml, MenuIndex *index, void *ctx) {
  (void)ml;
  (void)ctx;
  if (!index || index->row >= s_n) {
    return;
  }
  begin_dictate(s_ids[index->row]);
}

#if defined(PBL_TOUCH)
static void on_touch(const TouchEvent *event, void *context) {
  MenuIndex idx;
  uint16_t max_row;
  (void)context;
  if (!s_menu || window_stack_get_top_window() != s_win) {
    s_touching = false;
    return;
  }
  if (event->type == TouchEvent_Touchdown) {
    s_touching = true;
    s_touch_last = GPoint(event->x, event->y);
    s_touch_acc = 0;
    return;
  }
  if (event->type == TouchEvent_Liftoff) {
    s_touching = false;
    return;
  }
  if (event->type != TouchEvent_PositionUpdate || !s_touching) {
    return;
  }
  s_touch_acc = (int16_t)(s_touch_acc + (s_touch_last.y - event->y));
  s_touch_last = GPoint(event->x, event->y);
  max_row = s_n > 0 ? (uint16_t)(s_n - 1) : 0;
  idx = menu_layer_get_selected_index(s_menu);
  while (s_touch_acc >= AR_NEW_ROW_H && idx.row < max_row) {
    idx.row++;
    s_touch_acc = (int16_t)(s_touch_acc - AR_NEW_ROW_H);
  }
  while (s_touch_acc <= -AR_NEW_ROW_H && idx.row > 0) {
    idx.row--;
    s_touch_acc = (int16_t)(s_touch_acc + AR_NEW_ROW_H);
  }
  menu_layer_set_selected_index(s_menu, idx, MenuRowAlignCenter, false);
}
#endif

static void window_load(Window *window) {
  Layer *root = window_get_root_layer(window);
  GRect b = layer_get_bounds(root);
  s_status = layer_create(GRect(0, 0, b.size.w, AR_STATUS_H + 1));
  layer_set_update_proc(s_status, status_update);
  layer_add_child(root, s_status);

  s_menu = menu_layer_create(GRect(0, AR_STATUS_H + 1, b.size.w,
                                   (int16_t)(b.size.h - AR_STATUS_H - 1)));
  menu_layer_set_normal_colors(s_menu, GColorBlack, GColorWhite);
  menu_layer_set_highlight_colors(s_menu, AR_COLOR_HI, GColorWhite);
  menu_layer_set_callbacks(s_menu, NULL, (MenuLayerCallbacks){
    .get_num_rows = get_num_rows,
    .get_cell_height = get_cell_height,
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

static void show_window(void) {
  parse_provs();
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
  } else if (s_menu) {
    menu_layer_reload_data(s_menu);
    layer_mark_dirty(menu_layer_get_layer(s_menu));
  }
  if (s_status) {
    layer_mark_dirty(s_status);
  }
}

void new_win_begin(void) {
  /* MULTI/PROVS arrive on the CONN=2 catalogue HELLO, not the CONN=1 stub. */
  if (g_ar.conn != AR_CONN_OK) {
    msg_send_hello();
    return;
  }
  memset(&g_sess, 0, sizeof(g_sess));
  parse_provs();
  if (g_ar.multi) {
    if (s_n == 0) {
      msg_send_providers_req();
    }
    show_window();
    return;
  }
  begin_dictate(s_n > 0 ? s_ids[0] : "");
}

void new_win_pop(void) {
  if (s_win && window_stack_contains_window(s_win)) {
    window_stack_remove(s_win, true);
  }
}

void new_win_mark_dirty(void) {
  if (!s_win || !window_stack_contains_window(s_win)) {
    return;
  }
  parse_provs();
  if (s_menu) {
    menu_layer_reload_data(s_menu);
    layer_mark_dirty(menu_layer_get_layer(s_menu));
  }
  if (s_status) {
    layer_mark_dirty(s_status);
  }
}

bool new_win_is_up(void) {
  return s_win && window_stack_contains_window(s_win);
}

void new_win_deinit(void) {
  if (s_win) {
    if (window_stack_contains_window(s_win)) {
      window_stack_remove(s_win, false);
    }
    window_destroy(s_win);
    s_win = NULL;
  }
  s_n = 0;
}
