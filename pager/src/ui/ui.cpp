#include "ui/ui.h"
#include "board_pins.h"
#include "hw/display.h"
#include "hw/chime.h"
#include "hw/keyboard.h"
#include "hw/power.h"
#include "hw/wifi_mgr.h"
#include "net/statusfeed.h"

namespace ui {
namespace {

AppConfig *cfg = nullptr;
Screen scr = Screen::Boot;
String line;
String statusLine;
String ticker;  // last beeper event, e.g. "done: Fix login flow"
String lastSig;
uint32_t lastAct = 0;
uint32_t lastDraw = 0;
uint32_t lastPoll = 0;
std::vector<SessionRow> sessions;
uint32_t lastTitleFetch = 0;
int sel = 0;
int menuSel = 0;
int netSel = 0;
int powerSel = 0;
int setupField = 0;  // daemon: 0 url 1 token
String pendingSsid;
// Password entry: last typed char stays visible for 1 s, then masks.
uint32_t lastCharMs = 0;
bool maskArmed = false;
constexpr uint32_t kPassRevealMs = 1000;

// ---- beeper state -------------------------------------------------------
struct PrevJob {
  String id;
  String toolSig;
  bool pending;
};
std::vector<PrevJob> prevJobs;
bool feedSeeded = false;   // first snapshot after (re)connect never chimes
uint32_t lastFeedGen = 0;
bool anyPending = false;
uint32_t remindNextAt = 0;
constexpr uint32_t kRemindMs = 30000;

// ---- menu ---------------------------------------------------------------
struct MenuItem {
  const char *label;
  Screen target;
};
const MenuItem kMenu[] = {
    {"Beeper", Screen::Beeper},   {"Sessions", Screen::Sessions},
    {"Compose", Screen::Compose}, {"Chimes", Screen::Status},
    {"Wi-Fi", Screen::WifiScan},  {"Daemon", Screen::SetupDaemon},
    {"Power", Screen::Power},
};
constexpr int kMenuCount = sizeof(kMenu) / sizeof(kMenu[0]);

void touch() { lastAct = millis(); }

uint16_t providerColor(const String &p) {
  if (p.startsWith("cl")) return display::rgb(196, 122, 255);  // claude
  if (p.startsWith("co")) return display::rgb(90, 200, 250);   // codex
  if (p.startsWith("gr")) return display::rgb(120, 220, 120);  // grok
  return display::COL_DIM;
}

const char *providerTag(const String &p) {
  if (p.startsWith("cl")) return "CLD";
  if (p.startsWith("co")) return "CDX";
  if (p.startsWith("gr")) return "GRK";
  return p.length() ? p.c_str() : "AGT";
}

String titleFor(const statusfeed::JobStat &j) {
  for (auto &s : sessions) {
    if (s.id.length() && (s.id == j.sessionId)) return s.title;
  }
  if (j.prompt.length()) return j.prompt;
  return "session " + j.jobId;
}

void header(const char *title) {
  display::fillRect(0, 0, display::width(), 18, display::COL_BAR);
  display::drawText(4, 3, title, display::COL_FG, 1);
  auto bat = power::read();
  char right[40];
  const char *mod = keyboard::capsOn() ? "CAP" : (keyboard::symOn() ? "SYM" : "");
  if (bat.percent >= 0)
    snprintf(right, sizeof(right), "%s %s %d%%", mod, wifi_mgr::stateName(),
             bat.percent);
  else
    snprintf(right, sizeof(right), "%s %s", mod, wifi_mgr::stateName());
  display::drawText(display::width() - 120, 3, right, display::COL_DIM, 1);
  // Feed liveness dot at the far right.
  uint16_t dot = display::COL_DIM;
  switch (statusfeed::state()) {
    case statusfeed::State::Live: dot = display::COL_OK; break;
    case statusfeed::State::Connecting: dot = display::COL_WARN; break;
    case statusfeed::State::Failed: dot = display::COL_ERR; break;
    default: break;
  }
  display::fillCircle(display::width() - 8, 9, 3, dot);
  display::hLine(18, display::COL_DIM);
}

void footer(const char *hint) {
  int y = display::height() - 14;
  display::fillRect(0, y - 2, display::width(), 16, display::COL_BAR);
  display::drawText(4, y, hint, display::COL_DIM, 1);
}

// List window so the selected row stays visible.
int listFirst(int count, int selIdx, int maxRows) {
  if (count <= maxRows) return 0;
  int first = selIdx - maxRows / 2;
  if (first < 0) first = 0;
  if (first > count - maxRows) first = count - maxRows;
  return first;
}

void startWifiSetup() {
  netSel = 0;
  wifi_mgr::startScan();
  statusLine = "Scanning...";
  scr = Screen::WifiScan;
  line = "";
  touch();
  draw();
}

// ---- icons (primitives only, TrailMate-style tiles) ---------------------

void iconBell(int cx, int cy, uint16_t c) {
  display::fillArc(cx, cy + 2, 0, 11, 180, 360, c);
  display::fillRect(cx - 11, cy + 2, 22, 3, c);
  display::fillRect(cx - 1, cy - 12, 3, 4, c);
  display::fillCircle(cx, cy + 9, 3, c);
}

void iconList(int cx, int cy, uint16_t c) {
  display::drawRoundRect(cx - 13, cy - 12, 26, 24, 4, c);
  for (int i = 0; i < 3; i++)
    display::fillRect(cx - 8, cy - 6 + i * 6, 16, 2, c);
}

void iconPencil(int cx, int cy, uint16_t c) {
  for (int i = -1; i <= 1; i++)
    display::drawLine(cx - 9 + i, cy + 9, cx + 9 + i, cy - 9, c);
  display::fillTriangle(cx - 12, cy + 12, cx - 9, cy + 6, cx - 6, cy + 9, c);
}

void iconNote(int cx, int cy, uint16_t c) {
  display::fillCircle(cx - 4, cy + 8, 5, c);
  display::fillRect(cx, cy - 10, 3, 18, c);
  display::fillRect(cx, cy - 12, 12, 4, c);
}

void iconWifi(int cx, int cy, uint16_t c) {
  display::fillCircle(cx, cy + 8, 3, c);
  display::fillArc(cx, cy + 8, 8, 11, 225, 315, c);
  display::fillArc(cx, cy + 8, 15, 18, 225, 315, c);
}

void iconServer(int cx, int cy, uint16_t c) {
  display::drawRoundRect(cx - 13, cy - 12, 26, 11, 3, c);
  display::drawRoundRect(cx - 13, cy + 1, 26, 11, 3, c);
  display::fillCircle(cx - 8, cy - 7, 2, c);
  display::fillCircle(cx - 8, cy + 6, 2, c);
}

void iconPower(int cx, int cy, uint16_t c) {
  display::fillArc(cx, cy + 1, 9, 12, 300, 240, c);
  display::fillRect(cx - 1, cy - 13, 3, 12, c);
}

void drawMenuIcon(int idx, int cx, int cy, uint16_t c) {
  switch (idx) {
    case 0: iconBell(cx, cy, c); break;
    case 1: iconList(cx, cy, c); break;
    case 2: iconPencil(cx, cy, c); break;
    case 3: iconNote(cx, cy, c); break;
    case 4: iconWifi(cx, cy, c); break;
    case 5: iconServer(cx, cy, c); break;
    case 6: iconPower(cx, cy, c); break;
  }
}

// ---- screens ------------------------------------------------------------

void drawBoot() {
  display::clear(display::COL_BG);
  header("Agent Remote");
  display::drawText(8, 40, "T-LoRa Pager beeper", display::COL_FG, 2);
  display::drawText(8, 70, "Chimes + live status for your agents", display::COL_DIM, 1);
  display::drawText(8, 100, statusLine.c_str(), display::COL_ACCENT, 1);
  footer("Enter/Knob=set up");
}

void drawBeeper() {
  display::clear(display::COL_BG);
  header("Agent Remote");
  const auto &jobs = statusfeed::jobs();
  int w = display::width();

  if (jobs.empty()) {
    // Never show a calm bell when we cannot actually hear the daemon.
    auto fs = statusfeed::state();
    if (cfg && cfg->configured() && fs != statusfeed::State::Live) {
      iconBell(w / 2, 78, display::COL_ERR);
      const char *msg = fs == statusfeed::State::Connecting
                            ? "Connecting to daemon..."
                            : "Daemon unreachable";
      display::drawText(24, 104, msg, display::COL_ERR, 2);
      display::drawTextTrunc(24, 132, w - 48, cfg->apiBase().c_str(),
                             display::COL_DIM, 1);
      display::drawText(24, 150, "Check URL: Menu > Daemon (knob = menu)",
                        display::COL_DIM, 1);
    } else {
      iconBell(w / 2, 78, display::COL_DIM);
      display::drawText(w / 2 - 44, 104, "All quiet", display::COL_FG, 2);
      String sub = ticker.length() ? ("Last: " + ticker)
                                   : String("Waiting for agent activity");
      display::drawTextTrunc(12, 132, w - 24, sub.c_str(), display::COL_DIM, 1);
      if (statusLine.length())
        display::drawTextTrunc(12, 150, w - 24, statusLine.c_str(),
                               display::COL_ACCENT, 1);
    }
  } else {
    int y = 22;
    int cardH = 42;
    int shown = 0;
    for (const auto &j : jobs) {
      if (shown >= 4) break;
      bool needs = j.pendingPermission || j.pendingQuestion;
      bool blinkOn = (millis() / 500) % 2 == 0;
      uint16_t border = needs ? (blinkOn ? display::COL_WARN : display::COL_BAR)
                              : display::COL_BAR;
      display::fillRoundRect(4, y, w - 8, cardH, 6, display::COL_BAR);
      if (needs) display::drawRoundRect(4, y, w - 8, cardH, 6, border);

      uint16_t pc = providerColor(j.provider);
      display::fillRoundRect(10, y + 5, 34, 14, 4, pc);
      display::drawText(14, y + 8, providerTag(j.provider), display::COL_BG, 1);

      String title = titleFor(j);
      display::drawTextTrunc(52, y + 5, w - 52 - 96, title.c_str(),
                             display::COL_FG, 1);

      // Right column: elapsed + queue.
      char right[24];
      int m = j.elapsedS / 60, s = j.elapsedS % 60;
      if (j.queued > 0)
        snprintf(right, sizeof(right), "%d:%02d +%d", m, s, j.queued);
      else
        snprintf(right, sizeof(right), "%d:%02d", m, s);
      display::drawText(w - 60, y + 5, right, display::COL_DIM, 1);

      // Second line: NEEDS YOU beats tool text beats phase.
      String detail;
      uint16_t dcol = display::COL_DIM;
      if (needs) {
        detail = j.pendingQuestion ? "? QUESTION - answer needed"
                                   : "! PERMISSION - answer needed";
        dcol = display::COL_WARN;
      } else if (j.tool.length()) {
        detail = j.tool;
        if (j.toolDetail.length()) detail += ": " + j.toolDetail;
        dcol = display::COL_OK;
      } else {
        detail = j.phase.length() ? j.phase : "working";
        if (j.phaseDetail.length()) detail += " " + j.phaseDetail;
      }
      display::drawTextTrunc(52, y + 24, w - 60, detail.c_str(), dcol, 1);

      y += cardH + 4;
      shown++;
    }
    if ((int)jobs.size() > shown) {
      char more[24];
      snprintf(more, sizeof(more), "+%d more", (int)jobs.size() - shown);
      display::drawText(8, y + 2, more, display::COL_DIM, 1);
    }
  }
  footer("Knob=menu");
}

void drawMenu() {
  display::clear(display::COL_BG);
  header("Menu");
  int w = display::width();
  int cols = 4;
  int cellW = w / cols;
  int cellH = 88;
  int top = 22;
  for (int i = 0; i < kMenuCount; i++) {
    int r = i / cols, c = i % cols;
    int x = c * cellW, y = top + r * cellH;
    bool on = i == menuSel;
    int bx = x + (cellW - 56) / 2;
    uint16_t tileCol = on ? display::COL_ACCENT : display::COL_BAR;
    display::fillRoundRect(bx, y + 4, 56, 56, 12, tileCol);
    uint16_t iconCol = on ? display::COL_BG : display::COL_FG;
    if (i == kMenuCount - 1 && !on) iconCol = display::COL_ERR;  // power
    drawMenuIcon(i, bx + 28, y + 32, iconCol);
    int lw = (int)strlen(kMenu[i].label) * 6;
    display::drawText(x + (cellW - lw) / 2, y + 66,
                      kMenu[i].label, on ? display::COL_ACCENT : display::COL_DIM, 1);
  }
  footer("Knob=move  Enter=open  Bksp=home");
}

void drawPower() {
  display::clear(display::COL_BG);
  header("Power");
  iconPower(70, 100, powerSel == 0 ? display::COL_ERR : display::COL_DIM);
  const char *opts[] = {"Power off", "Restart", "Cancel"};
  for (int i = 0; i < 3; i++) {
    bool on = i == powerSel;
    int y = 52 + i * 34;
    if (on) display::fillRoundRect(140, y - 6, display::width() - 152, 28, 6, display::COL_BAR);
    display::drawText(152, y, opts[i], on ? display::COL_ACCENT : display::COL_FG, 2);
  }
  display::drawText(140, 160, "Off = deep sleep. Wake: press the knob",
                    display::COL_DIM, 1);
  display::drawText(140, 174, "or the side button.", display::COL_DIM, 1);
  footer("Knob=move  Enter=confirm  Bksp=back");
}

void drawWifiScan() {
  display::clear(display::COL_BG);
  header("Wi-Fi networks");
  int count = wifi_mgr::scanCount();
  int total = count + 2;
  const int maxRows = 12;
  int first = listFirst(total, netSel, maxRows);
  int y = 24;
  if (wifi_mgr::scanning()) {
    display::drawText(8, 40, "Scanning...", display::COL_ACCENT, 1);
  } else if (count == 0) {
    display::drawText(8, 40, "No networks found", display::COL_DIM, 1);
  }
  for (int i = first; i < total && i < first + maxRows; i++) {
    bool on = i == netSel;
    if (on) display::fillRect(0, y - 1, display::width(), 14, display::COL_BAR);
    char row[96];
    if (i < count) {
      const auto &it = wifi_mgr::scanItem(i);
      snprintf(row, sizeof(row), "%c %-32s %4d dBm", it.secure ? '*' : ' ',
               it.ssid.c_str(), it.rssi);
    } else if (i == count) {
      snprintf(row, sizeof(row), "  [ Rescan ]");
    } else {
      snprintf(row, sizeof(row), "  [ Type SSID manually ]");
    }
    display::drawTextTrunc(4, y, display::width() - 8, row,
                           on ? display::COL_ACCENT : display::COL_FG, 1);
    y += 14;
  }
  footer("Knob=move  Enter=pick  Bksp=back  (*=secured)");
}

void drawWifiManual() {
  display::clear(display::COL_BG);
  header("Wi-Fi SSID");
  display::drawText(8, 32, "Network name:", display::COL_FG, 1);
  display::drawText(8, 50, line.c_str(), display::COL_ACCENT, 2);
  footer("Enter=next  Bksp=del/back");
}

void drawWifiPass() {
  display::clear(display::COL_BG);
  header("Wi-Fi password");
  char nline[64];
  snprintf(nline, sizeof(nline), "Network: %s", pendingSsid.c_str());
  display::drawText(8, 28, nline, display::COL_DIM, 1);
  display::drawText(8, 46, "Password:", display::COL_FG, 1);
  // Mask all but the newest char; the newest stays readable for 1 s.
  String shown;
  bool revealLast = maskArmed && (millis() - lastCharMs < kPassRevealMs);
  for (size_t i = 0; i < line.length(); i++) {
    if (revealLast && i == line.length() - 1)
      shown += line[i];
    else
      shown += '*';
  }
  display::drawText(8, 64, shown.c_str(), display::COL_ACCENT, 2);
  display::drawText(8, 110, statusLine.c_str(), display::COL_FG, 1);
  footer("Enter=join  Bksp=del/back  Sym=digits");
}

void drawSetupDaemon() {
  display::clear(display::COL_BG);
  header("Daemon setup");
  display::drawText(8, 28, setupField == 0 ? "> URL:" : "  URL:", display::COL_FG, 1);
  display::drawTextTrunc(8, 42, display::width() - 16,
                         setupField == 0 ? line.c_str() : cfg->daemonUrl.c_str(),
                         display::COL_ACCENT, 1);
  display::drawText(8, 62, setupField == 1 ? "> Token:" : "  Token:", display::COL_FG, 1);
  if (setupField == 1) {
    display::drawTextTrunc(8, 76, display::width() - 16, line.c_str(),
                           display::COL_ACCENT, 1);
  } else {
    String tshow = cfg->daemonToken.length() > 8
                       ? cfg->daemonToken.substring(0, 6) + "..."
                       : cfg->daemonToken;
    display::drawText(8, 76, tshow.c_str(), display::COL_ACCENT, 1);
  }
  display::drawText(8, 100, statusLine.c_str(), display::COL_FG, 1);
  footer("Enter=next/save  Bksp=del/back  Sym=digits");
}

void drawSessions() {
  display::clear(display::COL_BG);
  header("Sessions");
  int y = 24;
  int maxRows = 12;
  if (sessions.empty()) {
    display::drawText(8, 40, statusLine.length() ? statusLine.c_str() : "(empty)",
                      display::COL_DIM, 1);
  }
  int first = listFirst((int)sessions.size(), sel, maxRows);
  for (int i = first; i < (int)sessions.size() && i < first + maxRows; i++) {
    bool on = i == sel;
    if (on) display::fillRect(0, y - 1, display::width(), 14, display::COL_BAR);
    char row[64];
    snprintf(row, sizeof(row), "%c%s %s",
             sessions[i].working ? '*' : ' ',
             sessions[i].provider.c_str(),
             sessions[i].title.c_str());
    display::drawTextTrunc(4, y, display::width() - 8, row,
                           on ? display::COL_ACCENT : display::COL_FG, 1);
    y += 14;
  }
  footer("Knob=move  Enter=compose  r=refresh  Bksp=back");
}

void drawCompose() {
  display::clear(display::COL_BG);
  header("Compose");
  if (sel >= 0 && sel < (int)sessions.size()) {
    display::drawTextTrunc(8, 24, display::width() - 16,
                           sessions[sel].title.c_str(), display::COL_DIM, 1);
  } else {
    display::drawText(8, 24, "New session (cwd=/tmp)", display::COL_DIM, 1);
  }
  display::drawText(8, 48, line.c_str(), display::COL_FG, 1);
  display::drawText(8, 100, statusLine.c_str(), display::COL_ACCENT, 1);
  footer("Enter=send  Bksp=del/back");
}

void drawStatus() {
  display::clear(display::COL_BG);
  header("Chimes");
  display::drawText(8, 30, statusLine.c_str(), display::COL_FG, 1);
  auto bat = power::read();
  char b[48];
  snprintf(b, sizeof(b), "Battery %d%%  %s", bat.percent,
           bat.charging ? "charging" : (bat.usb ? "USB" : "bat"));
  display::drawText(8, 50, b, display::COL_DIM, 1);
  display::drawText(8, 80, "Test: 1 status  2 done  3 err  4 attn (Sym+key)",
                    display::COL_FG, 1);
  display::drawText(8, 100, "Sound cues:", display::COL_DIM, 1);
  display::drawText(100, 100, cfg->soundCues ? "ON" : "OFF", display::COL_ACCENT, 1);
  footer("t=toggle sound  Bksp=back");
}

// Save Wi-Fi credentials, start joining, and move to the next setup step.
void joinPending(const String &pass) {
  cfg->wifiSsid = pendingSsid;
  cfg->wifiPass = pass;
  cfg->save();
  wifi_mgr::connect(cfg->wifiSsid, cfg->wifiPass);
  statusLine = "Joining " + pendingSsid + "...";
  line = "";
  setupField = 0;
  if (cfg->daemonUrl.length() == 0) {
    setScreen(Screen::SetupDaemon);
  } else {
    setScreen(Screen::Beeper);
  }
}

bool refreshSessions(bool force) {
  if (!force && millis() - lastTitleFetch < 20000) return false;
  if (wifi_mgr::state() != wifi_mgr::State::Connected) return false;
  if (!cfg || !cfg->configured()) return false;
  lastTitleFetch = millis();
  String err;
  return agentapi::fetchSessions(&sessions, &err);
}

void openMenuItem(int idx) {
  Screen target = kMenu[idx].target;
  switch (target) {
    case Screen::Sessions: {
      statusLine = "Loading...";
      draw();
      String err;
      if (agentapi::fetchSessions(&sessions, &err)) {
        statusLine = String(sessions.size()) + " sessions";
        sel = 0;
        setScreen(Screen::Sessions);
      } else {
        statusLine = err;
        chime::play(chime::Cue::Error, cfg->soundCues, cfg->hapticCues);
        draw();
      }
      break;
    }
    case Screen::Compose:
      sel = -1;
      setScreen(Screen::Compose);
      break;
    case Screen::WifiScan:
      startWifiSetup();
      break;
    case Screen::SetupDaemon:
      setupField = 0;
      line = cfg->daemonUrl;
      setScreen(Screen::SetupDaemon);
      break;
    case Screen::Power:
      powerSel = 0;
      setScreen(Screen::Power);
      break;
    default:
      setScreen(target);
      break;
  }
}

// ---- beeper feed diffing --------------------------------------------------

String toolSigOf(const statusfeed::JobStat &j) {
  return j.phase + "|" + j.tool + "|" + j.toolDetail;
}

const PrevJob *findPrev(const String &id) {
  for (auto &p : prevJobs)
    if (p.id == id) return &p;
  return nullptr;
}

void diffFeed() {
  const auto &jobs = statusfeed::jobs();

  bool sawStart = false, sawDone = false, sawAttention = false, sawTick = false;
  String doneTitle, attnTitle;

  // Ends: previous ids that vanished.
  for (auto &p : prevJobs) {
    bool still = false;
    for (const auto &j : jobs)
      if (j.jobId == p.id) still = true;
    if (!still) {
      sawDone = true;
    }
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

  // First snapshot after connect: seed silently so a reboot next to three
  // working agents does not fire a chime volley.
  if (feedSeeded) {
    if (sawAttention) {
      chime::play(chime::Cue::Attention, cfg->soundCues, cfg->hapticCues);
      ticker = "needs you: " + attnTitle;
      remindNextAt = millis() + kRemindMs;
    } else if (sawDone) {
      chime::play(chime::Cue::Done, cfg->soundCues, cfg->hapticCues);
      ticker = "turn ended";
    } else if (sawStart) {
      chime::play(chime::Cue::Status, cfg->soundCues, cfg->hapticCues);
      ticker = "turn started";
    } else if (sawTick) {
      chime::play(chime::Cue::Tick, false, cfg->hapticCues);
    }
  } else {
    feedSeeded = true;
    if (pendingNow) remindNextAt = millis() + kRemindMs;
  }

  anyPending = pendingNow;
  statusLine = jobs.empty()
                   ? ""
                   : String((int)jobs.size()) +
                         (jobs.size() == 1 ? " agent working" : " agents working");

  prevJobs.clear();
  for (const auto &j : jobs)
    prevJobs.push_back({j.jobId, toolSigOf(j),
                        j.pendingPermission || j.pendingQuestion});

  // Titles for cards; cheap because rate-limited.
  if (!jobs.empty()) refreshSessions(false);
}

}  // namespace

void begin(AppConfig *c) {
  cfg = c;
  touch();
  scr = cfg && cfg->configured() ? Screen::Beeper : Screen::Boot;
  statusLine = cfg && cfg->configured() ? "" : "Configure Wi-Fi + daemon";
}

void setScreen(Screen s) {
  scr = s;
  if (s != Screen::SetupDaemon) line = "";
  touch();
  draw();
}

Screen screen() { return scr; }

void draw() {
  switch (scr) {
    case Screen::Boot: drawBoot(); break;
    case Screen::Beeper: drawBeeper(); break;
    case Screen::Menu: drawMenu(); break;
    case Screen::Sessions: drawSessions(); break;
    case Screen::Compose: drawCompose(); break;
    case Screen::Status: drawStatus(); break;
    case Screen::WifiScan: drawWifiScan(); break;
    case Screen::WifiManual: drawWifiManual(); break;
    case Screen::WifiPass: drawWifiPass(); break;
    case Screen::SetupDaemon: drawSetupDaemon(); break;
    case Screen::Power: drawPower(); break;
  }
  display::flush();
  lastDraw = millis();
}

void markActivity() { touch(); }
uint32_t lastActivityMs() { return lastAct; }

void onRotary(int delta) {
  touch();
  if (scr == Screen::Menu) {
    menuSel += delta;
    if (menuSel < 0) menuSel = 0;
    if (menuSel >= kMenuCount) menuSel = kMenuCount - 1;
    draw();
  } else if (scr == Screen::Power) {
    powerSel += delta;
    if (powerSel < 0) powerSel = 0;
    if (powerSel > 2) powerSel = 2;
    draw();
  } else if (scr == Screen::WifiScan) {
    int total = wifi_mgr::scanCount() + 2;
    netSel += delta;
    if (netSel < 0) netSel = 0;
    if (netSel >= total) netSel = total - 1;
    draw();
  } else if (scr == Screen::Sessions) {
    sel += delta;
    if (sel < 0) sel = 0;
    if (sel >= (int)sessions.size()) sel = (int)sessions.size() - 1;
    if (sel < 0) sel = 0;
    draw();
  }
}

bool onKey(char ch, bool enter, bool backspace, bool back) {
  touch();

  if (scr == Screen::Boot) {
    if (enter || back) {
      if (cfg->wifiSsid.length()) {
        wifi_mgr::connect(cfg->wifiSsid, cfg->wifiPass);
        agentapi::configure(cfg->apiBase(), cfg->daemonToken);
        statusfeed::configure(cfg->apiBase(), cfg->daemonToken);
        setScreen(Screen::Beeper);
      } else {
        startWifiSetup();
      }
      return true;
    }
    return false;
  }

  if (scr == Screen::Beeper) {
    if (enter || ch == 'm') {
      menuSel = 0;
      setScreen(Screen::Menu);
      return true;
    }
    return false;
  }

  if (scr == Screen::Menu) {
    if (back || backspace) {
      setScreen(Screen::Beeper);
      return true;
    }
    if (ch == 'j') { onRotary(1); return true; }
    if (ch == 'k') { onRotary(-1); return true; }
    if (ch >= '1' && ch < '1' + kMenuCount) {
      menuSel = ch - '1';
      openMenuItem(menuSel);
      return true;
    }
    if (enter) {
      openMenuItem(menuSel);
      return true;
    }
    return false;
  }

  if (scr == Screen::Power) {
    if (back || backspace) {
      setScreen(Screen::Menu);
      return true;
    }
    if (enter) {
      if (powerSel == 0) power::powerOff();
      else if (powerSel == 1) power::restart();
      else setScreen(Screen::Menu);
      return true;
    }
    return false;
  }

  if (scr == Screen::WifiScan) {
    if (back || backspace) {
      setScreen(Screen::Menu);
      return true;
    }
    int count = wifi_mgr::scanCount();
    if (ch == 'j') { onRotary(1); return true; }
    if (ch == 'k') { onRotary(-1); return true; }
    if (ch == 'r') {
      startWifiSetup();
      return true;
    }
    if (enter) {
      if (netSel < count) {
        pendingSsid = wifi_mgr::scanItem(netSel).ssid;
        if (!wifi_mgr::scanItem(netSel).secure) {
          joinPending("");  // open network — no password step
        } else {
          maskArmed = false;
          statusLine = "";
          setScreen(Screen::WifiPass);
        }
      } else if (netSel == count) {
        startWifiSetup();  // rescan
      } else {
        setScreen(Screen::WifiManual);
      }
      return true;
    }
    return false;
  }

  if (scr == Screen::WifiManual) {
    if (back) {
      setScreen(Screen::WifiScan);
      return true;
    }
    if (backspace) {
      if (line.length()) {
        line.remove(line.length() - 1);
        draw();
      } else {
        setScreen(Screen::WifiScan);
      }
      return true;
    }
    if (enter) {
      if (line.length()) {
        pendingSsid = line;
        maskArmed = false;
        statusLine = "";
        setScreen(Screen::WifiPass);
      }
      return true;
    }
    if (ch >= 32 && ch < 127) {
      line += ch;
      draw();
      return true;
    }
    return false;
  }

  if (scr == Screen::WifiPass) {
    if (back) {
      setScreen(Screen::WifiScan);
      return true;
    }
    if (backspace) {
      if (line.length()) {
        line.remove(line.length() - 1);
        maskArmed = false;
        draw();
      } else {
        setScreen(Screen::WifiScan);
      }
      return true;
    }
    if (enter) {
      joinPending(line);
      return true;
    }
    if (ch >= 32 && ch < 127) {
      line += ch;
      lastCharMs = millis();
      maskArmed = true;
      draw();
      return true;
    }
    return false;
  }

  if (scr == Screen::SetupDaemon) {
    if (back) {
      setScreen(Screen::Menu);
      return true;
    }
    if (backspace) {
      if (line.length()) {
        line.remove(line.length() - 1);
        draw();
      } else if (setupField == 1) {
        setupField = 0;
        line = cfg->daemonUrl;
        draw();
      } else {
        setScreen(Screen::Menu);
      }
      return true;
    }
    if (enter) {
      if (setupField == 0) {
        if (line.length()) cfg->daemonUrl = line;
        setupField = 1;
        line = "";
      } else {
        if (line.length()) cfg->daemonToken = line;
        line = "";
        setupField = 0;
        cfg->save();
        agentapi::configure(cfg->apiBase(), cfg->daemonToken);
        statusfeed::configure(cfg->apiBase(), cfg->daemonToken);
        feedSeeded = false;
        // Immediate reachability check — a wrong URL should fail HERE, not
        // as a silent "All quiet" later.
        statusLine = "Checking daemon...";
        draw();
        String ver, err;
        if (agentapi::ping(&ver, &err)) {
          statusLine = "Daemon OK (v" + ver + ")";
          chime::play(chime::Cue::Status, cfg->soundCues, cfg->hapticCues);
        } else {
          statusLine = "Unreachable: " + err;
          chime::play(chime::Cue::Error, cfg->soundCues, cfg->hapticCues);
        }
        setScreen(Screen::Beeper);
        return true;
      }
      draw();
      return true;
    }
    if (ch >= 32 && ch < 127) {
      line += ch;
      draw();
      return true;
    }
    return false;
  }

  if (scr == Screen::Sessions) {
    if (back || backspace) {
      setScreen(Screen::Menu);
      return true;
    }
    if (ch == 'j' || ch == 'n') { onRotary(1); return true; }
    if (ch == 'k' || ch == 'p') { onRotary(-1); return true; }
    if (ch == 'r') {
      String err;
      agentapi::fetchSessions(&sessions, &err);
      statusLine = err.length() ? err : "refreshed";
      draw();
      return true;
    }
    if (enter && sel >= 0 && sel < (int)sessions.size()) {
      setScreen(Screen::Compose);
      return true;
    }
    return false;
  }

  if (scr == Screen::Compose) {
    if (back) {
      setScreen(sessions.empty() ? Screen::Menu : Screen::Sessions);
      return true;
    }
    if (backspace) {
      if (line.length()) {
        line.remove(line.length() - 1);
        draw();
      } else {
        setScreen(sessions.empty() ? Screen::Menu : Screen::Sessions);
      }
      return true;
    }
    if (enter) {
      if (line.length() == 0) return true;
      String err;
      bool ok = false;
      if (sel >= 0 && sel < (int)sessions.size()) {
        ok = agentapi::sendPrompt(sessions[sel].id, line, &err);
      } else {
        ok = agentapi::newSession("/tmp", line, "", &err);
      }
      if (ok) {
        statusLine = "sent";
        chime::play(chime::Cue::Status, cfg->soundCues, cfg->hapticCues);
        line = "";
      } else {
        statusLine = err;
        chime::play(chime::Cue::Error, cfg->soundCues, cfg->hapticCues);
      }
      draw();
      return true;
    }
    if (ch >= 32 && ch < 127) {
      line += ch;
      draw();
      return true;
    }
    return false;
  }

  if (scr == Screen::Status) {
    if (back || backspace) {
      setScreen(Screen::Menu);
      return true;
    }
    if (ch == 't') {
      cfg->soundCues = !cfg->soundCues;
      cfg->save();
      chime::setEnabled(cfg->soundCues, cfg->hapticCues);
      draw();
      return true;
    }
    if (ch == '1') {
      chime::play(chime::Cue::Status, true, cfg->hapticCues);
      return true;
    }
    if (ch == '2') {
      chime::play(chime::Cue::Done, true, cfg->hapticCues);
      return true;
    }
    if (ch == '3') {
      chime::play(chime::Cue::Error, true, cfg->hapticCues);
      return true;
    }
    if (ch == '4') {
      chime::play(chime::Cue::Attention, true, cfg->hapticCues);
      return true;
    }
    return false;
  }

  return false;
}

void onTick() {
  // Async Wi-Fi scan finishing while the picker is open.
  if (scr == Screen::WifiScan && wifi_mgr::scanning() && wifi_mgr::scanReady()) {
    statusLine = "";
    if (netSel >= wifi_mgr::scanCount() + 2) netSel = 0;
    draw();
  }

  // Re-mask the last password char once the reveal window closes.
  if (scr == Screen::WifiPass && maskArmed &&
      millis() - lastCharMs >= kPassRevealMs) {
    maskArmed = false;
    draw();
  }

  // Wi-Fi state changes refresh the header on settings-ish screens.
  static uint8_t lastWifiState = 255;
  uint8_t ws = (uint8_t)wifi_mgr::state();
  if (ws != lastWifiState) {
    lastWifiState = ws;
    if (scr != Screen::Boot) draw();
  }

  // ---- beeper: real-time feed ----
  if (statusfeed::generation() != lastFeedGen) {
    lastFeedGen = statusfeed::generation();
    if (statusfeed::state() == statusfeed::State::Live) {
      diffFeed();
      if (scr == Screen::Beeper) draw();
    } else {
      // Feed dropped: next Live snapshot re-seeds silently.
      feedSeeded = false;
    }
  }

  // Standing reminder while a question/permission waits for the user.
  if (anyPending && millis() >= remindNextAt) {
    remindNextAt = millis() + kRemindMs;
    chime::play(chime::Cue::Attention, cfg->soundCues, cfg->hapticCues);
    if (scr == Screen::Beeper) draw();
  }

  // Elapsed counters + pending blink need a periodic repaint.
  if (scr == Screen::Beeper && !statusfeed::jobs().empty()) {
    uint32_t interval = anyPending ? 500 : 1000;
    if (millis() - lastDraw >= interval) draw();
  }

  // ---- fallback poll when the SSE feed is not live ----
  // Backs off while the daemon is unreachable so blocking HTTP timeouts do
  // not starve the input loop (frozen knob/keys).
  static uint32_t pollDelay = STATUS_POLL_MS;
  if (statusfeed::state() == statusfeed::State::Live) return;
  if (millis() - lastPoll < pollDelay) return;
  lastPoll = millis();
  if (wifi_mgr::state() != wifi_mgr::State::Connected) return;
  if (!cfg || !cfg->configured()) return;

  StatusSnap s = agentapi::pollStatus();
  if (!s.ok) {
    pollDelay = pollDelay >= 30000 ? 60000 : pollDelay * 2;
    return;
  }
  pollDelay = STATUS_POLL_MS;
  String sig = agentapi::statusSignature(s);
  if (sig != lastSig) {
    if (s.needsYou) {
      chime::play(chime::Cue::Attention, cfg->soundCues, cfg->hapticCues);
    } else if (s.working > 0 && lastSig.indexOf("|0|") >= 0) {
      chime::play(chime::Cue::Status, cfg->soundCues, cfg->hapticCues);
    } else if (s.working == 0 && lastSig.length() && lastSig[0] != '0') {
      chime::play(chime::Cue::Done, cfg->soundCues, cfg->hapticCues);
    }
    lastSig = sig;
    statusLine = s.needsYou
                     ? "Needs you"
                     : (s.working ? (String(s.working) + " working " + s.phase)
                                  : "Idle");
    if (scr == Screen::Beeper) draw();
  }
}

}  // namespace ui
