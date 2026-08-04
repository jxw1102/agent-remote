#include "crashguard.hpp"

#include "brand.hpp"

#include <backtrace.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <exception>

// Everything below may run inside a fatal-signal handler: only
// async-signal-safe calls (open/write/close/time) plus QNX libbacktrace,
// which supports in-process (BT_SELF) use from a handler.

namespace {

char g_crashPath[512];
volatile sig_atomic_t g_inCrash = 0;
#if defined(SA_ONSTACK)
// Alternate stack so a stack-overflow SIGSEGV can still be dumped.
// (QNX 6.x / BB10 has no sigaltstack - only compiled where it exists.)
char g_altStack[64 * 1024];
#endif

void wrStr(int fd, const char *s)
{
    if (s && *s) {
        ssize_t ignored = ::write(fd, s, ::strlen(s));
        (void)ignored;
    }
}

void wrNum(int fd, unsigned long v)
{
    char buf[24];
    int i = int(sizeof(buf));
    buf[--i] = '\0';
    do {
        buf[--i] = char('0' + (v % 10));
        v /= 10;
    } while (v != 0 && i > 0);
    wrStr(fd, buf + i);
}

void wrHex(int fd, unsigned long v)
{
    static const char digits[] = "0123456789abcdef";
    char buf[24];
    int i = int(sizeof(buf));
    buf[--i] = '\0';
    do {
        buf[--i] = digits[v & 0xf];
        v >>= 4;
    } while (v != 0 && i > 0);
    wrStr(fd, "0x");
    wrStr(fd, buf + i);
}

const char *sigName(int sig)
{
    switch (sig) {
    case SIGSEGV: return "SIGSEGV";
    case SIGBUS:  return "SIGBUS";
    case SIGILL:  return "SIGILL";
    case SIGFPE:  return "SIGFPE";
    case SIGABRT: return "SIGABRT";
    default:      return "SIGNAL";
    }
}

int openCrashFile()
{
    if (!g_crashPath[0])
        return -1;
    return ::open(g_crashPath, O_WRONLY | O_CREAT | O_APPEND, 0644);
}

void writeHeader(int fd, const char *what, unsigned long faultAddr,
                 bool haveAddr)
{
    wrStr(fd, "\n===== ");
    wrStr(fd, what);
    wrStr(fd, " unix_time=");
    wrNum(fd, (unsigned long)::time(0));
    if (haveAddr) {
        wrStr(fd, " fault_addr=");
        wrHex(fd, faultAddr);
    }
    wrStr(fd, " =====\n");
}

void dumpBacktrace(int fd)
{
    bt_accessor_t acc;
    if (bt_init_accessor(&acc, BT_SELF) == -1) {
        wrStr(fd, "(bt_init_accessor failed errno=");
        wrNum(fd, (unsigned long)errno);
        wrStr(fd, ")\n");
        return;
    }

    bt_memmap_t memmap;
    ::memset(&memmap, 0, sizeof(memmap));
    const bool haveMap = (bt_load_memmap(&acc, &memmap) != -1);

    bt_addr_t addrs[48];
    const int cnt = bt_get_backtrace(&acc, addrs,
                                     int(sizeof(addrs) / sizeof(addrs[0])));
    if (cnt > 0) {
        wrStr(fd, "backtrace frames=");
        wrNum(fd, (unsigned long)cnt);
        wrStr(fd, "\n");
        static char text[8192];
        // NDK signature: last arg is a separator STRING (not a size_t*)
        char fmt[] = "%a";
        char sep[] = "\n";
        text[0] = '\0';
        if (haveMap
                && bt_sprnf_addrs(&memmap, addrs, cnt, fmt,
                                  text, sizeof(text), sep) > 0
                && text[0]) {
            wrStr(fd, text);
            wrStr(fd, "\n");
        } else {
            // No memmap (or formatting failed): raw absolute addresses.
            for (int i = 0; i < cnt; ++i) {
                wrHex(fd, (unsigned long)addrs[i]);
                wrStr(fd, "\n");
            }
        }
    } else {
        wrStr(fd, "(bt_get_backtrace failed)\n");
    }

    if (haveMap) {
        // Memory map lets addresses be resolved to lib+offset offline:
        // offset = frame_addr - library_base, then addr2line/objdump on the
        // matching .so / app binary from this exact build.
        static char mapText[8192];
        mapText[0] = '\0';
        if (bt_sprn_memmap(&memmap, mapText, sizeof(mapText)) != -1
                && mapText[0]) {
            wrStr(fd, "memmap:\n");
            wrStr(fd, mapText);
            wrStr(fd, "\n");
        }
        bt_unload_memmap(&memmap);
    }
    bt_release_accessor(&acc);
}

void crashHandler(int sig, siginfo_t *info, void *ctx)
{
    (void)ctx;
    if (g_inCrash) {
        // Crashed again inside the handler: bail without another dump.
        _exit(128 + sig);
    }
    g_inCrash = 1;

    const int fd = openCrashFile();
    if (fd >= 0) {
        const bool haveAddr = info
                && (sig == SIGSEGV || sig == SIGBUS
                    || sig == SIGILL || sig == SIGFPE);
        writeHeader(fd, sigName(sig),
                    haveAddr ? (unsigned long)(uintptr_t)info->si_addr : 0,
                    haveAddr);
        dumpBacktrace(fd);
        ::fsync(fd);
        ::close(fd);
    }

    // Re-raise with the default action so the OS still records the crash
    // (dev-mode dumper can write a .core next to the app logs).
    struct sigaction dfl;
    ::memset(&dfl, 0, sizeof(dfl));
    dfl.sa_handler = SIG_DFL;
    ::sigaction(sig, &dfl, 0);
    ::raise(sig);
}

void terminateHandler()
{
    const int fd = openCrashFile();
    if (fd >= 0) {
        writeHeader(fd, "std::terminate (uncaught C++ exception)", 0, false);
        dumpBacktrace(fd);
        ::fsync(fd);
        ::close(fd);
    }
    // abort() raises SIGABRT; g_inCrash stays 0 here on purpose so the
    // SIGABRT dump still runs if this path itself did not write one.
    ::abort();
}

} // namespace

namespace CrashGuard {

void install()
{
    // Same sandbox dir as ApiClient's UI error log (QDir::homePath()).
    const char *home = ::getenv("HOME");
    if (home && *home
            && ::strlen(home) < sizeof(g_crashPath) - 32) {
        ::strcpy(g_crashPath, home);
        ::strcat(g_crashPath, BRAND_CRASH_FILE);
    } else {
        g_crashPath[0] = '\0';
    }

#if defined(SA_ONSTACK)
    stack_t ss;
    ::memset(&ss, 0, sizeof(ss));
    ss.ss_sp = g_altStack;
    ss.ss_size = sizeof(g_altStack);
    sigaltstack(&ss, 0);
#endif

    struct sigaction sa;
    ::memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = crashHandler;
    // No :: prefix - sigemptyset is a macro on some libcs
    sigemptyset(&sa.sa_mask);
#if defined(SA_ONSTACK)
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
#else
    sa.sa_flags = SA_SIGINFO;
#endif
    const int sigs[] = { SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT };
    for (unsigned i = 0; i < sizeof(sigs) / sizeof(sigs[0]); ++i)
        ::sigaction(sigs[i], &sa, 0);

    std::set_terminate(terminateHandler);
}

} // namespace CrashGuard
