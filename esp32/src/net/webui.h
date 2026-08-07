#pragma once
// On-device web server for managing the SD card from a browser: list,
// download, upload, delete. Runs only while the Web screen is open —
// serving shares the display's SPI bus and a socket, so it is scoped to
// the moments the user actually wants it. HTTP Basic auth with a per-start
// password shown on the pager's screen.

#include <Arduino.h>

namespace webui {

void begin();                 // start server, mint a fresh password
void stop();
void tick();                  // handleClient(); call from loop while active
bool active();
String url();                 // http://<ip>/
const String &password();     // for the on-screen hint (user: pager)

}  // namespace webui
