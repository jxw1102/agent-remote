#ifndef CRASHGUARD_HPP_
#define CRASHGUARD_HPP_

/**
 * Fatal-signal + std::terminate handler for BB10/QNX (from GrokRemote).
 *
 * On SIGSEGV/SIGBUS/SIGILL/SIGFPE/SIGABRT (and uncaught C++ exceptions) it
 * appends signal, fault address, a libbacktrace stack trace, and the process
 * memory map to $HOME + BRAND_CRASH_FILE using only async-signal-safe calls,
 * then re-raises the signal so the OS still sees the crash.
 *
 * ApiClient reads and rotates that file on next launch and shows it in
 * Settings -> Crash / error log.
 *
 * Note: an out-of-memory kill is SIGKILL and cannot be caught. If the app
 * "crashed" but this file stays empty, suspect OOM (check memory use).
 */
namespace CrashGuard
{
/** Install handlers. Call first thing in main(), before Application. */
void install();
}

#endif
