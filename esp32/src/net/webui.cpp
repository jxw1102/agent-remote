#include "net/webui.h"
#include "board_pins.h"
#include "hw/dlog.h"

#include <SD.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_random.h>

namespace webui {
namespace {

WebServer *server = nullptr;
String g_pass;
File uploadFile;

bool auth() {
  if (server->authenticate("pager", g_pass.c_str())) return true;
  server->requestAuthentication();
  return false;
}

String htmlEscape(const String &s) {
  String o;
  o.reserve(s.length());
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '<') o += "&lt;";
    else if (c == '>') o += "&gt;";
    else if (c == '&') o += "&amp;";
    else if (c == '"') o += "&quot;";
    else o += c;
  }
  return o;
}

String sanitizeName(String n) {
  int slash = n.lastIndexOf('/');
  if (slash >= 0) n = n.substring(slash + 1);
  String o;
  for (size_t i = 0; i < n.length() && o.length() < 48; i++) {
    char c = n[i];
    if (isalnum(c) || c == '-' || c == '_' || c == '.') o += c;
  }
  return o.length() ? o : String("file");
}

void handleRoot() {
  if (!auth()) return;
  String page =
      "<!doctype html><meta name=viewport content='width=device-width'>"
      "<title>Agent Remote SD</title><style>"
      "body{font:14px system-ui;background:#14141a;color:#e8e8ee;margin:2em}"
      "table{border-collapse:collapse;min-width:24em}td,th{padding:.4em .8em;"
      "border-bottom:1px solid #333;text-align:left}a{color:#7cc7ff}"
      "h1{font-size:1.2em}form{margin:1em 0}input{margin-right:.5em}"
      ".btn{color:#ff8a8a}</style>"
      "<h1>&gt;_ Agent Remote &mdash; SD card</h1>"
      "<form method=post action=/upload enctype=multipart/form-data>"
      "<input type=file name=f required> <input type=submit value=Upload>"
      "</form><table><tr><th>Name</th><th>Size</th><th></th></tr>";
  File root = SD.open("/");
  if (root) {
    File f = root.openNextFile();
    while (f) {
      if (!f.isDirectory()) {
        String name = f.name();
        if (name.startsWith("/")) name = name.substring(1);
        page += "<tr><td><a href='/dl?f=" + name + "'>" + htmlEscape(name) +
                "</a></td><td>" + String((unsigned)f.size()) +
                "</td><td><a class=btn href='/rm?f=" + name +
                "' onclick='return confirm(\"Delete " + htmlEscape(name) +
                "?\")'>delete</a></td></tr>";
      }
      f = root.openNextFile();
    }
    root.close();
  } else {
    page += "<tr><td colspan=3>No SD card</td></tr>";
  }
  uint64_t total = SD.totalBytes(), used = SD.usedBytes();
  page += "</table><p>" + String((unsigned)(used / 1048576)) + " / " +
          String((unsigned)(total / 1048576)) + " MB used</p>";
  server->send(200, "text/html", page);
}

void handleDownload() {
  if (!auth()) return;
  String name = sanitizeName(server->arg("f"));
  File f = SD.open("/" + name, FILE_READ);
  if (!f) {
    server->send(404, "text/plain", "not found");
    return;
  }
  server->sendHeader("Content-Disposition",
                     "attachment; filename=\"" + name + "\"");
  server->streamFile(f, "application/octet-stream");
  f.close();
}

void handleDelete() {
  if (!auth()) return;
  String name = sanitizeName(server->arg("f"));
  bool ok = SD.remove("/" + name);
  dlog::logf("[web] delete %s: %s", name.c_str(), ok ? "ok" : "failed");
  server->sendHeader("Location", "/");
  server->send(303);
}

void handleUploadData() {
  // Auth checked in the completion handler; still avoid writing for
  // unauthenticated posts.
  if (!server->authenticate("pager", g_pass.c_str())) return;
  HTTPUpload &up = server->upload();
  if (up.status == UPLOAD_FILE_START) {
    String name = sanitizeName(up.filename);
    SD.remove("/" + name);
    uploadFile = SD.open("/" + name, FILE_WRITE);
    dlog::logf("[web] upload start: %s", name.c_str());
  } else if (up.status == UPLOAD_FILE_WRITE && uploadFile) {
    uploadFile.write(up.buf, up.currentSize);
  } else if (up.status == UPLOAD_FILE_END && uploadFile) {
    uploadFile.close();
    dlog::logf("[web] upload done (%u bytes)", (unsigned)up.totalSize);
  }
}

void handleUploadDone() {
  if (!auth()) return;
  server->sendHeader("Location", "/");
  server->send(303);
}

}  // namespace

void begin() {
  if (server) return;
  // 6-char password, fresh per start, shown on the pager's screen.
  static const char *alpha = "abcdefghjkmnpqrstuvwxyz23456789";
  g_pass = "";
  for (int i = 0; i < 6; i++) g_pass += alpha[esp_random() % 31];

  server = new WebServer(80);
  server->on("/", HTTP_GET, handleRoot);
  server->on("/dl", HTTP_GET, handleDownload);
  server->on("/rm", HTTP_GET, handleDelete);
  server->on("/upload", HTTP_POST, handleUploadDone, handleUploadData);
  server->onNotFound([]() {
    if (!auth()) return;
    server->send(404, "text/plain", "not found");
  });
  server->begin();
  // Modem power-save adds 100-300 ms to every TCP round trip — that is the
  // difference between "extremely slow" and a normal web server. Full power
  // while serving; restored on stop().
  WiFi.setSleep(false);
  dlog::logf("[web] serving at %s (user pager)", url().c_str());
}

void stop() {
  if (!server) return;
  WiFi.setSleep(true);
  server->stop();
  delete server;
  server = nullptr;
  if (uploadFile) uploadFile.close();
  dlog::logf("[web] stopped");
}

void tick() {
  if (!server) return;
  // Idle: one cheap poll — the previous unconditional 20 ms burst ran even
  // with no browser connected and dragged the whole UI loop (laggy screen).
  server->handleClient();
  // A request is in flight: now burst so the transfer runs at full speed.
  if (server->client() && server->client().connected()) {
    uint32_t until = millis() + 20;
    do {
      server->handleClient();
    } while (millis() < until && server->client() &&
             server->client().connected());
  }
}

bool active() { return server != nullptr; }

String url() { return "http://" + WiFi.localIP().toString() + "/"; }

const String &password() { return g_pass; }

}  // namespace webui
