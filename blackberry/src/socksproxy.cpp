#include "socksproxy.hpp"

#include "socks10808.h"
#include "trace.hpp"

#include <time.h>

#include <QList>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkProxyFactory>
#include <QNetworkProxyQuery>
#include <QString>

namespace {

QNetworkProxy makeSocks()
{
    QNetworkProxy p(QNetworkProxy::Socks5Proxy,
                    QLatin1String("127.0.0.1"), 10808);
    p.setCapabilities(QNetworkProxy::TunnelingCapability
                      | QNetworkProxy::HostNameLookupCapability);
    return p;
}

long long monoMs()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

bool hostGoesDirect(const QString &host)
{
    if (host.isEmpty())
        return true;
    if (socks10808_host_is_direct(host.toUtf8().constData()) != 0)
        return true;
    const long long t0 = monoMs();
    const int up = socks10808_is_up();
    const long long ms = monoMs() - t0;
    // Trace the probe only when it cost something or the answer flipped.
    static int s_lastUp = -2;
    if (ms > 50 || up != s_lastUp) {
        traceMark("proxy %s: v2ray %s (probe %lld ms)", qPrintable(host),
                  up ? "UP -> socks" : "down -> direct", ms);
        s_lastUp = up;
    }
    return up == 0;
}

class Socks10808Factory : public QNetworkProxyFactory
{
public:
    QList<QNetworkProxy> queryProxy(const QNetworkProxyQuery &query)
    {
        QList<QNetworkProxy> out;
        if (hostGoesDirect(query.peerHostName()))
            out << QNetworkProxy(QNetworkProxy::NoProxy);
        else
            out << makeSocks();
        return out;
    }
};

} // namespace

// Every install gets its OWN heap factory. Both setApplicationProxyFactory()
// and QNetworkAccessManager::setProxyFactory() take ownership and `delete`
// the factory at shutdown (the global one when the app exits, a manager's
// when the manager is destroyed). This used to hand all three owners the
// address of one function-local static, so closing the app deleted a
// non-heap object up to three times. That invalid free lands inside the
// allocator during exit; the crash guard then tries to write a backtrace,
// which needs the same allocator lock, and waits forever — the process
// never finishes exiting, so the icon stays grey and the OS will not start
// a second copy. One `new` per owner means each is deleted exactly once.
void installSocks10808ApplicationProxy()
{
#ifdef AR_NO_SOCKS
    traceMark("A/B build: SOCKS proxy compiled OUT, all traffic direct");
    return;
#endif
    QNetworkProxyFactory::setApplicationProxyFactory(new Socks10808Factory);
}

void installSocks10808Factory(QNetworkAccessManager *nam)
{
#ifdef AR_NO_SOCKS
    Q_UNUSED(nam);
    return;
#endif
    if (!nam)
        return;
    nam->setProxyFactory(new Socks10808Factory);
}

QNetworkProxy socks10808ProxyForHost(const QString &host)
{
#ifdef AR_NO_SOCKS
    Q_UNUSED(host);
    return QNetworkProxy(QNetworkProxy::NoProxy);
#endif
    if (hostGoesDirect(host))
        return QNetworkProxy(QNetworkProxy::NoProxy);
    return makeSocks();
}
