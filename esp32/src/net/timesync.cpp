#include "net/timesync.h"
#include "hw/dlog.h"

#include <sys/time.h>
#include <time.h>

namespace timesync {
namespace {

// Display offset from UTC. Default CST (UTC+8); a BLE time-sync replaces it
// with the desktop's real zone. TODO: settings UI if this ever needs to be
// configured on-device.
int32_t g_offset = 8 * 3600;
bool g_ntpStarted = false;

}  // namespace

void beginNtp() {
  if (g_ntpStarted) return;
  g_ntpStarted = true;
  // Offset 0: keep the system clock UTC; format-time applies g_offset.
  configTime(0, 0, "pool.ntp.org", "ntp.aliyun.com", "time.windows.com");
  dlog::logf("[time] SNTP started");
}

void setFromBle(uint32_t epochUtc, int32_t offsetSec) {
  struct timeval tv;
  tv.tv_sec = (time_t)epochUtc;
  tv.tv_usec = 0;
  settimeofday(&tv, nullptr);
  g_offset = offsetSec;
  dlog::logf("[time] BLE sync: epoch %u offset %d", (unsigned)epochUtc,
             (int)offsetSec);
}

bool valid() { return time(nullptr) > 1700000000; }

void nowLocal(int *hourOut, int *minOut) {
  time_t t = time(nullptr) + g_offset;
  struct tm tmv;
  gmtime_r(&t, &tmv);
  if (hourOut) *hourOut = tmv.tm_hour;
  if (minOut) *minOut = tmv.tm_min;
}

}  // namespace timesync
