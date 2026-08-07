#include "ui/ui.h"
#include "board_pins.h"
#include "hw/chime.h"
#include "hw/dlog.h"
#include "hw/keyboard.h"
#include "hw/lvgl_glue.h"
#include "hw/power.h"
#include "hw/sdconfig.h"
#include "hw/wifi_mgr.h"
#include "net/agentapi.h"
#include "net/blebuddy.h"
#include "net/statusfeed.h"
#include "net/timesync.h"
#include "net/webui.h"

#include <lvgl.h>
#include <algorithm>
#include <vector>

namespace ui {
namespace {

enum class Screen : uint8_t {
  Boot, Beeper, Menu, Sessions, Compose, LiveTui, Chimes,
  WifiScan, WifiPass, WifiManual, WifiSaved, Daemon, DaemonEdit, Bluetooth,
  WebUi, Usage, Diag, Power,
};

AppConfig *cfg = nullptr;
Screen cur = Screen::Boot;
lv_group_t *grp = nullptr;

String pendingSsid;
String composeSession;  // session id, empty = new session
int composeDaemon = 0;   // which daemon owns composeSession
Screen tuiFrom = Screen::Sessions;  // where Live TUI was opened from
int editDaemon = 0;      // slot open in the daemon edit screen
String ticker;
String lastSig;
uint32_t lastPoll = 0;
std::vector<SessionRow> sessions;
uint32_t lastTitleFetch = 0;

// Beeper widgets refreshed in place each second.
lv_obj_t *cardsBox = nullptr;
lv_obj_t *idleBox = nullptr;
lv_obj_t *idleTitle = nullptr;
lv_obj_t *idleSub = nullptr;
lv_obj_t *hdrTime = nullptr;  // wall clock (NTP / BLE sync)
lv_obj_t *hdrMod = nullptr;   // CAP / SYM
lv_obj_t *hdrBell = nullptr;  // beeper feed health
lv_obj_t *hdrWifi = nullptr;  // network state
lv_obj_t *hdrBatt = nullptr;  // battery icon + %
struct CardRef {
  String jobId;
  lv_obj_t *elapsed;
};
std::vector<CardRef> cardRefs;

// Feed diff bookkeeping.
struct PrevJob {
  String id;
  String toolSig;
  bool pending;
};
std::vector<PrevJob> prevJobs;
bool feedSeeded = false;
uint32_t lastToolBeep = 0;  // floor between tool-change beeps
uint32_t lastFeedGen = 0;
bool anyPending = false;
uint32_t remindNextAt = 0;
constexpr uint32_t kRemindMs = 30000;

void show(Screen s, bool backward = false);

// ---- shared bits ---------------------------------------------------------

// Agent Remote mark ("
// >_") drawn as LVGL lines — same geometry as the web
// client's SVG (viewBox 108: chevron 34,40-48,54-34,68 / bar 56,68-76,68).
void freeLogoPoints(lv_event_t *e) {
  lv_free(lv_event_get_user_data(e));
}

lv_obj_t *makeLogo(lv_obj_t *parent, int size) {
  lv_obj_t *box = lv_obj_create(parent);
  lv_obj_remove_style_all(box);
  lv_obj_set_size(box, size, size);

  float k = size / 108.0f;
  int lw = (int)(7 * k);
  if (lw < 2) lw = 2;

  lv_point_precise_t *pts =
      (lv_point_precise_t *)lv_malloc(sizeof(lv_point_precise_t) * 5);
  if (!pts) return box;  // pool exhausted — plain chip beats a crash
  pts[0] = {(lv_value_precise_t)(34 * k), (lv_value_precise_t)(40 * k)};
  pts[1] = {(lv_value_precise_t)(48 * k), (lv_value_precise_t)(54 * k)};
  pts[2] = {(lv_value_precise_t)(34 * k), (lv_value_precise_t)(68 * k)};
  pts[3] = {(lv_value_precise_t)(56 * k), (lv_value_precise_t)(68 * k)};
  pts[4] = {(lv_value_precise_t)(76 * k), (lv_value_precise_t)(68 * k)};
  lv_obj_add_event_cb(box, freeLogoPoints, LV_EVENT_DELETE, pts);

  lv_obj_t *chev = lv_line_create(box);
  lv_line_set_points(chev, pts, 3);
  lv_obj_set_style_line_width(chev, lw, 0);
  lv_obj_set_style_line_rounded(chev, true, 0);
  lv_obj_set_style_line_color(chev, lv_color_hex(0xd97757), 0);

  lv_obj_t *bar = lv_line_create(box);
  lv_line_set_points(bar, pts + 3, 2);
  lv_obj_set_style_line_width(bar, lw, 0);
  lv_obj_set_style_line_rounded(bar, true, 0);
  lv_obj_set_style_line_color(bar, lv_color_hex(0x00d4ff), 0);
  return box;
}

// Save to NVS and mirror to the SD card so a reflash keeps the config.
void persist() {
  cfg->save();
  sdconfig::exportConfig(*cfg);
}

// Saved Wi-Fi list → wifi_mgr for auto-connect.
void syncWifi() {
  String ss[AppConfig::kMaxWifi], pp[AppConfig::kMaxWifi];
  for (int i = 0; i < cfg->wifiCount; i++) {
    ss[i] = cfg->wifis[i].ssid;
    pp[i] = cfg->wifis[i].pass;
  }
  wifi_mgr::setSaved(ss, pp, cfg->wifiCount);
}

// One persistent worker runs every background job. Spawning a fresh task
// per job needed a contiguous 20 KB internal-RAM stack each time — with
// three https feeds resident that allocation could fail, the job never ran,
// and its "loading" state spun forever (the endless Usage spinner).
enum WorkerJob : uint8_t {
  JOB_SESSIONS, JOB_TUI, JOB_REPLY, JOB_DIAG, JOB_USAGE, JOB_POLL,
};
QueueHandle_t workerQ = nullptr;

void runSessionsFetch();
void runTuiFetch();
void runReply();
void runDiagUpload();
void runUsageFetch();
void runStatusPoll();

void workerTask(void *) {
  uint8_t j;
  for (;;) {
    if (xQueueReceive(workerQ, &j, portMAX_DELAY) != pdTRUE) continue;
    switch ((WorkerJob)j) {
      case JOB_SESSIONS: runSessionsFetch(); break;
      case JOB_TUI: runTuiFetch(); break;
      case JOB_REPLY: runReply(); break;
      case JOB_DIAG: runDiagUpload(); break;
      case JOB_USAGE: runUsageFetch(); break;
      case JOB_POLL: runStatusPoll(); break;
    }
  }
}

bool submitJob(WorkerJob j) {
  if (!workerQ) return false;
  uint8_t v = (uint8_t)j;
  return xQueueSend(workerQ, &v, 0) == pdTRUE;
}

// Push the configured daemons into the API + feed layers.
void applyDaemons() {
  agentapi::setDaemonCount(cfg->daemonCount);
  statusfeed::setCount(cfg->daemonCount);
  for (int i = 0; i < cfg->daemonCount; i++) {
    bool on = cfg->daemons[i].enabled;
    agentapi::configure(i, on ? cfg->apiBase(i) : String(),
                        cfg->daemons[i].token);
    statusfeed::configure(i, on ? cfg->apiBase(i) : String(),
                          cfg->daemons[i].token);
  }
}

// Free an https daemon's held TLS session before an API call to it; the
// feed reconnects on its own. With every daemon on https, held sessions
// were starving the API handshake (measured: 50 KB heap, HTTP -1).
void pauseHttpsFeed(int d, uint32_t ms) {
  if (d >= 0 && d < cfg->daemonCount && cfg->apiBase(d).startsWith("https://"))
    statusfeed::pause(d, ms);
}

// First daemon whose feed is live — target for uploads and best-effort calls.
int anyLiveDaemon() {
  for (int i = 0; i < cfg->daemonCount; i++)
    if (statusfeed::state(i) == statusfeed::State::Live) return i;
  return 0;
}

// Event chime that also lights the screen (dim wakes on new activity).
void cue(chime::Cue c) {
  chime::play(c, cfg->soundCues, cfg->hapticCues);
  lvgl_glue::pokeActivity();
}

// Brand accents shared with Android/web (Theme.kt Accent).
lv_color_t providerColor(const String &p) {
  if (p.startsWith("cl")) return lv_color_hex(0xd97757);  // Claude warm orange
  if (p.startsWith("co")) return lv_color_hex(0x10a37f);  // Codex teal
  if (p.startsWith("gr")) return lv_color_hex(0x00d4ff);  // Grok icon cyan
  return lv_color_hex(0x9aa4b2);                           // neutral
}

// Provider badge: brand-colored chip with a drawn glyph — Claude starburst,
// Grok slash-X, Codex hexagon — instead of a 3-letter code.
lv_obj_t *makeProviderBadge(lv_obj_t *parent, const String &p) {
  lv_color_t col = providerColor(p);
  lv_obj_t *chip = lv_obj_create(parent);
  lv_obj_remove_style_all(chip);
  lv_obj_set_size(chip, 20, 20);
  lv_obj_set_style_bg_color(chip, col, 0);
  lv_obj_set_style_bg_opa(chip, LV_OPA_COVER, 0);
  lv_obj_set_style_radius(chip, 5, 0);
  lv_color_t ink = lv_color_hex(0x14100c);

  auto addLine = [&](lv_point_precise_t *pts, int n, int w) {
    lv_obj_t *ln = lv_line_create(chip);
    lv_line_set_points(ln, pts, n);
    lv_obj_set_style_line_width(ln, w, 0);
    lv_obj_set_style_line_rounded(ln, true, 0);
    lv_obj_set_style_line_color(ln, ink, 0);
  };

  if (p.startsWith("cl")) {
    // Starburst: four strokes through the centre.
    lv_point_precise_t *pts =
        (lv_point_precise_t *)lv_malloc(sizeof(lv_point_precise_t) * 8);
    if (!pts) return chip;
    pts[0] = {4, 10};    pts[1] = {16, 10};
    pts[2] = {10, 4};    pts[3] = {10, 16};
    pts[4] = {6, 6};     pts[5] = {14, 14};
    pts[6] = {14, 6};    pts[7] = {6, 14};
    lv_obj_add_event_cb(chip, freeLogoPoints, LV_EVENT_DELETE, pts);
    for (int i = 0; i < 4; i++) addLine(pts + i * 2, 2, 2);
  } else if (p.startsWith("gr")) {
    // Grok: the minimal bold slash from the app icon.
    lv_point_precise_t *pts =
        (lv_point_precise_t *)lv_malloc(sizeof(lv_point_precise_t) * 2);
    if (!pts) return chip;
    pts[0] = {7, 16};
    pts[1] = {13, 4};
    lv_obj_add_event_cb(chip, freeLogoPoints, LV_EVENT_DELETE, pts);
    addLine(pts, 2, 3);
  } else if (p.startsWith("co")) {
    // OpenAI-style knot: six skewed petals in hexagonal symmetry.
    lv_point_precise_t *pts =
        (lv_point_precise_t *)lv_malloc(sizeof(lv_point_precise_t) * 12);
    if (!pts) return chip;
    pts[0] = {(lv_value_precise_t)17.6, (lv_value_precise_t)10.0};
    pts[1] = {(lv_value_precise_t)11.8, (lv_value_precise_t)12.6};
    pts[2] = {(lv_value_precise_t)13.8, (lv_value_precise_t)16.6};
    pts[3] = {(lv_value_precise_t)8.6, (lv_value_precise_t)12.9};
    pts[4] = {(lv_value_precise_t)6.2, (lv_value_precise_t)16.6};
    pts[5] = {(lv_value_precise_t)6.8, (lv_value_precise_t)10.3};
    pts[6] = {(lv_value_precise_t)2.4, (lv_value_precise_t)10.0};
    pts[7] = {(lv_value_precise_t)8.2, (lv_value_precise_t)7.4};
    pts[8] = {(lv_value_precise_t)6.2, (lv_value_precise_t)3.4};
    pts[9] = {(lv_value_precise_t)11.4, (lv_value_precise_t)7.1};
    pts[10] = {(lv_value_precise_t)13.8, (lv_value_precise_t)3.4};
    pts[11] = {(lv_value_precise_t)13.2, (lv_value_precise_t)9.7};
    lv_obj_add_event_cb(chip, freeLogoPoints, LV_EVENT_DELETE, pts);
    for (int i = 0; i < 6; i++) addLine(pts + i * 2, 2, 2);
  } else {
    lv_obj_t *dot = lv_obj_create(chip);
    lv_obj_remove_style_all(dot);
    lv_obj_set_size(dot, 8, 8);
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(dot, ink, 0);
    lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
    lv_obj_center(dot);
  }
  return chip;
}

String titleFor(const statusfeed::JobStat &j) {
  for (auto &s : sessions)
    if (s.id.length() && s.id == j.sessionId) return s.title;
  if (j.prompt.length()) return j.prompt;
  return "session " + j.jobId;
}

lv_color_t feedColor() {
  switch (statusfeed::aggregate()) {
    case statusfeed::State::Live: return lv_palette_main(LV_PALETTE_GREEN);
    case statusfeed::State::Connecting: return lv_palette_main(LV_PALETTE_AMBER);
    case statusfeed::State::Failed: return lv_palette_main(LV_PALETTE_RED);
    default: return lv_palette_darken(LV_PALETTE_GREY, 2);
  }
}

lv_color_t wifiColor() {
  switch (wifi_mgr::state()) {
    case wifi_mgr::State::Connected: return lv_palette_main(LV_PALETTE_GREEN);
    case wifi_mgr::State::Connecting: return lv_palette_main(LV_PALETTE_AMBER);
    case wifi_mgr::State::Failed: return lv_palette_main(LV_PALETTE_RED);
    default: return lv_palette_darken(LV_PALETTE_GREY, 2);
  }
}

const char *battSymbol(int pct) {
  if (pct >= 90) return LV_SYMBOL_BATTERY_FULL;
  if (pct >= 65) return LV_SYMBOL_BATTERY_3;
  if (pct >= 40) return LV_SYMBOL_BATTERY_2;
  if (pct >= 15) return LV_SYMBOL_BATTERY_1;
  return LV_SYMBOL_BATTERY_EMPTY;
}

void updateHeader() {
  if (hdrTime) {
    if (timesync::valid()) {
      int h = 0, m = 0;
      timesync::nowLocal(&h, &m);
      char t[8];
      snprintf(t, sizeof(t), "%02d:%02d", h, m);
      lv_label_set_text(hdrTime, t);
    } else {
      lv_label_set_text(hdrTime, "");
    }
  }
  if (hdrMod)
    lv_label_set_text(hdrMod, keyboard::capsOn() ? "CAP"
                              : keyboard::symOn() ? "SYM"
                                                  : "");
  if (hdrBell) lv_obj_set_style_text_color(hdrBell, feedColor(), 0);
  if (hdrWifi) lv_obj_set_style_text_color(hdrWifi, wifiColor(), 0);
  if (hdrBatt) {
    auto bat = power::read();
    String t;
    if (bat.charging) t += LV_SYMBOL_CHARGE " ";
    if (bat.percent >= 0) {
      t += battSymbol(bat.percent);
      t += " " + String(bat.percent) + "%";
    } else {
      t += battSymbol(50);
      t += " ?";
    }
    lv_label_set_text(hdrBatt, t.c_str());
    lv_obj_set_style_text_color(
        hdrBatt,
        bat.percent >= 0 && bat.percent < 15 ? lv_palette_main(LV_PALETTE_RED)
                                             : lv_palette_main(LV_PALETTE_GREY),
        0);
  }
}

lv_obj_t *makeScreen() {
  lv_obj_t *scr = lv_obj_create(NULL);
  lv_obj_set_style_pad_all(scr, 0, 0);
  return scr;
}

// Top bar: title left; right side (in order) CAP/SYM, bell = beeper feed
// health, Wi-Fi glyph = network state, battery icon + real percentage.
void addHeader(lv_obj_t *scr, const char *title) {
  lv_obj_t *bar = lv_obj_create(scr);
  lv_obj_remove_style_all(bar);
  lv_obj_set_size(bar, LV_PCT(100), 24);
  lv_obj_set_style_bg_color(bar, lv_color_hex(0x1c1c22), 0);
  lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
  lv_obj_set_style_pad_hor(bar, 8, 0);

  lv_obj_t *t = lv_label_create(bar);
  lv_label_set_text(t, title);
  lv_obj_set_style_text_font(t, &lv_font_montserrat_14, 0);
  if (strcmp(title, "Agent Remote") == 0) {
    // Brand mark beside the home title.
    lv_obj_t *lg = makeLogo(bar, 18);
    lv_obj_align(lg, LV_ALIGN_LEFT_MID, -2, 0);
    lv_obj_align(t, LV_ALIGN_LEFT_MID, 19, 0);
  } else {
    lv_obj_align(t, LV_ALIGN_LEFT_MID, 0, 0);
  }

  lv_obj_t *right = lv_obj_create(bar);
  lv_obj_remove_style_all(right);
  lv_obj_set_size(right, LV_SIZE_CONTENT, LV_PCT(100));
  lv_obj_set_flex_flow(right, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(right, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_column(right, 8, 0);
  lv_obj_align(right, LV_ALIGN_RIGHT_MID, 0, 0);

  hdrTime = lv_label_create(right);
  lv_obj_set_style_text_font(hdrTime, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(hdrTime, lv_color_white(), 0);

  hdrMod = lv_label_create(right);
  lv_obj_set_style_text_font(hdrMod, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(hdrMod, lv_palette_main(LV_PALETTE_AMBER), 0);

  hdrBell = lv_label_create(right);
  lv_label_set_text(hdrBell, LV_SYMBOL_BELL);
  lv_obj_set_style_text_font(hdrBell, &lv_font_montserrat_12, 0);

  hdrWifi = lv_label_create(right);
  lv_label_set_text(hdrWifi, LV_SYMBOL_WIFI);
  lv_obj_set_style_text_font(hdrWifi, &lv_font_montserrat_12, 0);

  hdrBatt = lv_label_create(right);
  lv_obj_set_style_text_font(hdrBatt, &lv_font_montserrat_12, 0);

  updateHeader();
}

lv_obj_t *addBody(lv_obj_t *scr) {
  lv_obj_t *body = lv_obj_create(scr);
  lv_obj_remove_style_all(body);
  lv_obj_set_pos(body, 0, 24);
  lv_obj_set_size(body, LV_PCT(100), lv_display_get_vertical_resolution(NULL) - 24);
  lv_obj_set_style_pad_all(body, 6, 0);
  return body;
}

// ---- beeper ---------------------------------------------------------------

lv_obj_t *beeperWrap = nullptr;

void beeperClicked(lv_event_t *) { show(Screen::Menu); }

void jobCardClicked(lv_event_t *e) {
  int idx = (int)(intptr_t)lv_event_get_user_data(e);
  const auto &jobs = statusfeed::jobs();
  if (idx < 0 || idx >= (int)jobs.size()) return;
  if (jobs[idx].sessionId.isEmpty()) return;
  composeSession = jobs[idx].sessionId;
  composeDaemon = jobs[idx].daemon;
  tuiFrom = Screen::Beeper;
  show(Screen::LiveTui);
}

void blinkAnimCb(void *obj, int32_t v) {
  lv_obj_set_style_border_opa((lv_obj_t *)obj, (lv_opa_t)v, 0);
}

void bleApprove(lv_event_t *e) {
  bool allow = (bool)(intptr_t)lv_event_get_user_data(e);
  blebuddy::sendPermission(blebuddy::snap().promptId, allow);
  chime::play(allow ? chime::Cue::Status : chime::Cue::Error, cfg->soundCues,
              cfg->hapticCues);
}

// Card for the desktop-buddy BLE bridge: status line, and Approve/Deny
// buttons when a permission prompt is waiting.
void addBleCards() {
  const auto &b = blebuddy::snap();

  if (b.hasPrompt) {
    lv_obj_t *card = lv_obj_create(cardsBox);
    lv_obj_set_size(card, LV_PCT(100), 66);
    lv_obj_set_style_pad_all(card, 6, 0);
    lv_obj_set_style_radius(card, 8, 0);
    lv_obj_set_style_bg_color(card, lv_color_hex(0x2c2412), 0);
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_border_color(card, lv_palette_main(LV_PALETTE_AMBER), 0);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *title = lv_label_create(card);
    lv_label_set_text(title,
                      (String(LV_SYMBOL_BELL "  Approve: ") + b.promptTool).c_str());
    lv_obj_set_style_text_color(title, lv_palette_main(LV_PALETTE_AMBER), 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 0, 0);

    lv_obj_t *hint = lv_label_create(card);
    lv_label_set_text(hint, b.promptHint.c_str());
    lv_label_set_long_mode(hint, LV_LABEL_LONG_DOT);
    lv_obj_set_size(hint, LV_PCT(55), 15);
    lv_obj_set_style_text_font(hint, &lv_font_montserrat_12, 0);
    lv_obj_set_style_text_color(hint, lv_palette_main(LV_PALETTE_GREY), 0);
    lv_obj_align(hint, LV_ALIGN_BOTTOM_LEFT, 0, 0);

    const char *bl[] = {LV_SYMBOL_OK " Approve", LV_SYMBOL_CLOSE " Deny"};
    for (int i = 0; i < 2; i++) {
      lv_obj_t *btn = lv_button_create(card);
      lv_obj_set_size(btn, 96, 26);
      lv_obj_align(btn, LV_ALIGN_BOTTOM_RIGHT, i == 0 ? -104 : 0, 0);
      if (i == 1)
        lv_obj_set_style_bg_color(btn, lv_palette_main(LV_PALETTE_RED), 0);
      lv_obj_add_event_cb(btn, bleApprove, LV_EVENT_CLICKED,
                          (void *)(intptr_t)(i == 0));
      lv_obj_t *lb = lv_label_create(btn);
      lv_label_set_text(lb, bl[i]);
      lv_obj_set_style_text_font(lb, &lv_font_montserrat_12, 0);
      lv_obj_center(lb);
    }
  }

  lv_obj_t *card = lv_obj_create(cardsBox);
  lv_obj_set_size(card, LV_PCT(100), 50);
  lv_obj_set_style_pad_all(card, 6, 0);
  lv_obj_set_style_radius(card, 8, 0);
  lv_obj_set_style_bg_color(card, lv_color_hex(0x1a2733), 0);
  lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);

  lv_obj_t *tag = lv_label_create(card);
  lv_label_set_text(tag, "BLE");
  lv_obj_set_style_text_font(tag, &lv_font_montserrat_12, 0);
  lv_obj_set_style_bg_color(tag, lv_palette_main(LV_PALETTE_CYAN), 0);
  lv_obj_set_style_bg_opa(tag, LV_OPA_COVER, 0);
  lv_obj_set_style_radius(tag, 4, 0);
  lv_obj_set_style_pad_hor(tag, 5, 0);
  lv_obj_set_style_pad_ver(tag, 1, 0);
  lv_obj_set_style_text_color(tag, lv_color_black(), 0);
  lv_obj_align(tag, LV_ALIGN_TOP_LEFT, 0, 0);

  lv_obj_t *title = lv_label_create(card);
  String t = blebuddy::connected()
                 ? (b.msg.length() ? b.msg : "Claude desktop connected")
                 : "Waiting for Claude desktop...";
  lv_label_set_text(title, t.c_str());
  lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
  lv_obj_set_size(title, LV_PCT(80), 17);
  lv_obj_align(title, LV_ALIGN_TOP_LEFT, 30, 0);

  lv_obj_t *detail = lv_label_create(card);
  char d[80];
  snprintf(d, sizeof(d), "%d sessions   %d running   %d waiting   %uk today",
           b.total, b.running, b.waiting,
           (unsigned)(b.tokensToday / 1000));
  lv_label_set_text(detail, d);
  lv_obj_set_style_text_font(detail, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(detail, lv_palette_main(LV_PALETTE_GREY), 0);
  lv_obj_align(detail, LV_ALIGN_BOTTOM_LEFT, 30, 0);
}

void rebuildCards() {
  cardRefs.clear();
  lv_obj_clean(cardsBox);
  if (blebuddy::active()) addBleCards();
  const auto &jobs = statusfeed::jobs();

  bool showIdle = jobs.empty() && !blebuddy::active();
  // Whole-screen press = menu only when idle; with cards, knob focus
  // belongs to the cards and the side button opens the menu.
  if (beeperWrap) {
    lv_group_remove_obj(beeperWrap);
    if (jobs.empty()) lv_group_add_obj(grp, beeperWrap);
  }
  if (idleBox) (showIdle ? lv_obj_remove_flag : lv_obj_add_flag)(idleBox, LV_OBJ_FLAG_HIDDEN);
  if (showIdle) {
    auto fs = statusfeed::aggregate();
    bool unreachable = cfg && cfg->configured() && fs != statusfeed::State::Live;
    if (cfg && cfg->configured() && cfg->enabledDaemons() == 0) {
      lv_label_set_text(idleTitle, LV_SYMBOL_BELL "  Daemons disabled");
      lv_obj_set_style_text_color(idleTitle,
                                  lv_palette_main(LV_PALETTE_AMBER), 0);
      lv_label_set_text(idleSub, "Enable one: knob " LV_SYMBOL_RIGHT " Daemon");
    } else if (unreachable) {
      lv_label_set_text(idleTitle,
                        fs == statusfeed::State::Connecting
                            ? LV_SYMBOL_REFRESH "  Connecting to daemon..."
                            : LV_SYMBOL_WARNING "  Daemon unreachable");
      lv_obj_set_style_text_color(idleTitle, lv_palette_main(LV_PALETTE_RED), 0);
      String urls;
      for (int i = 0; i < cfg->daemonCount; i++) {
        if (i) urls += "  ";
        urls += cfg->daemonName(i);
      }
      lv_label_set_text(idleSub,
                        (urls + "\nCheck URL: knob " LV_SYMBOL_RIGHT
                                " Daemon").c_str());
    } else {
      lv_label_set_text(idleTitle, LV_SYMBOL_BELL "  All quiet");
      lv_obj_set_style_text_color(idleTitle, lv_color_white(), 0);
      lv_label_set_text(idleSub, ticker.length()
                                     ? ("Last: " + ticker).c_str()
                                     : "Waiting for agent activity");
    }
    return;
  }

  int shown = 0;
  int jobIdx = -1;
  for (const auto &j : jobs) {
    jobIdx++;
    if (shown++ >= 4) break;
    bool needs = j.pendingPermission || j.pendingQuestion;

    lv_obj_t *card = lv_obj_create(cardsBox);
    // Focusable: rotate to a card, press to open its Live TUI.
    lv_group_add_obj(grp, card);
    lv_obj_set_style_border_width(card, 2, LV_STATE_FOCUS_KEY);
    lv_obj_set_style_border_color(card, lv_palette_main(LV_PALETTE_PURPLE),
                                  LV_STATE_FOCUS_KEY);
    lv_obj_add_event_cb(card, jobCardClicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)jobIdx);
    lv_obj_set_size(card, LV_PCT(100), 50);
    lv_obj_set_style_pad_all(card, 6, 0);
    lv_obj_set_style_radius(card, 8, 0);
    lv_obj_set_style_bg_color(card, lv_color_hex(0x232330), 0);
    lv_obj_set_style_border_width(card, needs ? 2 : 0, 0);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    if (needs) {
      lv_obj_set_style_border_color(card, lv_palette_main(LV_PALETTE_AMBER), 0);
      lv_anim_t a;
      lv_anim_init(&a);
      lv_anim_set_var(&a, card);
      lv_anim_set_exec_cb(&a, blinkAnimCb);
      lv_anim_set_values(&a, LV_OPA_20, LV_OPA_COVER);
      lv_anim_set_duration(&a, 400);
      lv_anim_set_playback_duration(&a, 400);
      lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
      lv_anim_start(&a);
    }

    lv_obj_t *tag = makeProviderBadge(card, j.provider);
    lv_obj_align(tag, LV_ALIGN_TOP_LEFT, 0, 0);

    lv_obj_t *title = lv_label_create(card);
    lv_label_set_text(title, titleFor(j).c_str());
    lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
    lv_obj_set_size(title, LV_PCT(64), 17);  // exactly one line
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 30, 0);

    lv_obj_t *elapsed = lv_label_create(card);
    char e[24];
    if (j.queued > 0)
      snprintf(e, sizeof(e), "%d:%02d  +%d", j.elapsedS / 60, j.elapsedS % 60,
               j.queued);
    else
      snprintf(e, sizeof(e), "%d:%02d", j.elapsedS / 60, j.elapsedS % 60);
    lv_label_set_text(elapsed, e);
    lv_obj_set_style_text_font(elapsed, &lv_font_montserrat_12, 0);
    lv_obj_set_style_text_color(elapsed, lv_palette_main(LV_PALETTE_GREY), 0);
    lv_obj_align(elapsed, LV_ALIGN_TOP_RIGHT, 0, 0);
    cardRefs.push_back({j.jobId, elapsed});

    lv_obj_t *detail = lv_label_create(card);
    String d;
    lv_color_t dc = lv_palette_main(LV_PALETTE_GREY);
    if (needs) {
      d = j.pendingQuestion ? LV_SYMBOL_BELL "  QUESTION - answer needed"
                            : LV_SYMBOL_BELL "  PERMISSION - answer needed";
      dc = lv_palette_main(LV_PALETTE_AMBER);
    } else if (j.tool.length()) {
      d = j.tool;
      if (j.toolDetail.length()) d += ": " + j.toolDetail;
      dc = lv_palette_main(LV_PALETTE_GREEN);
    } else {
      d = j.phase.length() ? j.phase : "working";
      if (j.phaseDetail.length()) d += " " + j.phaseDetail;
    }
    lv_label_set_text(detail, d.c_str());
    lv_label_set_long_mode(detail, LV_LABEL_LONG_DOT);
    lv_obj_set_size(detail, LV_PCT(85), 15);  // one line, "..." past that
    lv_obj_set_style_text_font(detail, &lv_font_montserrat_12, 0);
    lv_obj_set_style_text_color(detail, dc, 0);
    lv_obj_align(detail, LV_ALIGN_BOTTOM_LEFT, 30, 0);
  }
}

lv_obj_t *buildBeeper() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Agent Remote");
  lv_obj_t *body = addBody(scr);

  // Whole screen is one clickable target: knob press opens the menu.
  lv_obj_t *btn = lv_button_create(body);
  lv_obj_remove_style_all(btn);
  lv_obj_set_size(btn, LV_PCT(100), LV_PCT(100));
  lv_obj_add_event_cb(btn, beeperClicked, LV_EVENT_CLICKED, NULL);
  lv_group_add_obj(grp, btn);
  beeperWrap = btn;

  cardsBox = lv_obj_create(btn);
  lv_obj_remove_style_all(cardsBox);
  lv_obj_set_size(cardsBox, LV_PCT(100), LV_PCT(100));
  lv_obj_set_flex_flow(cardsBox, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(cardsBox, 5, 0);

  idleBox = lv_obj_create(btn);
  lv_obj_remove_style_all(idleBox);
  lv_obj_set_size(idleBox, LV_PCT(100), LV_PCT(100));
  idleTitle = lv_label_create(idleBox);
  lv_obj_set_style_text_font(idleTitle, &lv_font_montserrat_18, 0);
  lv_obj_align(idleTitle, LV_ALIGN_CENTER, 0, -20);
  idleSub = lv_label_create(idleBox);
  lv_obj_set_style_text_font(idleSub, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(idleSub, lv_palette_main(LV_PALETTE_GREY), 0);
  lv_obj_set_style_text_align(idleSub, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_align(idleSub, LV_ALIGN_CENTER, 0, 24);

  rebuildCards();
  return scr;
}

// ---- menu -----------------------------------------------------------------

struct MenuDef {
  const char *sym;
  const char *label;
  Screen target;
};
const MenuDef kMenu[] = {
    {LV_SYMBOL_BELL, "Beeper", Screen::Beeper},
    {LV_SYMBOL_LIST, "Sessions", Screen::Sessions},
    {LV_SYMBOL_AUDIO, "Chimes", Screen::Chimes},
    {LV_SYMBOL_WIFI, "Wi-Fi", Screen::WifiScan},
    {LV_SYMBOL_SETTINGS, "Daemon", Screen::Daemon},
    {LV_SYMBOL_BLUETOOTH, "BLE", Screen::Bluetooth},
    {LV_SYMBOL_DOWNLOAD, "Web", Screen::WebUi},
    {LV_SYMBOL_CHARGE, "Usage", Screen::Usage},
    {LV_SYMBOL_FILE, "Diag", Screen::Diag},
    {LV_SYMBOL_POWER, "Power", Screen::Power},
};

void menuClicked(lv_event_t *e) {
  int idx = (int)(intptr_t)lv_event_get_user_data(e);
  show(kMenu[idx].target);
}

lv_obj_t *buildMenu() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Menu");
  lv_obj_t *body = addBody(scr);
  lv_obj_set_flex_flow(body, LV_FLEX_FLOW_ROW_WRAP);
  lv_obj_set_style_pad_column(body, 8, 0);
  lv_obj_set_style_pad_row(body, 6, 0);
  lv_obj_set_style_pad_hor(body, 12, 0);

  for (int i = 0; i < (int)(sizeof(kMenu) / sizeof(kMenu[0])); i++) {
    lv_obj_t *btn = lv_button_create(body);
    lv_obj_set_size(btn, 104, 84);
    lv_obj_set_style_radius(btn, 14, 0);
    lv_obj_set_style_bg_color(btn, lv_color_hex(0x232330), 0);
    lv_obj_set_style_bg_color(btn, lv_palette_main(LV_PALETTE_PURPLE),
                              LV_STATE_FOCUS_KEY);
    lv_obj_add_event_cb(btn, menuClicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);

    if (kMenu[i].target == Screen::Daemon) {
      lv_obj_t *lg = makeLogo(btn, 34);
      lv_obj_align(lg, LV_ALIGN_TOP_MID, 0, 2);
    } else {
      lv_obj_t *ic = lv_label_create(btn);
      lv_label_set_text(ic, kMenu[i].sym);
      lv_obj_set_style_text_font(ic, &lv_font_montserrat_24, 0);
      if (kMenu[i].target == Screen::Power)
        lv_obj_set_style_text_color(ic, lv_palette_main(LV_PALETTE_RED), 0);
      lv_obj_align(ic, LV_ALIGN_TOP_MID, 0, 4);
    }

    lv_obj_t *lb = lv_label_create(btn);
    lv_label_set_text(lb, kMenu[i].label);
    lv_obj_set_style_text_font(lb, &lv_font_montserrat_12, 0);
    lv_obj_align(lb, LV_ALIGN_BOTTOM_MID, 0, -2);
  }
  return scr;
}

// ---- sessions / compose ----------------------------------------------------

void sessionClicked(lv_event_t *e) {
  int idx = (int)(intptr_t)lv_event_get_user_data(e);
  if (idx >= 0 && idx < (int)sessions.size()) {
    composeSession = sessions[idx].id;
    composeDaemon = sessions[idx].daemon;
    tuiFrom = Screen::Sessions;
    show(Screen::LiveTui);
  }
}

// Session fetch runs on its own task so the screen transition is instant:
// enter with a spinner, populate when the HTTP round-trip lands.
lv_obj_t *sessList = nullptr;
lv_obj_t *sessSpinner = nullptr;
volatile int sessFetchState = 0;  // 0 idle, 1 running, 2 ok, 3 failed
std::vector<SessionRow> sessFetchResult;
String sessFetchErr;

void runSessionsFetch() {
  std::vector<SessionRow> all;
  String err;
  bool ok = false;
  dlog::logf("[sess] fetch start (heap %u)", (unsigned)ESP.getFreeHeap());
  // All-https configs: every held TLS session starves the handshake, so
  // pause them all for the duration of the fetch, not just the target.
  for (int d = 0; d < agentapi::daemonCount(); d++)
    if (cfg->daemons[d].enabled) pauseHttpsFeed(d, 15000);
  for (int d = 0; d < agentapi::daemonCount(); d++) {
    if (!cfg->daemons[d].enabled) continue;
    std::vector<SessionRow> rows;
    String e;
    if (agentapi::fetchSessions(d, &rows, &e)) {
      ok = true;
      for (auto &r : rows) all.push_back(r);
    } else if (err.isEmpty()) {
      err = e;
    }
    dlog::logf("[sess] d%d: %d rows (heap %u)", d, (int)all.size(),
               (unsigned)ESP.getFreeHeap());
  }
  // Newest first across every daemon (ISO timestamps sort lexicographically;
  // rows without one sink to the bottom).
  std::sort(all.begin(), all.end(), [](const SessionRow &a, const SessionRow &b) {
    return a.lastActive > b.lastActive;
  });
  sessFetchResult.swap(all);
  sessFetchErr = err;
  sessFetchState = ok ? 2 : 3;
}

uint32_t sessFetchStartedAt = 0;

void startSessionsFetch() {
  if (sessFetchState == 1) return;
  sessFetchState = 1;
  sessFetchStartedAt = millis();
  if (!submitJob(JOB_SESSIONS)) {
    sessFetchState = 3;
    sessFetchErr = "worker queue full";
  }
}

void fillSessionsList() {
  if (!sessList) return;
  lv_obj_clean(sessList);
  lv_obj_t *firstBtn = nullptr;
  if (sessions.empty()) {
    lv_list_add_text(sessList, sessFetchErr.length() ? sessFetchErr.c_str()
                                                     : "No sessions");
  }
  int shown = (int)sessions.size();
  if (shown > 50) shown = 50;  // keep widget count bounded
  for (int i = 0; i < shown; i++) {
    String row = String(sessions[i].working ? LV_SYMBOL_PLAY " " : "") +
                 sessions[i].title;
    if (cfg->daemonCount > 1)
      row += "   [" + cfg->daemonName(sessions[i].daemon).substring(0, 6) + "]";
    lv_obj_t *btn = lv_list_add_button(sessList, NULL, row.c_str());
    lv_obj_t *badge = makeProviderBadge(btn, sessions[i].provider);
    lv_obj_move_to_index(badge, 0);  // icon before the label
    lv_obj_set_style_bg_color(btn, lv_palette_main(LV_PALETTE_PURPLE),
                              LV_STATE_FOCUS_KEY);
    lv_obj_add_event_cb(btn, sessionClicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);
    if (!firstBtn) firstBtn = btn;
  }
  // Knob-press / Enter must work without rotating first.
  if (firstBtn) lv_group_focus_obj(firstBtn);
}

lv_obj_t *buildSessions() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Sessions");
  lv_obj_t *body = addBody(scr);

  sessList = lv_list_create(body);
  // Transparent chrome only — removing the whole main style also wipes the
  // list's flex layout and every row stacks at (0,0).
  lv_obj_set_style_bg_opa(sessList, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(sessList, 0, 0);
  lv_obj_set_style_pad_all(sessList, 0, 0);
  lv_obj_set_size(sessList, LV_PCT(100), LV_PCT(100));

  sessSpinner = lv_spinner_create(body);
  lv_obj_set_size(sessSpinner, 40, 40);
  lv_obj_center(sessSpinner);
  lv_group_remove_obj(sessSpinner);  // must never take focus from list rows

  // Stale-while-revalidate: cached rows show immediately, refresh follows.
  if (!sessions.empty()) fillSessionsList();
  startSessionsFetch();
  return scr;
}

lv_obj_t *replyNote = nullptr;

// Replies post from a task — a slow daemon must not freeze the composer.
volatile int replyState = 0;  // 0 idle, 1 sending, 2 ok, 3 failed
String replyText, replyErr;

void runReply() {
  pauseHttpsFeed(composeDaemon, 8000);
  String err;
  bool ok = agentapi::sendPrompt(composeDaemon, composeSession,
                                 replyText.c_str(), &err);
  replyErr = err;
  replyState = ok ? 2 : 3;
}

void sendReply(const char *txt) {
  if (!txt || !*txt || replyState == 1) return;
  replyText = txt;
  if (replyNote) lv_label_set_text(replyNote, "Sending...");
  replyState = 1;
  if (!submitJob(JOB_REPLY)) {
    replyState = 0;
    if (replyNote) lv_label_set_text(replyNote, "Busy - try again");
  }
}

void customReplyReady(lv_event_t *e) {
  lv_obj_t *ta = (lv_obj_t *)lv_event_get_user_data(e);
  sendReply(lv_textarea_get_text(ta));
}

lv_obj_t *buildCompose() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Reply");
  lv_obj_t *body = addBody(scr);

  lv_obj_t *hint = lv_label_create(body);
  String h = "To: ";
  for (auto &s : sessions)
    if (s.id == composeSession) h += s.title;
  lv_label_set_text(hint, h.c_str());
  lv_label_set_long_mode(hint, LV_LABEL_LONG_DOT);
  lv_obj_set_width(hint, LV_PCT(100));
  lv_obj_set_style_text_font(hint, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(hint, lv_palette_main(LV_PALETTE_GREY), 0);
  lv_obj_align(hint, LV_ALIGN_TOP_LEFT, 0, 0);

  lv_obj_t *ta = lv_textarea_create(body);
  lv_textarea_set_one_line(ta, true);
  lv_textarea_set_placeholder_text(ta, "Message... (Enter = send)");
  lv_obj_set_width(ta, LV_PCT(100));
  lv_obj_align(ta, LV_ALIGN_TOP_MID, 0, 26);
  lv_obj_add_event_cb(ta, customReplyReady, LV_EVENT_READY, ta);
  lv_group_focus_obj(ta);

  replyNote = lv_label_create(body);
  lv_label_set_text(replyNote, "");
  lv_obj_set_style_text_color(replyNote, lv_palette_main(LV_PALETTE_RED), 0);
  lv_obj_set_style_text_font(replyNote, &lv_font_montserrat_12, 0);
  lv_obj_align(replyNote, LV_ALIGN_BOTTOM_LEFT, 0, 0);
  return scr;
}

// ---- live tui ---------------------------------------------------------------

lv_obj_t *tuiLabel = nullptr;
lv_obj_t *tuiPane = nullptr;
volatile int tuiFetchState = 0;  // 0 idle, 1 running, 2 done
String tuiFetchText;
String tuiShown;
uint32_t tuiLastPoll = 0;

void runTuiFetch() {
  String text, err;
  bool attached = false;
  if (agentapi::fetchTui(composeDaemon, composeSession, &text, &attached, &err) && attached) {
    tuiFetchText = text;
  } else {
    tuiFetchText = err.length() ? ("[ " + err + " ]")
                                : "[ no live TUI for this session ]";
  }
  tuiFetchState = 2;
}

void startTuiFetch() {
  if (tuiFetchState != 0) return;
  // Rolling pause: TUI polls every 1.5 s, so an https daemon's feed stays
  // down while this screen is open and reconnects after leaving it.
  pauseHttpsFeed(composeDaemon, 5000);
  tuiFetchState = 1;
  if (!submitJob(JOB_TUI)) tuiFetchState = 0;  // retry next poll
}

void tuiReplyClicked(lv_event_t *) { show(Screen::Compose); }

// Programmatic lv_obj_scroll_by has no bounds — clamp the step to the
// remaining content so the knob stops at the edges instead of scrolling
// into the void. viewDx/viewDy are view-movement: +x = right, +y = down.
void scrollByClamped(lv_obj_t *obj, int viewDx, int viewDy) {
  if (viewDx > 0) {
    int m = (int)lv_obj_get_scroll_right(obj);
    if (m < 0) m = 0;
    if (viewDx > m) viewDx = m;
  } else if (viewDx < 0) {
    int m = (int)lv_obj_get_scroll_left(obj);
    if (m < 0) m = 0;
    if (-viewDx > m) viewDx = -m;
  }
  if (viewDy > 0) {
    int m = (int)lv_obj_get_scroll_bottom(obj);
    if (m < 0) m = 0;
    if (viewDy > m) viewDy = m;
  } else if (viewDy < 0) {
    int m = (int)lv_obj_get_scroll_top(obj);
    if (m < 0) m = 0;
    if (-viewDy > m) viewDy = -m;
  }
  if (viewDx || viewDy) lv_obj_scroll_by(obj, -viewDx, -viewDy, LV_ANIM_ON);
}

// Knob rotation scrolls the pane; clockwise = down. Holding Sym (the orange
// triangle) turns the same rotation into horizontal scrolling for wide TUI
// lines. Consumed here so LVGL focus navigation never sees it.
bool tuiRotary(int delta) {
  if (!tuiPane) return false;
  if (keyboard::symOn()) {
    scrollByClamped(tuiPane, delta * 60, 0);
  } else {
    scrollByClamped(tuiPane, 0, delta * 40);
  }
  return true;
}

lv_obj_t *buildLiveTui() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Live TUI");
  lv_obj_t *body = addBody(scr);

  lv_obj_t *btn = lv_button_create(body);
  lv_obj_remove_style_all(btn);
  lv_obj_set_size(btn, LV_PCT(100), LV_PCT(100));
  lv_obj_add_event_cb(btn, tuiReplyClicked, LV_EVENT_CLICKED, NULL);
  lv_group_add_obj(grp, btn);

  tuiPane = lv_obj_create(btn);
  lv_obj_set_size(tuiPane, LV_PCT(100),
                  lv_display_get_vertical_resolution(NULL) - 24 - 30);
  lv_obj_set_style_bg_color(tuiPane, lv_color_hex(0x0d0d12), 0);
  lv_obj_set_style_border_width(tuiPane, 0, 0);
  lv_obj_set_style_radius(tuiPane, 6, 0);
  lv_obj_set_style_pad_all(tuiPane, 4, 0);
  tuiLabel = lv_label_create(tuiPane);
  lv_label_set_text(tuiLabel, "Loading...");
  lv_obj_set_width(tuiLabel, LV_SIZE_CONTENT);  // no wrap — Sym+knob pans
  lv_obj_set_style_text_font(tuiLabel, &lv_font_unscii_8, 0);
  lv_obj_set_style_text_color(tuiLabel, lv_color_hex(0xd8d8e0), 0);
  tuiShown = "";

  lv_obj_t *hint = lv_label_create(btn);
  lv_label_set_text(hint, "Knob = reply     Bksp = back");
  lv_obj_set_style_text_font(hint, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(hint, lv_palette_main(LV_PALETTE_GREY), 0);
  lv_obj_align(hint, LV_ALIGN_BOTTOM_MID, 0, 0);

  tuiLastPoll = 0;  // fetch immediately from tick
  return scr;
}

// ---- chimes ----------------------------------------------------------------

void cueClicked(lv_event_t *e) {
  int cue = (int)(intptr_t)lv_event_get_user_data(e);
  chime::play((chime::Cue)cue, true, cfg->hapticCues);
}

void soundToggled(lv_event_t *e) {
  lv_obj_t *sw = (lv_obj_t *)lv_event_get_target(e);
  cfg->soundCues = lv_obj_has_state(sw, LV_STATE_CHECKED);
  persist();
  chime::setEnabled(cfg->soundCues, cfg->hapticCues);
}

lv_obj_t *buildChimes() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Chimes");
  lv_obj_t *body = addBody(scr);
  lv_obj_set_flex_flow(body, LV_FLEX_FLOW_ROW_WRAP);
  lv_obj_set_style_pad_column(body, 8, 0);
  lv_obj_set_style_pad_row(body, 8, 0);

  const char *names[] = {"Status", "Done", "Error", "Attention"};
  for (int i = 0; i < 4; i++) {
    lv_obj_t *btn = lv_button_create(body);
    lv_obj_set_size(btn, 108, 40);
    lv_obj_add_event_cb(btn, cueClicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);
    lv_obj_t *lb = lv_label_create(btn);
    lv_label_set_text(lb, names[i]);
    lv_obj_center(lb);
  }

  lv_obj_t *row = lv_obj_create(body);
  lv_obj_remove_style_all(row);
  lv_obj_set_size(row, LV_PCT(100), 40);
  lv_obj_t *lb = lv_label_create(row);
  lv_label_set_text(lb, "Sound");
  lv_obj_align(lb, LV_ALIGN_LEFT_MID, 4, 0);
  lv_obj_t *sw = lv_switch_create(row);
  if (cfg->soundCues) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, soundToggled, LV_EVENT_VALUE_CHANGED, NULL);
  lv_obj_align(sw, LV_ALIGN_LEFT_MID, 80, 0);

  // Volume: focus with the knob, click to edit, rotate to adjust.
  lv_obj_t *vl = lv_label_create(row);
  lv_label_set_text(vl, "Volume");
  lv_obj_align(vl, LV_ALIGN_LEFT_MID, 180, 0);
  lv_obj_t *slider = lv_slider_create(row);
  lv_slider_set_range(slider, 0, 100);
  lv_slider_set_value(slider, cfg->volume, LV_ANIM_OFF);
  lv_obj_set_size(slider, 160, 10);
  lv_obj_align(slider, LV_ALIGN_LEFT_MID, 250, 0);
  lv_obj_add_event_cb(
      slider,
      [](lv_event_t *e) {
        lv_obj_t *s = (lv_obj_t *)lv_event_get_target(e);
        cfg->volume = (uint8_t)lv_slider_get_value(s);
        chime::setVolume(cfg->volume);
        persist();
      },
      LV_EVENT_VALUE_CHANGED, NULL);
  return scr;
}

// ---- bluetooth (claude-desktop-buddy) --------------------------------------

lv_obj_t *bleState = nullptr;

void bleToggled(lv_event_t *e) {
  lv_obj_t *sw = (lv_obj_t *)lv_event_get_target(e);
  cfg->bleMode = lv_obj_has_state(sw, LV_STATE_CHECKED);
  persist();
  if (cfg->bleMode) blebuddy::begin();
  else blebuddy::stop();
}

String bleStateText() {
  if (!blebuddy::active()) return "Off";
  if (!blebuddy::connected()) return "Advertising - waiting for Claude desktop";
  String t = "Connected";
  if (blebuddy::owner().length()) t += " to " + blebuddy::owner() + "'s Claude";
  return t;
}

lv_obj_t *buildBluetooth() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Bluetooth buddy");
  lv_obj_t *body = addBody(scr);
  lv_obj_set_flex_flow(body, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(body, 8, 0);

  lv_obj_t *row = lv_obj_create(body);
  lv_obj_remove_style_all(row);
  lv_obj_set_size(row, LV_PCT(100), 32);
  lv_obj_t *lb = lv_label_create(row);
  lv_label_set_text(lb, "BLE bridge");
  lv_obj_align(lb, LV_ALIGN_LEFT_MID, 4, 0);
  lv_obj_t *sw = lv_switch_create(row);
  if (cfg->bleMode) lv_obj_add_state(sw, LV_STATE_CHECKED);
  lv_obj_add_event_cb(sw, bleToggled, LV_EVENT_VALUE_CHANGED, NULL);
  lv_obj_align(sw, LV_ALIGN_LEFT_MID, 120, 0);

  bleState = lv_label_create(body);
  lv_label_set_text(bleState, bleStateText().c_str());
  lv_obj_set_style_text_color(bleState, lv_palette_main(LV_PALETTE_GREY), 0);

  lv_obj_t *hint = lv_label_create(body);
  lv_label_set_text(hint,
                    "Pair from Claude for macOS/Windows:\n"
                    "Help " LV_SYMBOL_RIGHT " Enable Developer Mode, then\n"
                    "Developer " LV_SYMBOL_RIGHT " Open Hardware Buddy... "
                    LV_SYMBOL_RIGHT " Connect.\n"
                    "Permission prompts become Approve/Deny on the beeper.");
  lv_obj_set_style_text_font(hint, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(hint, lv_palette_main(LV_PALETTE_GREY), 0);
  return scr;
}

// ---- usage ------------------------------------------------------------------

lv_obj_t *usageBox = nullptr;
lv_obj_t *usageSpinner = nullptr;
volatile int usageFetchState = 0;  // 0 idle, 1 running, 2 ok, 3 failed
volatile uint32_t usageGen = 0;    // bumps as each daemon's answer lands
SemaphoreHandle_t usageMx = nullptr;
std::vector<UsageBucket> usageResult;
String usageErr;

void runUsageFetch() {
  // One daemon at a time, rendered as each answers — never concurrent.
  // Buckets dedupe by title (the same subscription seen from two hosts
  // shows once, at the higher percentage).
  std::vector<UsageBucket> acc;
  String err;
  bool any = false;
  for (int d = 0; d < cfg->daemonCount; d++) {
    if (!cfg->daemons[d].enabled) continue;
    pauseHttpsFeed(d, 30000);
    std::vector<UsageBucket> rows;
    String e;
    if (agentapi::fetchUsage(d, &rows, &e)) {
      any = true;
      for (auto &u : rows) {
        bool dup = false;
        for (auto &v : acc) {
          if (v.title == u.title) {
            dup = true;
            if (u.percent > v.percent) v = u;
          }
        }
        if (!dup) acc.push_back(u);
      }
      xSemaphoreTake(usageMx, portMAX_DELAY);
      usageResult = acc;
      xSemaphoreGive(usageMx);
      usageGen++;
    } else if (err.isEmpty()) {
      err = e;
    }
  }
  usageErr = err;
  usageFetchState = any ? 2 : 3;
}

void fillUsage() {
  if (!usageBox) return;
  lv_obj_clean(usageBox);
  std::vector<UsageBucket> snapshot;
  if (usageMx) {
    xSemaphoreTake(usageMx, portMAX_DELAY);
    snapshot = usageResult;
    xSemaphoreGive(usageMx);
  }
  if (snapshot.empty()) {
    lv_obj_t *lb = lv_label_create(usageBox);
    lv_label_set_text(lb, usageErr.length() ? usageErr.c_str() : "No usage data");
    return;
  }
  for (auto &u : snapshot) {
    lv_obj_t *row = lv_obj_create(usageBox);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, LV_PCT(100), 40);

    lv_obj_t *t = lv_label_create(row);
    lv_label_set_text(t, u.title.c_str());
    lv_label_set_long_mode(t, LV_LABEL_LONG_DOT);
    lv_obj_set_size(t, LV_PCT(55), 16);
    lv_obj_set_style_text_font(t, &lv_font_montserrat_12, 0);
    lv_obj_align(t, LV_ALIGN_TOP_LEFT, 0, 0);

    lv_obj_t *bar = lv_bar_create(row);
    lv_bar_set_range(bar, 0, 100);
    lv_bar_set_value(bar, u.percent, LV_ANIM_ON);
    lv_obj_set_size(bar, 120, 10);
    lv_obj_align(bar, LV_ALIGN_TOP_RIGHT, -56, 3);
    lv_color_t col = u.percent >= 90 ? lv_palette_main(LV_PALETTE_RED)
                     : u.percent >= 70 ? lv_palette_main(LV_PALETTE_AMBER)
                                       : lv_palette_main(LV_PALETTE_GREEN);
    if (u.severity != "normal") col = lv_palette_main(LV_PALETTE_AMBER);
    lv_obj_set_style_bg_color(bar, col, LV_PART_INDICATOR);

    lv_obj_t *pc = lv_label_create(row);
    lv_label_set_text(pc, (String(u.percent) + "%").c_str());
    lv_obj_set_style_text_font(pc, &lv_font_montserrat_12, 0);
    lv_obj_align(pc, LV_ALIGN_TOP_RIGHT, -8, 0);

    lv_obj_t *r = lv_label_create(row);
    lv_label_set_text(r, u.resets.c_str());
    lv_label_set_long_mode(r, LV_LABEL_LONG_DOT);
    lv_obj_set_size(r, LV_PCT(60), 14);
    lv_obj_set_style_text_font(r, &lv_font_montserrat_12, 0);
    lv_obj_set_style_text_color(r, lv_palette_main(LV_PALETTE_GREY), 0);
    lv_obj_align(r, LV_ALIGN_TOP_LEFT, 0, 18);
  }
}

// Knob scrolls the bucket list (edge-clamped like the other panes).
bool usageRotary(int delta) {
  if (!usageBox) return false;
  scrollByClamped(usageBox, 0, delta * 40);
  return true;
}

lv_obj_t *buildUsage() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Usage");
  lv_obj_t *body = addBody(scr);

  usageBox = lv_obj_create(body);
  lv_obj_remove_style_all(usageBox);
  lv_obj_set_size(usageBox, LV_PCT(100), LV_PCT(100));
  lv_obj_set_flex_flow(usageBox, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(usageBox, 4, 0);

  usageSpinner = lv_spinner_create(body);
  lv_obj_set_size(usageSpinner, 40, 40);
  lv_obj_center(usageSpinner);
  lv_group_remove_obj(usageSpinner);  // must never take focus from list rows

  // Stale-while-revalidate: the daemon's usage scrape takes seconds, so
  // last-known buckets render immediately (bars animate to their values)
  // and the refresh replaces them quietly when it lands.
  if (!usageResult.empty()) {
    lv_obj_add_flag(usageSpinner, LV_OBJ_FLAG_HIDDEN);
    fillUsage();
  }
  if (usageFetchState != 1) {
    usageFetchState = 1;
    if (!submitJob(JOB_USAGE)) {
      usageFetchState = 0;
      lv_obj_add_flag(usageSpinner, LV_OBJ_FLAG_HIDDEN);
      usageErr = "worker busy - reopen to retry";
      fillUsage();
    }
  }
  return scr;
}

// ---- web ui (SD manager) ----------------------------------------------------

lv_obj_t *buildWebUi() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Web SD manager");
  lv_obj_t *body = addBody(scr);

  // Feeds down while serving: frees their sockets/TLS and keeps the shared
  // SPI bus calmer during SD streaming. They reconnect after leaving.
  for (int i = 0; i < cfg->daemonCount; i++)
    statusfeed::pause(i, 3600000);
  webui::begin();

  lv_obj_t *t = lv_label_create(body);
  if (wifi_mgr::state() == wifi_mgr::State::Connected) {
    lv_label_set_text(t, ("Open in a browser:\n" + webui::url()).c_str());
  } else {
    lv_label_set_text(t, "Wi-Fi is not connected.");
  }
  lv_obj_set_style_text_font(t, &lv_font_montserrat_18, 0);
  lv_obj_align(t, LV_ALIGN_TOP_LEFT, 4, 8);

  lv_obj_t *cred = lv_label_create(body);
  lv_label_set_text(
      cred, ("User: pager    Password: " + webui::password()).c_str());
  lv_obj_set_style_text_color(cred, lv_palette_main(LV_PALETTE_AMBER), 0);
  lv_obj_align(cred, LV_ALIGN_TOP_LEFT, 4, 78);

  lv_obj_t *hint = lv_label_create(body);
  lv_label_set_text(hint,
                    sdconfig::present()
                        ? "Browse / upload / delete SD files.\nBack stops the server."
                        : "No SD card detected.");
  lv_obj_set_style_text_font(hint, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(hint, lv_palette_main(LV_PALETTE_GREY), 0);
  lv_obj_align(hint, LV_ALIGN_BOTTOM_LEFT, 4, -4);
  return scr;
}

// ---- diag -------------------------------------------------------------------

lv_obj_t *diagNote = nullptr;
lv_obj_t *diagText = nullptr;
lv_obj_t *diagBox = nullptr;

// Knob scrolls the log (Sym+knob = horizontal, like the Live TUI).
bool diagRotary(int delta) {
  if (!diagBox) return false;
  if (keyboard::symOn()) {
    scrollByClamped(diagBox, delta * 60, 0);
  } else {
    scrollByClamped(diagBox, 0, delta * 40);
  }
  return true;
}

// Log upload runs on a task: 3 daemons × a slow tunnel would freeze the
// UI for tens of seconds otherwise.
volatile int diagUpState = 0;  // 0 idle, 1 running, 2 done
String diagUpNote;

void runDiagUpload() {
  // Every enabled daemon gets the log — with several daemons there is no
  // telling which host the person debugging can actually read.
  String note, log = dlog::dump();
  int okCount = 0;
  for (int d = 0; d < cfg->daemonCount; d++) {
    if (!cfg->daemons[d].enabled) continue;
    pauseHttpsFeed(d, 10000);
    String path, err;
    if (agentapi::uploadText(d, "pager-log.txt", log, &path, &err)) {
      okCount++;
    } else if (note.isEmpty()) {
      note = err;
    }
  }
  diagUpNote = okCount ? ("Uploaded to " + String(okCount) + " daemon(s)")
                       : ("Upload failed: " + note);
  diagUpState = 2;
}

void diagUpload(lv_event_t *) {
  if (diagUpState == 1) return;
  if (diagNote) lv_label_set_text(diagNote, "Uploading...");
  diagUpState = 1;
  if (!submitJob(JOB_DIAG)) {
    diagUpState = 0;
    if (diagNote) lv_label_set_text(diagNote, "Busy - try again");
  }
}

void diagRefresh(lv_event_t *) {
  if (!diagText) return;
  String all = dlog::dump();
  int from = all.length() > 3000 ? all.length() - 3000 : 0;
  lv_label_set_text(diagText, all.substring(from).c_str());
}

lv_obj_t *buildDiag() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Diagnostics");
  lv_obj_t *body = addBody(scr);

  lv_obj_t *btns = lv_obj_create(body);
  lv_obj_remove_style_all(btns);
  lv_obj_set_size(btns, LV_PCT(100), 32);
  lv_obj_set_flex_flow(btns, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(btns, 8, 0);
  const char *labels[] = {LV_SYMBOL_UPLOAD "  Send log to daemon",
                          LV_SYMBOL_REFRESH "  Refresh"};
  lv_event_cb_t cbs[] = {diagUpload, diagRefresh};
  for (int i = 0; i < 2; i++) {
    lv_obj_t *btn = lv_button_create(btns);
    lv_obj_set_height(btn, 28);
    lv_obj_add_event_cb(btn, cbs[i], LV_EVENT_CLICKED, NULL);
    lv_obj_t *lb = lv_label_create(btn);
    lv_label_set_text(lb, labels[i]);
    lv_obj_set_style_text_font(lb, &lv_font_montserrat_12, 0);
    lv_obj_center(lb);
  }

  diagNote = lv_label_create(body);
  lv_label_set_text(diagNote, "");
  lv_obj_set_style_text_font(diagNote, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(diagNote, lv_palette_main(LV_PALETTE_AMBER), 0);
  lv_obj_align(diagNote, LV_ALIGN_TOP_LEFT, 0, 36);

  lv_obj_t *box = lv_obj_create(body);
  diagBox = box;
  lv_obj_set_size(box, LV_PCT(100), 130);
  lv_obj_align(box, LV_ALIGN_BOTTOM_MID, 0, 0);
  lv_obj_set_style_bg_color(box, lv_color_hex(0x16161c), 0);
  diagText = lv_label_create(box);
  lv_obj_set_width(diagText, LV_SIZE_CONTENT);  // no wrap — Sym+knob pans
  lv_obj_set_style_text_font(diagText, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(diagText, lv_palette_main(LV_PALETTE_GREY), 0);
  diagRefresh(nullptr);
  return scr;
}

// ---- wifi setup -------------------------------------------------------------

lv_obj_t *wifiList = nullptr;
lv_obj_t *wifiSpinner = nullptr;

void wifiPicked(lv_event_t *e) {
  int idx = (int)(intptr_t)lv_event_get_user_data(e);
  int count = wifi_mgr::scanCount();
  if (idx < count) {
    pendingSsid = wifi_mgr::scanItem(idx).ssid;
    if (!wifi_mgr::scanItem(idx).secure) {
      cfg->addWifi(pendingSsid, "");
      persist();
      syncWifi();
      wifi_mgr::connect(pendingSsid, "");
      show(cfg->daemonCount > 0 ? Screen::Beeper : Screen::DaemonEdit);
    } else {
      show(Screen::WifiPass);
    }
  } else if (idx == count) {
    // Rescan in place — reloading the screen slides the whole UI each time.
    wifi_mgr::startScan();
    if (wifiList) lv_obj_clean(wifiList);
    if (wifiSpinner) lv_obj_remove_flag(wifiSpinner, LV_OBJ_FLAG_HIDDEN);
  } else if (idx == count + 1) {
    show(Screen::WifiManual);
  } else {
    show(Screen::WifiSaved);
  }
}

void wifiForgetClicked(lv_event_t *e) {
  int idx = (int)(intptr_t)lv_event_get_user_data(e);
  cfg->removeWifi(idx);
  persist();
  syncWifi();
  show(Screen::WifiSaved);  // rebuild the list
}

lv_obj_t *buildWifiSaved() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Saved networks");
  lv_obj_t *body = addBody(scr);
  lv_obj_t *list = lv_list_create(body);
  lv_obj_set_style_bg_opa(list, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(list, 0, 0);
  lv_obj_set_style_pad_all(list, 0, 0);
  lv_obj_set_size(list, LV_PCT(100), LV_PCT(100));
  if (cfg->wifiCount == 0) lv_list_add_text(list, "None saved");
  for (int i = 0; i < cfg->wifiCount; i++) {
    String row = cfg->wifis[i].ssid + "   (click to forget)";
    lv_obj_t *btn = lv_list_add_button(list, LV_SYMBOL_TRASH, row.c_str());
    lv_obj_add_event_cb(btn, wifiForgetClicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);
  }
  return scr;
}

void fillWifiList() {
  if (!wifiList) return;
  lv_obj_clean(wifiList);
  int count = wifi_mgr::scanCount();
  if (count == 0) lv_list_add_text(wifiList, "No networks found");
  for (int i = 0; i < count; i++) {
    const auto &it = wifi_mgr::scanItem(i);
    bool known = false;
    for (int k = 0; k < cfg->wifiCount; k++)
      if (cfg->wifis[k].ssid == it.ssid) known = true;
    String row = String(known ? LV_SYMBOL_SAVE " " : "") + it.ssid + "   " +
                 String(it.rssi) + " dBm";
    lv_obj_t *btn = lv_list_add_button(
        wifiList, it.secure ? LV_SYMBOL_EYE_CLOSE : NULL, row.c_str());
    lv_obj_add_event_cb(btn, wifiPicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);
  }
  lv_obj_t *rescan =
      lv_list_add_button(wifiList, LV_SYMBOL_REFRESH, "Rescan");
  lv_obj_add_event_cb(rescan, wifiPicked, LV_EVENT_CLICKED,
                      (void *)(intptr_t)count);
  lv_obj_t *manual =
      lv_list_add_button(wifiList, LV_SYMBOL_KEYBOARD, "Type SSID manually");
  lv_obj_add_event_cb(manual, wifiPicked, LV_EVENT_CLICKED,
                      (void *)(intptr_t)(count + 1));
  if (cfg->wifiCount > 0) {
    lv_obj_t *saved = lv_list_add_button(wifiList, LV_SYMBOL_SAVE,
                                         "Saved networks...");
    lv_obj_add_event_cb(saved, wifiPicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)(count + 2));
  }
}

lv_obj_t *buildWifiScan() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Wi-Fi networks");
  lv_obj_t *body = addBody(scr);

  wifiList = lv_list_create(body);
  lv_obj_set_style_bg_opa(wifiList, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(wifiList, 0, 0);
  lv_obj_set_style_pad_all(wifiList, 0, 0);
  lv_obj_set_size(wifiList, LV_PCT(100), LV_PCT(100));

  wifiSpinner = lv_spinner_create(body);
  lv_obj_set_size(wifiSpinner, 40, 40);
  lv_obj_center(wifiSpinner);
  lv_group_remove_obj(wifiSpinner);  // must never take focus from list rows

  if (wifi_mgr::scanReady() && wifi_mgr::scanCount() > 0) {
    lv_obj_add_flag(wifiSpinner, LV_OBJ_FLAG_HIDDEN);
    fillWifiList();
  } else if (!wifi_mgr::scanning()) {
    wifi_mgr::startScan();
  }
  return scr;
}

void wifiPassReady(lv_event_t *e) {
  lv_obj_t *ta = (lv_obj_t *)lv_event_get_user_data(e);
  String pass = lv_textarea_get_text(ta);
  cfg->addWifi(pendingSsid, pass);
  persist();
  syncWifi();
  wifi_mgr::connect(pendingSsid, pass);
  show(cfg->daemonCount > 0 ? Screen::Beeper : Screen::DaemonEdit);
}

lv_obj_t *buildWifiPass() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Wi-Fi password");
  lv_obj_t *body = addBody(scr);

  lv_obj_t *hint = lv_label_create(body);
  lv_label_set_text(hint, ("Network: " + pendingSsid).c_str());
  lv_obj_set_style_text_font(hint, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(hint, lv_palette_main(LV_PALETTE_GREY), 0);

  lv_obj_t *ta = lv_textarea_create(body);
  lv_textarea_set_one_line(ta, true);
  lv_textarea_set_password_mode(ta, true);  // last char shows 1 s (lv_conf)
  lv_textarea_set_placeholder_text(ta, "Password  (Sym = digits)");
  lv_obj_set_width(ta, LV_PCT(100));
  lv_obj_align(ta, LV_ALIGN_TOP_MID, 0, 22);
  lv_obj_add_event_cb(ta, wifiPassReady, LV_EVENT_READY, ta);
  lv_group_focus_obj(ta);
  return scr;
}

void wifiManualReady(lv_event_t *e) {
  lv_obj_t *ta = (lv_obj_t *)lv_event_get_user_data(e);
  String ssid = lv_textarea_get_text(ta);
  if (ssid.length()) {
    pendingSsid = ssid;
    show(Screen::WifiPass);
  }
}

lv_obj_t *buildWifiManual() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Wi-Fi SSID");
  lv_obj_t *body = addBody(scr);
  lv_obj_t *ta = lv_textarea_create(body);
  lv_textarea_set_one_line(ta, true);
  lv_textarea_set_placeholder_text(ta, "Network name");
  lv_obj_set_width(ta, LV_PCT(100));
  lv_obj_add_event_cb(ta, wifiManualReady, LV_EVENT_READY, ta);
  lv_group_focus_obj(ta);
  return scr;
}

// ---- daemon list + edit -----------------------------------------------------

lv_obj_t *daemonUrlTa = nullptr;
lv_obj_t *daemonTokenTa = nullptr;
lv_obj_t *daemonNote = nullptr;

const char *daemonStateName(int i) {
  if (!cfg->daemons[i].enabled) return "disabled";
  switch (statusfeed::state(i)) {
    case statusfeed::State::Live: return "live";
    case statusfeed::State::Connecting: return "connecting";
    case statusfeed::State::Failed: return "unreachable";
    default: return "off";
  }
}

void daemonRowClicked(lv_event_t *e) {
  editDaemon = (int)(intptr_t)lv_event_get_user_data(e);
  show(Screen::DaemonEdit);
}

lv_obj_t *buildDaemon() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Daemons");
  lv_obj_t *body = addBody(scr);

  lv_obj_t *list = lv_list_create(body);
  lv_obj_set_style_bg_opa(list, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(list, 0, 0);
  lv_obj_set_style_pad_all(list, 0, 0);
  lv_obj_set_size(list, LV_PCT(100), LV_PCT(100));

  for (int i = 0; i < cfg->daemonCount; i++) {
    String row = cfg->apiBase(i) + "   (" + daemonStateName(i) + ")";
    lv_obj_t *btn = lv_list_add_button(list, LV_SYMBOL_SETTINGS, row.c_str());
    lv_obj_add_event_cb(btn, daemonRowClicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);
  }
  if (cfg->daemonCount < AppConfig::kMaxDaemons) {
    lv_obj_t *add = lv_list_add_button(list, LV_SYMBOL_PLUS, "Add daemon");
    lv_obj_add_event_cb(add, daemonRowClicked, LV_EVENT_CLICKED,
                        (void *)(intptr_t)(int)cfg->daemonCount);
  }
  return scr;
}

void daemonSave(lv_event_t *) {
  String url = lv_textarea_get_text(daemonUrlTa);
  String tok = lv_textarea_get_text(daemonTokenTa);
  if (!url.length()) {
    lv_label_set_text(daemonNote, "URL required");
    return;
  }
  bool isNew = editDaemon >= cfg->daemonCount;
  if (isNew) {
    if (cfg->daemonCount >= AppConfig::kMaxDaemons) return;
    editDaemon = cfg->daemonCount;
    cfg->daemonCount++;
  }
  cfg->daemons[editDaemon].url = url;
  if (tok.length()) cfg->daemons[editDaemon].token = tok;
  persist();
  applyDaemons();
  feedSeeded = false;
  lv_label_set_text(daemonNote, "Checking daemon...");
  lv_refr_now(NULL);
  String ver, err;
  if (agentapi::ping(editDaemon, &ver, &err)) {
    chime::play(chime::Cue::Status, cfg->soundCues, cfg->hapticCues);
    ticker = "daemon OK (v" + ver + ")";
    show(Screen::Beeper);
  } else {
    chime::play(chime::Cue::Error, cfg->soundCues, cfg->hapticCues);
    lv_label_set_text(daemonNote, ("Unreachable: " + err).c_str());
  }
}

void daemonRemove(lv_event_t *) {
  if (editDaemon >= cfg->daemonCount) {
    show(Screen::Daemon, true);
    return;
  }
  for (int i = editDaemon; i + 1 < cfg->daemonCount; i++)
    cfg->daemons[i] = cfg->daemons[i + 1];
  cfg->daemonCount--;
  persist();
  applyDaemons();
  show(Screen::Daemon, true);
}

lv_obj_t *buildDaemonEdit() {
  if (editDaemon > cfg->daemonCount) editDaemon = cfg->daemonCount;
  bool isNew = editDaemon >= cfg->daemonCount;

  lv_obj_t *scr = makeScreen();
  addHeader(scr, isNew ? "Add daemon" : "Edit daemon");
  lv_obj_t *body = addBody(scr);
  lv_obj_set_flex_flow(body, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_style_pad_row(body, 4, 0);

  daemonUrlTa = lv_textarea_create(body);
  lv_textarea_set_one_line(daemonUrlTa, true);
  lv_textarea_set_placeholder_text(daemonUrlTa, "http://192.168.x.x:8473");
  if (!isNew) lv_textarea_set_text(daemonUrlTa, cfg->apiBase(editDaemon).c_str());
  lv_obj_set_width(daemonUrlTa, LV_PCT(100));

  daemonTokenTa = lv_textarea_create(body);
  lv_textarea_set_one_line(daemonTokenTa, true);
  lv_textarea_set_placeholder_text(
      daemonTokenTa,
      (!isNew && cfg->daemons[editDaemon].token.length())
          ? "Token (saved - keep)"
          : "Token (~/.agentremoted/token)");
  lv_obj_set_width(daemonTokenTa, LV_PCT(100));

  if (!isNew) {
    lv_obj_t *en = lv_obj_create(body);
    lv_obj_remove_style_all(en);
    lv_obj_set_size(en, LV_PCT(100), 28);
    lv_obj_t *el = lv_label_create(en);
    lv_label_set_text(el, "Enabled");
    lv_obj_align(el, LV_ALIGN_LEFT_MID, 4, 0);
    lv_obj_t *sw = lv_switch_create(en);
    if (cfg->daemons[editDaemon].enabled) lv_obj_add_state(sw, LV_STATE_CHECKED);
    lv_obj_add_event_cb(
        sw,
        [](lv_event_t *e) {
          lv_obj_t *s2 = (lv_obj_t *)lv_event_get_target(e);
          cfg->daemons[editDaemon].enabled =
              lv_obj_has_state(s2, LV_STATE_CHECKED);
          persist();
          applyDaemons();
          feedSeeded = false;
        },
        LV_EVENT_VALUE_CHANGED, NULL);
    lv_obj_align(sw, LV_ALIGN_LEFT_MID, 90, 0);
  }

  lv_obj_t *row = lv_obj_create(body);
  lv_obj_remove_style_all(row);
  lv_obj_set_size(row, LV_PCT(100), 36);
  lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(row, 8, 0);

  lv_obj_t *save = lv_button_create(row);
  lv_obj_t *lb = lv_label_create(save);
  lv_label_set_text(lb, LV_SYMBOL_OK "  Save + test");
  lv_obj_add_event_cb(save, daemonSave, LV_EVENT_CLICKED, NULL);

  if (!isNew) {
    lv_obj_t *rm = lv_button_create(row);
    lv_obj_set_style_bg_color(rm, lv_palette_main(LV_PALETTE_RED), 0);
    lv_obj_t *rl = lv_label_create(rm);
    lv_label_set_text(rl, LV_SYMBOL_TRASH "  Remove");
    lv_obj_add_event_cb(rm, daemonRemove, LV_EVENT_CLICKED, NULL);
  }

  daemonNote = lv_label_create(body);
  lv_label_set_text(daemonNote, "");
  lv_obj_set_style_text_font(daemonNote, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(daemonNote, lv_palette_main(LV_PALETTE_RED), 0);

  lv_group_focus_obj(daemonUrlTa);
  return scr;
}

// ---- power ---------------------------------------------------------------

void powerAction(lv_event_t *e) {
  int what = (int)(intptr_t)lv_event_get_user_data(e);
  if (what == 0) power::powerOff();
  else if (what == 1) power::restart();
  else show(Screen::Menu, true);
}

lv_obj_t *buildPower() {
  lv_obj_t *scr = makeScreen();
  addHeader(scr, "Power");
  lv_obj_t *body = addBody(scr);

  lv_obj_t *icon = lv_label_create(body);
  lv_label_set_text(icon, LV_SYMBOL_POWER);
  lv_obj_set_style_text_font(icon, &lv_font_montserrat_24, 0);
  lv_obj_set_style_text_color(icon, lv_palette_main(LV_PALETTE_RED), 0);
  lv_obj_align(icon, LV_ALIGN_LEFT_MID, 24, -20);

  lv_obj_t *note = lv_label_create(body);
  lv_label_set_text(note, "Off = deep sleep.\nWake: knob or side button.");
  lv_obj_set_style_text_font(note, &lv_font_montserrat_12, 0);
  lv_obj_set_style_text_color(note, lv_palette_main(LV_PALETTE_GREY), 0);
  lv_obj_align(note, LV_ALIGN_LEFT_MID, 0, 40);

  const char *labels[] = {LV_SYMBOL_POWER "  Power off",
                          LV_SYMBOL_REFRESH "  Restart", "Cancel"};
  for (int i = 0; i < 3; i++) {
    lv_obj_t *btn = lv_button_create(body);
    lv_obj_set_size(btn, 200, 36);
    lv_obj_align(btn, LV_ALIGN_TOP_RIGHT, -8, 4 + i * 44);
    lv_obj_add_event_cb(btn, powerAction, LV_EVENT_CLICKED,
                        (void *)(intptr_t)i);
    lv_obj_t *lb = lv_label_create(btn);
    lv_label_set_text(lb, labels[i]);
    lv_obj_center(lb);
  }
  return scr;
}

// ---- boot (first run) -----------------------------------------------------

void bootClicked(lv_event_t *) {
  wifi_mgr::startScan();
  show(Screen::WifiScan);
}

lv_obj_t *buildBoot() {
  lv_obj_t *scr = makeScreen();
  // Centered lockup: logo + wordmark as one row, subtitle and button below.
  lv_obj_t *row = lv_obj_create(scr);
  lv_obj_remove_style_all(row);
  lv_obj_set_size(row, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_t *lg = makeLogo(row, 56);
  (void)lg;
  lv_obj_t *t = lv_label_create(row);
  lv_label_set_text(t, "Agent Remote");
  lv_obj_set_style_text_font(t, &lv_font_montserrat_24, 0);
  lv_obj_align(row, LV_ALIGN_CENTER, 0, -42);

  lv_obj_t *s = lv_label_create(scr);
  lv_label_set_text(s, "Beeper for your coding agents");
  lv_obj_set_style_text_color(s, lv_palette_main(LV_PALETTE_GREY), 0);
  lv_obj_align(s, LV_ALIGN_CENTER, 0, 2);

  lv_obj_t *btn = lv_button_create(scr);
  lv_obj_align(btn, LV_ALIGN_CENTER, 0, 44);
  lv_obj_add_event_cb(btn, bootClicked, LV_EVENT_CLICKED, NULL);
  lv_obj_t *lb = lv_label_create(btn);
  lv_label_set_text(lb, "Set up  " LV_SYMBOL_RIGHT);
  lv_obj_center(lb);
  return scr;
}

// ---- navigation -----------------------------------------------------------

void goBack() {
  switch (cur) {
    case Screen::Beeper: show(Screen::Menu); break;  // side button = menu
    case Screen::Menu: show(Screen::Beeper, true); break;
    case Screen::WifiPass:
    case Screen::WifiManual:
    case Screen::WifiSaved: show(Screen::WifiScan, true); break;
    case Screen::WifiScan:
      // Scanning dropped the connection (associated scans fail) — rejoin.
      if (cfg && cfg->wifiCount > 0 &&
          wifi_mgr::state() != wifi_mgr::State::Connected) {
        wifi_mgr::autoConnect();
      }
      show(Screen::Menu, true);
      break;
    case Screen::Compose: show(Screen::LiveTui, true); break;
    case Screen::WebUi:
      webui::stop();
      // Wake the feeds back up (they were paused for the server).
      for (int i = 0; i < cfg->daemonCount; i++) statusfeed::pause(i, 1);
      show(Screen::Menu, true);
      break;
    case Screen::LiveTui: show(tuiFrom, true); break;
    case Screen::DaemonEdit: show(Screen::Daemon, true); break;
    case Screen::Boot: break;
    default: show(Screen::Menu, true); break;
  }
}

void show(Screen s, bool backward) {
  cardsBox = nullptr;
  idleBox = nullptr;
  beeperWrap = nullptr;
  hdrTime = hdrMod = hdrBell = hdrWifi = hdrBatt = nullptr;
  wifiList = nullptr;
  wifiSpinner = nullptr;
  replyNote = nullptr;
  sessList = nullptr;
  sessSpinner = nullptr;
  tuiLabel = nullptr;
  tuiPane = nullptr;
  bleState = nullptr;
  usageBox = nullptr;
  usageSpinner = nullptr;
  diagNote = nullptr;
  diagText = nullptr;
  diagBox = nullptr;

  if (grp) lv_group_delete(grp);
  grp = lv_group_create();
  lv_group_set_default(grp);
  lvgl_glue::setGroup(grp);

  lv_obj_t *scr = nullptr;
  switch (s) {
    case Screen::Boot: scr = buildBoot(); break;
    case Screen::Beeper: scr = buildBeeper(); break;
    case Screen::Menu: scr = buildMenu(); break;
    case Screen::Sessions: scr = buildSessions(); break;
    case Screen::Compose: scr = buildCompose(); break;
    case Screen::LiveTui: scr = buildLiveTui(); break;
    case Screen::Chimes: scr = buildChimes(); break;
    case Screen::WifiScan: scr = buildWifiScan(); break;
    case Screen::WifiPass: scr = buildWifiPass(); break;
    case Screen::WifiManual: scr = buildWifiManual(); break;
    case Screen::WifiSaved: scr = buildWifiSaved(); break;
    case Screen::Daemon: scr = buildDaemon(); break;
    case Screen::DaemonEdit: scr = buildDaemonEdit(); break;
    case Screen::Bluetooth: scr = buildBluetooth(); break;
    case Screen::WebUi: scr = buildWebUi(); break;
    case Screen::Usage: scr = buildUsage(); break;
    case Screen::Diag: scr = buildDiag(); break;
    case Screen::Power: scr = buildPower(); break;
  }
  cur = s;
  lvgl_glue::setRotaryHandler(s == Screen::LiveTui ? tuiRotary
                              : s == Screen::Diag  ? diagRotary
                              : s == Screen::Usage ? usageRotary
                                                   : nullptr);
  lv_screen_load_anim(scr,
                      backward ? LV_SCR_LOAD_ANIM_MOVE_RIGHT
                               : LV_SCR_LOAD_ANIM_MOVE_LEFT,
                      200, 0, true);
}

// ---- feed diffing (beeper controller) --------------------------------------

// Fallback status poll (feeds down): blocking HTTP belongs on the worker —
// at boot this ran on the UI thread in the gap between Wi-Fi connecting and
// the feeds going live, freezing the device for seconds.
volatile int pollJobState = 0;  // 0 idle, 1 running, 2 done
StatusSnap pollSnap;

void runStatusPoll() {
  StatusSnap s{};
  bool anyOk = false;
  for (int d = 0; d < agentapi::daemonCount(); d++) {
    if (!cfg->daemons[d].enabled) continue;
    StatusSnap one = agentapi::pollStatus(d);
    if (!one.ok) continue;
    anyOk = true;
    s.working += one.working;
    s.needsYou = s.needsYou || one.needsYou;
    if (s.phase.isEmpty()) s.phase = one.phase;
    if (s.tool.isEmpty()) s.tool = one.tool;
  }
  s.ok = anyOk;
  pollSnap = s;
  pollJobState = 2;
}

String toolSigOf(const statusfeed::JobStat &j) {
  return j.phase + "|" + j.tool + "|" + j.toolDetail;
}

const PrevJob *findPrev(const String &id) {
  for (auto &p : prevJobs)
    if (p.id == id) return &p;
  return nullptr;
}

bool refreshSessionsRateLimited() {
  if (millis() - lastTitleFetch < 20000) return false;
  if (wifi_mgr::state() != wifi_mgr::State::Connected) return false;
  if (!cfg || !cfg->configured()) return false;
  lastTitleFetch = millis();
  startSessionsFetch();  // async; tick() applies the merged result
  return true;
}

void diffFeed() {
  const auto &jobs = statusfeed::jobs();

  bool sawStart = false, sawDone = false, sawAttention = false, sawTick = false;
  String attnTitle;

  for (auto &p : prevJobs) {
    bool still = false;
    for (const auto &j : jobs)
      if (j.jobId == p.id) still = true;
    if (!still) sawDone = true;
  }

  bool pendingNow = false;
  for (const auto &j : jobs) {
    bool needs = j.pendingPermission || j.pendingQuestion;
    if (needs) {
      pendingNow = true;
      attnTitle = titleFor(j);
    }
    const PrevJob *p = findPrev(j.jobId);
    if (!p) {
      sawStart = true;
    } else {
      if (needs && !p->pending) sawAttention = true;
      if (!needs && p->toolSig != toolSigOf(j)) sawTick = true;
    }
  }

  if (feedSeeded) {
    if (sawAttention) {
      cue(chime::Cue::Attention);
      ticker = "needs you: " + attnTitle;
      remindNextAt = millis() + kRemindMs;
    } else if (sawDone) {
      cue(chime::Cue::Done);
      ticker = "turn ended";
    } else if (sawStart) {
      cue(chime::Cue::Status);
      ticker = "turn started";
    } else if (sawTick) {
      // Tool → tool transitions beep like the web client (Status cue is
      // defined as "phase/tool change"), floored so bursts don't rattle.
      if (millis() - lastToolBeep > 2000) {
        lastToolBeep = millis();
        cue(chime::Cue::Status);
      } else {
        chime::play(chime::Cue::Tick, false, cfg->hapticCues);
      }
    }
  } else {
    feedSeeded = true;
    if (pendingNow) remindNextAt = millis() + kRemindMs;
  }

  anyPending = pendingNow;
  prevJobs.clear();
  for (const auto &j : jobs)
    prevJobs.push_back({j.jobId, toolSigOf(j),
                        j.pendingPermission || j.pendingQuestion});

  if (!jobs.empty()) refreshSessionsRateLimited();
}

}  // namespace

void begin(AppConfig *c) {
  cfg = c;
  workerQ = xQueueCreate(8, 1);
  usageMx = xSemaphoreCreateMutex();
  if (xTaskCreate(workerTask, "worker", 24576, NULL, 1, NULL) != pdPASS)
    dlog::logf("[worker] task create FAILED");
  applyDaemons();
  syncWifi();
  lvgl_glue::onBack(goBack);
  show(cfg && cfg->configured() ? Screen::Beeper : Screen::Boot);
}

uint32_t lastActivityMs() { return millis() - lvgl_glue::inactiveMs(); }

void tick() {
  static uint32_t lastFeedGenSeen = 0;
  static uint32_t lastHeaderRefresh = 0;
  static uint32_t lastElapsedRefresh = 0;
  static uint32_t pollDelay = STATUS_POLL_MS;

  // Wi-Fi scan completing while the picker is open.
  if (cur == Screen::WifiScan && wifi_mgr::scanning() && wifi_mgr::scanReady()) {
    if (wifiSpinner) lv_obj_add_flag(wifiSpinner, LV_OBJ_FLAG_HIDDEN);
    fillWifiList();
  }

  // Live TUI: poll ~1.5 s while the screen is open, tail on new text.
  if (cur == Screen::LiveTui) {
    if (tuiFetchState == 2) {
      tuiFetchState = 0;
      if (tuiLabel && tuiFetchText != tuiShown) {
        // Tail only while the user is at the bottom — scrolling up to read
        // history must not get yanked back down by the next poll.
        bool tailing =
            !tuiPane || lv_obj_get_scroll_bottom(tuiPane) < 30;
        tuiShown = tuiFetchText;
        String view = tuiShown;
        if (view.length() > 4000) view = view.substring(view.length() - 4000);
        lv_label_set_text(tuiLabel, view.c_str());
        if (tuiPane && tailing)
          lv_obj_scroll_to_y(tuiPane, LV_COORD_MAX, LV_ANIM_OFF);
      }
    }
    if (tuiFetchState == 0 && millis() - tuiLastPoll >= 1500) {
      tuiLastPoll = millis();
      startTuiFetch();
    }
  } else if (tuiFetchState == 2) {
    tuiFetchState = 0;  // drop a result that landed after leaving
  }

  // Reply / diag-upload results land back on the UI thread here.
  if (replyState >= 2) {
    bool ok = replyState == 2;
    replyState = 0;
    if (ok) {
      cue(chime::Cue::Status);
      ticker = "sent: " + replyText;
      if (cur == Screen::Compose) show(Screen::Beeper);
    } else {
      cue(chime::Cue::Error);
      if (replyNote) lv_label_set_text(replyNote, replyErr.c_str());
    }
  }
  if (diagUpState == 2) {
    diagUpState = 0;
    if (diagNote) lv_label_set_text(diagNote, diagUpNote.c_str());
  }
  // Progressive usage rendering: repaint as each daemon's buckets arrive.
  static uint32_t lastUsageGen = 0;
  if (usageGen != lastUsageGen) {
    lastUsageGen = usageGen;
    if (cur == Screen::Usage) {
      if (usageSpinner) lv_obj_add_flag(usageSpinner, LV_OBJ_FLAG_HIDDEN);
      fillUsage();
    }
  }
  if (usageFetchState >= 2) {
    usageFetchState = 0;
    if (cur == Screen::Usage) {
      if (usageSpinner) lv_obj_add_flag(usageSpinner, LV_OBJ_FLAG_HIDDEN);
      fillUsage();
    }
  }

  // Fetch watchdog: a wedged task/connection must not spin forever.
  if (sessFetchState == 1 && millis() - sessFetchStartedAt > 30000) {
    sessFetchState = 0;
    sessFetchErr = "fetch timed out";
    if (cur == Screen::Sessions) {
      if (sessSpinner) lv_obj_add_flag(sessSpinner, LV_OBJ_FLAG_HIDDEN);
      fillSessionsList();
    }
  }

  // Session fetch landing while the list is open.
  if (sessFetchState >= 2) {
    if (sessFetchState == 2) sessions.swap(sessFetchResult);
    sessFetchState = 0;
    if (cur == Screen::Sessions) {
      if (sessSpinner) lv_obj_add_flag(sessSpinner, LV_OBJ_FLAG_HIDDEN);
      fillSessionsList();
    }
  }

  // New feed snapshot → chimes; rebuild cards only when their structure
  // changed (elapsed_s bumps the generation every second, and rebuilding
  // ~20 widgets per second made the whole UI feel laggy — the elapsed
  // labels already update in place below).
  static String lastCardsSig;
  if (statusfeed::generation() != lastFeedGenSeen) {
    lastFeedGenSeen = statusfeed::generation();
    if (statusfeed::aggregate() == statusfeed::State::Live) {
      diffFeed();
    } else {
      feedSeeded = false;
    }
    String sig = String((int)statusfeed::aggregate());
    for (const auto &j : statusfeed::jobs()) {
      sig += j.jobId;
      sig += (j.pendingPermission || j.pendingQuestion) ? '!' : '.';
      sig += j.phase + j.tool + j.toolDetail + "|";
    }
    if (sig != lastCardsSig) {
      lastCardsSig = sig;
      if (cur == Screen::Beeper && cardsBox) rebuildCards();
    }
  }

  // BLE buddy snapshot changes: same chime rules, desktop-side counters.
  static uint32_t lastBleGen = 0;
  static int blePrevRunning = 0;
  static bool blePrevWaiting = false, blePending = false, bleSeeded = false;
  if (blebuddy::generation() != lastBleGen) {
    lastBleGen = blebuddy::generation();
    const auto &b = blebuddy::snap();
    bool waitingNow = b.waiting > 0 || b.hasPrompt;
    if (bleSeeded && blebuddy::connected()) {
      if (waitingNow && !blePrevWaiting) {
        cue(chime::Cue::Attention);
        remindNextAt = millis() + kRemindMs;
      } else if (b.running == 0 && blePrevRunning > 0) {
        cue(chime::Cue::Done);
      } else if (b.running > 0 && blePrevRunning == 0) {
        cue(chime::Cue::Status);
      }
    } else if (blebuddy::connected()) {
      bleSeeded = true;
      if (waitingNow) remindNextAt = millis() + kRemindMs;
    }
    if (!blebuddy::connected()) bleSeeded = false;
    blePrevRunning = b.running;
    blePrevWaiting = waitingNow;
    blePending = waitingNow && blebuddy::connected();
    if (cur == Screen::Beeper && cardsBox) rebuildCards();
    if (cur == Screen::Bluetooth && bleState)
      lv_label_set_text(bleState, bleStateText().c_str());
  }

  // Standing reminder while a question/permission waits (daemon or BLE).
  if ((anyPending || blePending) && millis() >= remindNextAt) {
    remindNextAt = millis() + kRemindMs;
    cue(chime::Cue::Attention);
  }

  // Header (modifiers, feed bell, wifi, battery) once a second.
  if (millis() - lastHeaderRefresh >= 1000) {
    lastHeaderRefresh = millis();
    updateHeader();
  }

  // Elapsed counters on beeper cards.
  if (cur == Screen::Beeper && millis() - lastElapsedRefresh >= 1000) {
    lastElapsedRefresh = millis();
    const auto &jobs = statusfeed::jobs();
    for (auto &ref : cardRefs) {
      for (const auto &j : jobs) {
        if (j.jobId == ref.jobId && ref.elapsed) {
          char e[24];
          if (j.queued > 0)
            snprintf(e, sizeof(e), "%d:%02d  +%d", j.elapsedS / 60,
                     j.elapsedS % 60, j.queued);
          else
            snprintf(e, sizeof(e), "%d:%02d", j.elapsedS / 60, j.elapsedS % 60);
          lv_label_set_text(ref.elapsed, e);
        }
      }
    }
  }

  // Fallback poll when SSE is not live, with backoff while unreachable.
  if (statusfeed::aggregate() == statusfeed::State::Live) return;
  if (millis() - lastPoll < pollDelay) return;
  lastPoll = millis();
  if (wifi_mgr::state() != wifi_mgr::State::Connected) return;
  if (!cfg || !cfg->configured()) return;

  StatusSnap s{};
  bool anyOk = false;
  for (int d = 0; d < agentapi::daemonCount(); d++) {
    if (!cfg->daemons[d].enabled) continue;
    StatusSnap one = agentapi::pollStatus(d);
    if (!one.ok) continue;
    anyOk = true;
    s.working += one.working;
    s.needsYou = s.needsYou || one.needsYou;
    if (s.phase.isEmpty()) s.phase = one.phase;
    if (s.tool.isEmpty()) s.tool = one.tool;
  }
  s.ok = anyOk;
  if (!s.ok) {
    pollDelay = pollDelay >= 30000 ? 60000 : pollDelay * 2;
    return;
  }
  pollDelay = STATUS_POLL_MS;
  String sig = agentapi::statusSignature(s);
  if (sig != lastSig) {
    if (s.needsYou) {
      cue(chime::Cue::Attention);
    } else if (s.working > 0 && lastSig.indexOf("|0|") >= 0) {
      cue(chime::Cue::Status);
    } else if (s.working == 0 && lastSig.length() && lastSig[0] != '0') {
      cue(chime::Cue::Done);
    }
    lastSig = sig;
  }
}

}  // namespace ui
