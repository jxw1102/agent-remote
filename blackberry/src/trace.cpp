#include "trace.hpp"

#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

namespace {

const char *kDir = "/accounts/1000/shared/misc/CyberBerry";
const char *kPath = "/accounts/1000/shared/misc/CyberBerry/AgentRemote-trace.txt";
const long kMaxBytes = 512 * 1024;   // start over rather than grow forever

long long g_t0 = 0;                 // monotonic ms at init
volatile long long g_lastBeat = 0;  // monotonic ms of the last heartbeat
char g_last[200] = "(nothing yet)"; // last traced line, for stall reports

long long nowMs()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

void writeLine(const char *line, int n)
{
    int fd = ::open(kPath, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0)
        return;
    ::write(fd, line, (size_t)n);
    ::close(fd);
}

void emitLine(const char *tag, const char *msg)
{
    char buf[512];
    const long long t = nowMs() - g_t0;
    int n = snprintf(buf, sizeof(buf), "%7lld.%03lld %s %s\n",
                     t / 1000, t % 1000, tag, msg);
    if (n < 0)
        return;
    if (n > (int)sizeof(buf) - 1)
        n = (int)sizeof(buf) - 1;
    writeLine(buf, n);
}

void *watchdog(void *)
{
    long long reportedAt = 0;
    for (;;) {
        sleep(1);
        const long long beat = g_lastBeat;
        if (!beat)
            continue;
        const long long gap = nowMs() - beat;
        if (gap < 2000) {
            if (reportedAt) {
                emitLine("WDOG", "main thread is ticking again");
                reportedAt = 0;
            }
            continue;
        }
        // Report the first stall, then every 5 s while it lasts.
        if (!reportedAt || nowMs() - reportedAt >= 5000) {
            char msg[320];
            snprintf(msg, sizeof(msg),
                     "MAIN THREAD STALLED %lld ms; last traced: %s",
                     gap, g_last);
            emitLine("WDOG", msg);
            reportedAt = nowMs();
        }
    }
    return 0;
}

} // namespace

void traceInit(const char *version)
{
    g_t0 = nowMs();
    ::mkdir("/accounts/1000/shared/misc", 0755);
    ::mkdir(kDir, 0755);
    struct stat st;
    if (::stat(kPath, &st) == 0 && st.st_size > kMaxBytes)
        ::unlink(kPath);

    char head[200];
    time_t wall = time(0);
    struct tm tmv;
    localtime_r(&wall, &tmv);
    char when[40];
    strftime(when, sizeof(when), "%Y-%m-%d %H:%M:%S", &tmv);
    snprintf(head, sizeof(head),
             "==== Agent Remote %s launched %s (pid %d) ====",
             version, when, (int)getpid());
    emitLine("----", head);

    g_lastBeat = nowMs();
    pthread_t t;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    pthread_create(&t, &attr, watchdog, 0);
    pthread_attr_destroy(&attr);
}

void traceMark(const char *fmt, ...)
{
    char msg[400];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    strncpy(g_last, msg, sizeof(g_last) - 1);
    g_last[sizeof(g_last) - 1] = '\0';
    emitLine("MARK", msg);
}

void traceHeartbeat()
{
    g_lastBeat = nowMs();
}
