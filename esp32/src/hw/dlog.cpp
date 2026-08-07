#include "hw/dlog.h"

#include <esp_log.h>
#include <esp_system.h>
#include <stdarg.h>

namespace dlog {
namespace {

// Last ~1.5 KB of log survives panics in RTC noinit RAM; the next boot
// replays it into the fresh ring so Diag → upload shows the crash trail.
RTC_NOINIT_ATTR char rtcTail[1536];
RTC_NOINIT_ATTR uint32_t rtcPos;
RTC_NOINIT_ATTR uint32_t rtcMagic;
constexpr uint32_t kRtcMagic = 0xA6E1707;

constexpr size_t kRing = 16 * 1024;
char *ring = nullptr;
size_t head = 0;
bool wrapped = false;
vprintf_like_t prevVprintf = nullptr;

void put(const char *s, size_t n) {
  if (!ring) return;
  for (size_t i = 0; i < n; i++) {
    ring[head] = s[i];
    head = (head + 1) % kRing;
    if (head == 0) wrapped = true;
  }
}

void rtcPut(const char *s, size_t n) {
  if (rtcMagic != kRtcMagic) return;
  for (size_t i = 0; i < n; i++) {
    rtcTail[rtcPos % sizeof(rtcTail)] = s[i];
    rtcPos++;
  }
}

void putLine(const char *line) {
  char ts[16];
  snprintf(ts, sizeof(ts), "[%8lu] ", (unsigned long)millis());
  put(ts, strlen(ts));
  put(line, strlen(line));
  size_t n = strlen(line);
  if (n == 0 || line[n - 1] != '\n') put("\n", 1);
  rtcPut(ts, strlen(ts));
  rtcPut(line, n);
  rtcPut("\n", 1);
}

int espLogHook(const char *fmt, va_list args) {
  char buf[256];
  vsnprintf(buf, sizeof(buf), fmt, args);
  put(buf, strlen(buf));
  return prevVprintf ? prevVprintf(fmt, args) : vprintf(fmt, args);
}

}  // namespace

void begin() {
  ring = (char *)(psramFound() ? ps_malloc(kRing) : malloc(kRing));
  if (ring) memset(ring, 0, kRing);
  prevVprintf = esp_log_set_vprintf(espLogHook);
  logf("dlog ready (%u KB ring)", (unsigned)(kRing / 1024));

  // A panic/watchdog reset keeps RTC RAM: replay the pre-crash trail.
  esp_reset_reason_t r = esp_reset_reason();
  bool crashed = r == ESP_RST_PANIC || r == ESP_RST_INT_WDT ||
                 r == ESP_RST_TASK_WDT || r == ESP_RST_WDT;
  if (rtcMagic == kRtcMagic && crashed) {
    logf("=== PREVIOUS BOOT CRASHED (reset reason %d); last log: ===", (int)r);
    size_t total = rtcPos < sizeof(rtcTail) ? rtcPos : sizeof(rtcTail);
    size_t start = rtcPos < sizeof(rtcTail) ? 0 : rtcPos % sizeof(rtcTail);
    for (size_t i = 0; i < total; i++) {
      char c = rtcTail[(start + i) % sizeof(rtcTail)];
      if (c) put(&c, 1);
    }
    put("\n", 1);
    logf("=== end of crash trail ===");
  }
  rtcMagic = kRtcMagic;
  rtcPos = 0;
  memset(rtcTail, 0, sizeof(rtcTail));
}

void logf(const char *fmt, ...) {
  char buf[256];
  va_list args;
  va_start(args, fmt);
  vsnprintf(buf, sizeof(buf), fmt, args);
  va_end(args);
  Serial.println(buf);
  putLine(buf);
}

String dump() {
  if (!ring) return String();
  String out;
  out.reserve(wrapped ? kRing : head);
  if (wrapped) {
    for (size_t i = head; i < kRing; i++)
      if (ring[i]) out += ring[i];
  }
  for (size_t i = 0; i < head; i++) out += ring[i];
  return out;
}

}  // namespace dlog
