#include "app.h"

ArState g_ar;
char g_time[8] = "--:--";

static AppTimer *s_watchdog;

const char *ar_conn_label(ArConn conn) {
  switch (conn) {
    case AR_CONN_PHONE:
      return "phone";
    case AR_CONN_DAEMON:
      return "daemon";
    case AR_CONN_OK:
      return "AR";
    case AR_CONN_TOKEN:
      return "token?";
    case AR_CONN_SETUP:
      return "setup";
    default:
      return "";
  }
}

void ar_refresh_time(void) {
  time_t now = time(NULL);
  struct tm *tm = localtime(&now);
  strftime(g_time, sizeof(g_time), "%H:%M", tm);
}

void ar_set_conn(ArConn conn) {
  if (g_ar.conn != conn) {
    APP_LOG(APP_LOG_LEVEL_INFO, "conn %d -> %d", (int)g_ar.conn, (int)conn);
  }
  g_ar.conn = conn;
  if (conn == AR_CONN_SETUP) {
    error_win_show("set URL");
  } else if (conn == AR_CONN_TOKEN) {
    error_win_show("token?");
  } else if (conn == AR_CONN_OK) {
    error_win_hide();
  }
  ar_mark_dirty();
}

void ar_mark_dirty(void) {
  focus_win_mark_dirty();
  error_win_mark_dirty();
  sess_win_mark_dirty();
  confirm_win_mark_dirty();
  perm_win_mark_dirty();
  quest_win_mark_dirty();
  new_win_mark_dirty();
}

static void tick_handler(struct tm *tick_time, TimeUnits units);

void ar_update_tick(void) {
  tick_timer_service_subscribe(
      sess_win_wants_seconds() ? SECOND_UNIT : MINUTE_UNIT, tick_handler);
}

void ar_draw_status_bar(GContext *ctx, int16_t width, const char *right) {
  int16_t x;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, GRect(0, 0, width, AR_STATUS_H), 0, GCornerNone);
  graphics_context_set_text_color(ctx, GColorWhite);
  graphics_draw_text(ctx, g_time, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(4, 0, 72, AR_STATUS_H), GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);
  graphics_draw_text(ctx, right ? right : "", fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(width - 100, 0, 96, AR_STATUS_H),
                     GTextOverflowModeTrailingEllipsis, GTextAlignmentRight, NULL);
  graphics_context_set_stroke_color(ctx, GColorDarkGray);
  graphics_context_set_stroke_width(ctx, 1);
  for (x = 0; x < width; x += 4) {
    graphics_draw_line(ctx, GPoint(x, AR_STATUS_H), GPoint(x + 1, AR_STATUS_H));
  }
}

static void watchdog_cb(void *data) {
  (void)data;
  s_watchdog = NULL;
  if (g_ar.conn == AR_CONN_OK || g_ar.conn == AR_CONN_UNKNOWN) {
    ar_set_conn(AR_CONN_PHONE);
  }
}

void ar_watchdog_kick(void) {
  if (s_watchdog) {
    app_timer_cancel(s_watchdog);
  }
  s_watchdog = app_timer_register(AR_WATCHDOG_MS, watchdog_cb, NULL);
}

void ar_watchdog_stop(void) {
  if (s_watchdog) {
    app_timer_cancel(s_watchdog);
    s_watchdog = NULL;
  }
}

static void tick_handler(struct tm *tick_time, TimeUnits units) {
  (void)tick_time;
  (void)units;
  ar_refresh_time();
  ar_mark_dirty();
}

static void bt_handler(bool connected) {
  if (!connected) {
    ar_watchdog_stop();
    ar_set_conn(AR_CONN_PHONE);
    return;
  }
  msg_send_hello();
}

static void init(void) {
  g_ar.conn = AR_CONN_UNKNOWN;
  g_ar.font = 1;
  ar_refresh_time();

  focus_win_push();

  tick_timer_service_subscribe(MINUTE_UNIT, tick_handler);
  connection_service_subscribe((ConnectionHandlers){
    .pebble_app_connection_handler = bt_handler,
  });
  msg_init();

  if (!connection_service_peek_pebble_app_connection()) {
    ar_set_conn(AR_CONN_PHONE);
  } else {
    msg_send_hello();
  }
}

static void deinit(void) {
  ar_watchdog_stop();
  connection_service_unsubscribe();
  tick_timer_service_unsubscribe();
  msg_deinit();
  chime_deinit();
  dictation_deinit();
  quest_win_deinit();
  perm_win_deinit();
  confirm_win_deinit();
  new_win_deinit();
  sess_win_deinit();
  error_win_deinit();
  focus_win_deinit();
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
