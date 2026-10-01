#ifndef TRACE_HPP
#define TRACE_HPP

// Diagnostic trace for "close leaves a grey icon that will not relaunch".
//
// Every line is appended with open/write/close (O_APPEND), so the file is
// complete even if the process freezes or is killed a moment later, and it
// lives in the SHARED folder so it can be read from the File Manager while
// Agent Remote itself refuses to start:
//
//     shared/misc/CyberBerry/AgentRemote-trace.txt
//
// A watchdog thread reports when the MAIN thread stops ticking, naming the
// last thing that was traced before it froze. That is the question the two
// blind fixes could not answer.

void traceInit(const char *version);
void traceMark(const char *fmt, ...);
// Called ~2x/s from a main-thread timer; the watchdog measures the gaps.
void traceHeartbeat();

#endif
