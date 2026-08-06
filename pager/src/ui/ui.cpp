#include "ui/ui.h"
#include "board_pins.h"
#include "hw/display.h"
#include "hw/chime.h"
#include "hw/wifi_mgr.h"
#include "hw/power.h"

namespace ui {
namespace {

AppConfig *cfg = nullptr;
Screen scr = Screen::Boot;
String line;
String statusLine;
String lastSig;
uint32_t lastAct = 0;
uint32_t lastDraw = 0;
uint32_t lastPoll = 0;
std::vector<SessionRow> sessions;
int sel = 0;
String setupBuf;
int setupField = 0;  // wifi: 0 ssid 1 pass; daemon: 0 url 1 token

void touch() { lastAct = millis(); }

void header(const char *title) {
  display::fillRect(0, 0, display::width(), 18, display::COL_BAR);
  display::drawText(4, 3, title, display::COL_FG, 1);
  auto bat = power::read();
  char right[24];
  if (bat.percent >= 0)
    snprintf(right, sizeof(right), "%s %d%%", wifi_mgr::stateName(), bat.percent);
  else
    snprintf(right, sizeof(right), "%s", wifi_mgr::stateName());
  display::drawText(display::width() - 90, 3, right, display::COL_DIM, 1);
  display::hLine(18, display::COL_DIM);
}

void footer(const char *hint) {
  int y = display::height() - 14;
  display::fillRect(0, y - 2, display::width(), 16, display::COL_BAR);
  display::drawText(4, y, hint, display::COL_DIM, 1);
}

void drawBoot() {
  display::clear(display::COL_BG);
  header("Agent Remote");
  display::drawText(8, 40, "T-LoRa Pager client", display::COL_FG, 2);
  display::drawText(8, 70, "Wi-Fi + agentremoted", display::COL_DIM, 1);
  display::drawText(8, 100, statusLine.c_str(), display::COL_ACCENT, 1);
  footer("Enter=continue  Esc=setup");
}

void drawSetupWifi() {
  display::clear(display::COL_BG);
  header("Wi-Fi setup");
  display::drawText(8, 28, setupField == 0 ? "> SSID:" : "  SSID:", display::COL_FG, 1);
  display::drawText(8, 42, cfg->wifiSsid.c_str(), display::COL_ACCENT, 1);
  display::drawText(8, 62, setupField == 1 ? "> Password:" : "  Password:", display::COL_FG, 1);
  String dots;
  for (size_t i = 0; i < cfg->wifiPass.length(); i++) dots += '*';
  display::drawText(8, 76, dots.c_str(), display::COL_ACCENT, 1);
  display::drawText(8, 100, line.c_str(), display::COL_FG, 1);
  footer("Tab=field  Enter=next/save  Esc=back");
}

void drawSetupDaemon() {
  display::clear(display::COL_BG);
  header("Daemon setup");
  display::drawText(8, 28, setupField == 0 ? "> URL:" : "  URL:", display::COL_FG, 1);
  display::drawTextTrunc(8, 42, display::width() - 16, cfg->daemonUrl.c_str(),
                         display::COL_ACCENT, 1);
  display::drawText(8, 62, setupField == 1 ? "> Token:" : "  Token:", display::COL_FG, 1);
  String tshow = cfg->daemonToken.length() > 8
                     ? cfg->daemonToken.substring(0, 6) + "…"
                     : cfg->daemonToken;
  display::drawText(8, 76, tshow.c_str(), display::COL_ACCENT, 1);
  display::drawText(8, 100, line.c_str(), display::COL_FG, 1);
  footer("Tab=field  Enter=save  Esc=back");
}

void drawHome() {
  display::clear(display::COL_BG);
  header("Home");
  display::drawText(8, 30, "1 Sessions", display::COL_FG, 2);
  display::drawText(8, 56, "2 Compose / new", display::COL_FG, 2);
  display::drawText(8, 82, "3 Status + chime test", display::COL_FG, 2);
  display::drawText(8, 108, "4 Setup Wi-Fi / daemon", display::COL_FG, 2);
  display::drawText(8, 140, statusLine.c_str(), display::COL_DIM, 1);
  footer("Type number  Esc=sleep menu");
}

void drawSessions() {
  display::clear(display::COL_BG);
  header("Sessions");
  int y = 24;
  int maxRows = 8;
  if (sessions.empty()) {
    display::drawText(8, 40, statusLine.length() ? statusLine.c_str() : "(empty)",
                      display::COL_DIM, 1);
  }
  for (int i = 0; i < (int)sessions.size() && i < maxRows; i++) {
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
  footer("j/k or rot  Enter=compose  r=refresh  Esc=home");
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
  footer("Enter=send  Esc=back");
}

void drawStatus() {
  display::clear(display::COL_BG);
  header("Status / Chime");
  display::drawText(8, 30, statusLine.c_str(), display::COL_FG, 1);
  auto bat = power::read();
  char b[48];
  snprintf(b, sizeof(b), "Battery %d%%  %s", bat.percent,
           bat.charging ? "charging" : (bat.usb ? "USB" : "bat"));
  display::drawText(8, 50, b, display::COL_DIM, 1);
  display::drawText(8, 80, "Keys: 1 status  2 done  3 err  4 attn", display::COL_FG, 1);
  display::drawText(8, 100, "Sound cues:", display::COL_DIM, 1);
  display::drawText(100, 100, cfg->soundCues ? "ON" : "OFF", display::COL_ACCENT, 1);
  footer("t=toggle sound  Esc=home");
}

}  // namespace

void begin(AppConfig *c) {
  cfg = c;
  touch();
  scr = cfg && cfg->configured() ? Screen::Home : Screen::Boot;
  statusLine = cfg && cfg->configured() ? "Ready" : "Configure Wi-Fi + daemon";
}

void setScreen(Screen s) {
  scr = s;
  line = "";
  touch();
  draw();
}

Screen screen() { return scr; }

void draw() {
  switch (scr) {
    case Screen::Boot: drawBoot(); break;
    case Screen::SetupWifi: drawSetupWifi(); break;
    case Screen::SetupDaemon: drawSetupDaemon(); break;
    case Screen::Home: drawHome(); break;
    case Screen::Sessions: drawSessions(); break;
    case Screen::Compose: drawCompose(); break;
    case Screen::Status: drawStatus(); break;
  }
  display::flush();
  lastDraw = millis();
}

void markActivity() { touch(); }
uint32_t lastActivityMs() { return lastAct; }

bool onKey(char ch, bool enter, bool backspace, bool escape) {
  touch();
  if (scr == Screen::Boot) {
    if (enter) {
      if (cfg->wifiSsid.length()) {
        wifi_mgr::connect(cfg->wifiSsid, cfg->wifiPass);
        agentapi::configure(cfg->apiBase(), cfg->daemonToken);
        setScreen(Screen::Home);
      } else {
        setScreen(Screen::SetupWifi);
      }
      return true;
    }
    if (escape) {
      setScreen(Screen::SetupWifi);
      return true;
    }
  }

  if (scr == Screen::SetupWifi || scr == Screen::SetupDaemon) {
    if (escape) {
      setScreen(Screen::Home);
      return true;
    }
    if (ch == '\t' || (ch == ' ' && line.length() == 0)) {
      setupField = 1 - setupField;
      line = "";
      draw();
      return true;
    }
    if (backspace) {
      if (line.length()) line.remove(line.length() - 1);
      draw();
      return true;
    }
    if (enter) {
      if (scr == Screen::SetupWifi) {
        if (setupField == 0) {
          cfg->wifiSsid = line;
          setupField = 1;
          line = "";
        } else {
          cfg->wifiPass = line;
          line = "";
          setupField = 0;
          cfg->save();
          wifi_mgr::connect(cfg->wifiSsid, cfg->wifiPass);
          setScreen(Screen::SetupDaemon);
          return true;
        }
      } else {
        if (setupField == 0) {
          cfg->daemonUrl = line;
          setupField = 1;
          line = "";
        } else {
          cfg->daemonToken = line;
          line = "";
          setupField = 0;
          cfg->save();
          agentapi::configure(cfg->apiBase(), cfg->daemonToken);
          statusLine = "Saved";
          setScreen(Screen::Home);
          return true;
        }
      }
      draw();
      return true;
    }
    if (ch >= 32 && ch < 127) {
      line += ch;
      draw();
      return true;
    }
  }

  if (scr == Screen::Home) {
    if (ch == '1') {
      statusLine = "Loading…";
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
      return true;
    }
    if (ch == '2') {
      sel = -1;
      setScreen(Screen::Compose);
      return true;
    }
    if (ch == '3') {
      setScreen(Screen::Status);
      return true;
    }
    if (ch == '4') {
      setupField = 0;
      setScreen(Screen::SetupWifi);
      return true;
    }
  }

  if (scr == Screen::Sessions) {
    if (escape) {
      setScreen(Screen::Home);
      return true;
    }
    if (ch == 'j' || ch == 'n') {
      if (sel + 1 < (int)sessions.size()) sel++;
      draw();
      return true;
    }
    if (ch == 'k' || ch == 'p') {
      if (sel > 0) sel--;
      draw();
      return true;
    }
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
  }

  if (scr == Screen::Compose) {
    if (escape) {
      setScreen(sessions.empty() ? Screen::Home : Screen::Sessions);
      return true;
    }
    if (backspace) {
      if (line.length()) line.remove(line.length() - 1);
      draw();
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
  }

  if (scr == Screen::Status) {
    if (escape) {
      setScreen(Screen::Home);
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
  }

  return false;
}

void onTick() {
  // Status poll + chimes when working / needs-you changes.
  if (millis() - lastPoll < STATUS_POLL_MS) return;
  lastPoll = millis();
  if (wifi_mgr::state() != wifi_mgr::State::Connected) return;
  if (!cfg || !cfg->configured()) return;

  StatusSnap s = agentapi::pollStatus();
  if (!s.ok) return;
  String sig = agentapi::statusSignature(s);
  if (sig != lastSig) {
    if (s.needsYou) {
      chime::play(chime::Cue::Attention, cfg->soundCues, cfg->hapticCues);
    } else if (s.working > 0 && lastSig.indexOf("|0|") >= 0) {
      // transition into working
      chime::play(chime::Cue::Status, cfg->soundCues, cfg->hapticCues);
    } else if (s.working == 0 && lastSig.length() && lastSig[0] != '0') {
      chime::play(chime::Cue::Done, cfg->soundCues, cfg->hapticCues);
    }
    lastSig = sig;
    statusLine = s.needsYou
                     ? "Needs you"
                     : (s.working ? (String(s.working) + " working " + s.phase)
                                  : "Idle");
    if (scr == Screen::Home || scr == Screen::Status) draw();
  }
}

}  // namespace ui
