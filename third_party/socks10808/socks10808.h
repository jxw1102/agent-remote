#ifndef SOCKS10808_H
#define SOCKS10808_H

#ifdef __cplusplus
extern "C" {
#endif

/*
 * TCP connect that auto-tunnels through the local V2Ray SOCKS5 listener
 * at 127.0.0.1:10808 when that port is up. No auth, no per-app settings.
 *
 * Loopback, RFC1918, link-local, localhost and *.local stay direct so LAN
 * and the proxy itself cannot loop. If 10808 is closed, connect is direct.
 *
 * Returns a connected socket fd, or -1 with a message in errbuf.
 */
int socks10808_tcp_connect(const char *host, int port,
                           char *errbuf, int errbuf_len);

/* The same, with the connect deadline in milliseconds instead of the built-in
 * 15 s. For a caller that has somewhere else to try: a media CDN names three
 * hosts, and waiting fifteen seconds on each of the first two before reaching
 * the one that works is worse than not having the fallback. <= 0 means the
 * default. */
int socks10808_tcp_connect_timeout(const char *host, int port, int timeout_ms,
                                   char *errbuf, int errbuf_len);

/* 1 if 127.0.0.1:10808 accepted a TCP connect recently. */
int socks10808_is_up(void);

/* When non-zero, this process never tunnels, even if 10808 is up.
 * Default is zero. Call before the first connect. */
void socks10808_set_force_direct(int on);

/* 1 if host should never go through the proxy. */
int socks10808_host_is_direct(const char *host);

#ifdef __cplusplus
}
#endif

#endif /* SOCKS10808_H */
