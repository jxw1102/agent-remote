#include "socks10808.h"

#include <sys/types.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <time.h>

#define SOCKS_HOST "127.0.0.1"
#define SOCKS_PORT 10808
#define PROBE_MS 750
#define CONNECT_MS 15000
#define CACHE_POS_SEC 8
#define CACHE_NEG_SEC 2

static int g_cached = -1; /* -1 unknown, 0 down, 1 up */
static time_t g_cached_at = 0;
static int g_force_direct = 0;

void socks10808_set_force_direct(int on)
{
    g_force_direct = on ? 1 : 0;
}

static void set_err(char *err, int errlen, const char *msg)
{
    if (!err || errlen <= 0)
        return;
    strncpy(err, msg, (size_t)(errlen - 1));
    err[errlen - 1] = '\0';
}

static int parse_ipv4(const char *h, unsigned int *out)
{
    unsigned int a, b, c, d;
    char extra;
    if (!h || !out)
        return 0;
    if (sscanf(h, "%u.%u.%u.%u%c", &a, &b, &c, &d, &extra) != 4)
        return 0;
    if (a > 255 || b > 255 || c > 255 || d > 255)
        return 0;
    *out = (a << 24) | (b << 16) | (c << 8) | d;
    return 1;
}

int socks10808_host_is_direct(const char *host)
{
    unsigned int ip;
    size_t n;

    if (!host || !host[0])
        return 1;
    if (strcmp(host, "localhost") == 0)
        return 1;

    n = strlen(host);
    if (n >= 6 && strcmp(host + n - 6, ".local") == 0)
        return 1;

    if (!parse_ipv4(host, &ip))
        return 0;

    /* 127.0.0.0/8 */
    if ((ip & 0xFF000000u) == 0x7F000000u)
        return 1;
    /* 10.0.0.0/8 */
    if ((ip & 0xFF000000u) == 0x0A000000u)
        return 1;
    /* 172.16.0.0/12 */
    if ((ip & 0xFFF00000u) == 0xAC100000u)
        return 1;
    /* 192.168.0.0/16 */
    if ((ip & 0xFFFF0000u) == 0xC0A80000u)
        return 1;
    /* 169.254.0.0/16 */
    if ((ip & 0xFFFF0000u) == 0xA9FE0000u)
        return 1;
    /* 100.64.0.0/10  CGNAT / Tailscale — stay on the overlay, not V2Ray */
    if ((ip & 0xFFC00000u) == 0x64400000u)
        return 1;
    return 0;
}

static void apply_rw_timeout(int s)
{
    struct timeval tv;
    tv.tv_sec = 120;
    tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

static int timed_connect_addr(const struct sockaddr *sa, socklen_t slen,
                              int timeout_ms)
{
    int s, flags, rc;

    s = socket(sa->sa_family, SOCK_STREAM, 0);
    if (s < 0)
        return -1;
    flags = fcntl(s, F_GETFL, 0);
    fcntl(s, F_SETFL, flags | O_NONBLOCK);
    rc = connect(s, sa, slen);
    if (rc < 0 && errno == EINPROGRESS) {
        fd_set wf;
        struct timeval tv;
        FD_ZERO(&wf);
        FD_SET(s, &wf);
        tv.tv_sec = timeout_ms / 1000;
        tv.tv_usec = (timeout_ms % 1000) * 1000;
        rc = select(s + 1, 0, &wf, 0, &tv);
        if (rc > 0) {
            int soerr = 0;
            socklen_t sl = sizeof(soerr);
            getsockopt(s, SOL_SOCKET, SO_ERROR, &soerr, &sl);
            rc = soerr ? -1 : 0;
        } else {
            rc = -1;
        }
    }
    fcntl(s, F_SETFL, flags);
    if (rc == 0) {
        apply_rw_timeout(s);
        return s;
    }
    close(s);
    return -1;
}

static int timed_connect_ipv4(const char *ip, int port, int timeout_ms)
{
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1)
        return -1;
    return timed_connect_addr((struct sockaddr *)&addr, sizeof(addr),
                              timeout_ms);
}

static int timed_connect_host(const char *host, int port, int timeout_ms,
                              char *err, int errlen)
{
    char portStr[16];
    struct addrinfo hints;
    struct addrinfo *res = 0;
    struct addrinfo *rp;
    int fd = -1;

    snprintf(portStr, sizeof(portStr), "%d", port);
    memset(&hints, 0, sizeof(hints));
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_INET;
    if (getaddrinfo(host, portStr, &hints, &res) != 0 || !res) {
        set_err(err, errlen, "DNS failed");
        return -1;
    }
    for (rp = res; rp; rp = rp->ai_next) {
        fd = timed_connect_addr(rp->ai_addr, rp->ai_addrlen, timeout_ms);
        if (fd >= 0)
            break;
    }
    freeaddrinfo(res);
    if (fd < 0)
        set_err(err, errlen, "TCP connect failed");
    return fd;
}

static int proxy_is_up(void)
{
    time_t now = time(0);
    int fd;
    int ttl;

    if (g_cached >= 0) {
        ttl = g_cached ? CACHE_POS_SEC : CACHE_NEG_SEC;
        if ((now - g_cached_at) < ttl)
            return g_cached;
    }
    fd = timed_connect_ipv4(SOCKS_HOST, SOCKS_PORT, PROBE_MS);
    if (fd >= 0) {
        close(fd);
        g_cached = 1;
    } else {
        g_cached = 0;
    }
    g_cached_at = now;
    return g_cached;
}

int socks10808_is_up(void)
{
    return proxy_is_up();
}

static int send_all(int fd, const char *p, int n)
{
    int off = 0;
    while (off < n) {
        int w = (int)send(fd, p + off, (size_t)(n - off), 0);
        if (w <= 0)
            return 0;
        off += w;
    }
    return 1;
}

static int recv_all(int fd, char *p, int n)
{
    int off = 0;
    while (off < n) {
        int r = (int)recv(fd, p + off, (size_t)(n - off), 0);
        if (r <= 0)
            return 0;
        off += r;
    }
    return 1;
}

static int socks5_connect(int fd, const char *host, int port,
                          char *err, int errlen)
{
    unsigned char greet[3];
    unsigned char sel[2];
    unsigned char req[7 + 256];
    unsigned char hd[4];
    unsigned char skip[272];
    unsigned int ip;
    int hlen;
    int off;
    int addrLen;
    unsigned char atyp;

    greet[0] = 0x05;
    greet[1] = 0x01;
    greet[2] = 0x00;
    if (!send_all(fd, (char *)greet, 3)) {
        set_err(err, errlen, "SOCKS5: greeting send failed");
        return 0;
    }
    if (!recv_all(fd, (char *)sel, 2) || sel[0] != 0x05) {
        set_err(err, errlen, "SOCKS5: bad greeting reply");
        return 0;
    }
    if (sel[1] == 0xFF) {
        set_err(err, errlen, "SOCKS5: no acceptable auth method");
        return 0;
    }
    if (sel[1] != 0x00) {
        set_err(err, errlen, "SOCKS5: unsupported auth method");
        return 0;
    }

    req[0] = 0x05;
    req[1] = 0x01; /* CONNECT */
    req[2] = 0x00;
    if (parse_ipv4(host, &ip)) {
        req[3] = 0x01;
        req[4] = (unsigned char)((ip >> 24) & 0xFF);
        req[5] = (unsigned char)((ip >> 16) & 0xFF);
        req[6] = (unsigned char)((ip >> 8) & 0xFF);
        req[7] = (unsigned char)(ip & 0xFF);
        req[8] = (unsigned char)((port >> 8) & 0xFF);
        req[9] = (unsigned char)(port & 0xFF);
        off = 10;
    } else {
        hlen = (int)strlen(host);
        if (hlen > 255)
            hlen = 255;
        req[3] = 0x03; /* domain */
        req[4] = (unsigned char)hlen;
        memcpy(req + 5, host, (size_t)hlen);
        req[5 + hlen] = (unsigned char)((port >> 8) & 0xFF);
        req[6 + hlen] = (unsigned char)(port & 0xFF);
        off = 7 + hlen;
    }
    if (!send_all(fd, (char *)req, off)) {
        set_err(err, errlen, "SOCKS5: connect send failed");
        return 0;
    }
    if (!recv_all(fd, (char *)hd, 4) || hd[0] != 0x05) {
        set_err(err, errlen, "SOCKS5: bad connect reply");
        return 0;
    }
    if (hd[1] != 0x00) {
        char buf[72];
        snprintf(buf, sizeof(buf),
                 "SOCKS5: proxy refused connection (code %u)",
                 (unsigned)hd[1]);
        set_err(err, errlen, buf);
        return 0;
    }
    atyp = hd[3];
    if (atyp == 0x01)
        addrLen = 4;
    else if (atyp == 0x04)
        addrLen = 16;
    else if (atyp == 0x03) {
        unsigned char l = 0;
        if (!recv_all(fd, (char *)&l, 1)) {
            set_err(err, errlen, "SOCKS5: short reply");
            return 0;
        }
        addrLen = (int)l;
    } else {
        set_err(err, errlen, "SOCKS5: unknown reply address type");
        return 0;
    }
    if (addrLen + 2 > (int)sizeof(skip)
            || !recv_all(fd, (char *)skip, addrLen + 2)) {
        set_err(err, errlen, "SOCKS5: short bind address");
        return 0;
    }
    return 1;
}

int socks10808_tcp_connect(const char *host, int port,
                           char *errbuf, int errbuf_len)
{
    return socks10808_tcp_connect_timeout(host, port, CONNECT_MS,
                                          errbuf, errbuf_len);
}

int socks10808_tcp_connect_timeout(const char *host, int port, int timeout_ms,
                                   char *errbuf, int errbuf_len)
{
    int fd;

    if (timeout_ms <= 0)
        timeout_ms = CONNECT_MS;
    if (!host || !host[0] || port <= 0 || port > 65535) {
        set_err(errbuf, errbuf_len, "invalid host/port");
        return -1;
    }

    if (g_force_direct || socks10808_host_is_direct(host) || !proxy_is_up())
        return timed_connect_host(host, port, timeout_ms, errbuf, errbuf_len);

    fd = timed_connect_ipv4(SOCKS_HOST, SOCKS_PORT, timeout_ms);
    if (fd < 0) {
        g_cached = 0;
        g_cached_at = time(0);
        return timed_connect_host(host, port, timeout_ms, errbuf, errbuf_len);
    }
    if (!socks5_connect(fd, host, port, errbuf, errbuf_len)) {
        close(fd);
        return -1;
    }
    if (errbuf && errbuf_len > 0)
        errbuf[0] = '\0';
    return fd;
}
