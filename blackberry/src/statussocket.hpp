#ifndef STATUSSOCKET_HPP
#define STATUSSOCKET_HPP

#include <QByteArray>
#include <QObject>
#include <QString>
#include <QTcpSocket>
#include <QTimer>

/*!
 * Minimal RFC 6455 WebSocket client for the daemon's /ws/status stream.
 *
 * Qt 4.8 has no QWebSocket (it arrived in Qt 5.3), so this hand-rolls the
 * upgrade handshake and frame parsing over QTcpSocket. Only what the
 * status banner needs: server->client text frames, replying to pings,
 * and auto-reconnect with backoff. Client frames are masked as the RFC
 * requires.
 */
class StatusSocket : public QObject
{
    Q_OBJECT

public:
    explicit StatusSocket(QObject *parent = 0);

    // (Re)connect to <baseUrl>/ws/status. Empty url/token stops. Only
    // plain http base URLs are supported (this is a raw QTcpSocket; over
    // https the app just falls back to job polling for the banner).
    void configure(const QString &baseUrl, const QString &token);
    bool isUp() const { return m_upgraded; }

Q_SIGNALS:
    // One complete text frame (UTF-8 JSON payload from the daemon).
    void textFrame(const QByteArray &payload);
    void upChanged();

private Q_SLOTS:
    void onConnected();
    void onReadyRead();
    void onClosed();
    void openSocket();

private:
    void scheduleReconnect();
    void setUp(bool up);
    void sendFrame(quint8 opcode, const QByteArray &payload);
    void parseFrames();

    QTcpSocket m_socket;
    QTimer m_reconnectTimer;
    QString m_host;
    int m_port;
    QString m_token;
    QByteArray m_buffer;
    bool m_upgraded;
    int m_backoffMs;
};

#endif // STATUSSOCKET_HPP
