#ifndef SOCKSPROXY_HPP
#define SOCKSPROXY_HPP

#include <QNetworkProxy>
#include <QString>

class QNetworkAccessManager;

// Install a QNetworkProxyFactory so QNetworkAccessManager and QTcpSocket
// tunnel through V2Ray SOCKS5 127.0.0.1:10808 when that port is listening.
// LAN, loopback, and Tailscale/CGNAT stay direct.
void installSocks10808ApplicationProxy();

void installSocks10808Factory(QNetworkAccessManager *nam);
QNetworkProxy socks10808ProxyForHost(const QString &host);

#endif
