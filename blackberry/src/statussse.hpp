#ifndef STATUSSSE_HPP
#define STATUSSSE_HPP

#include <QByteArray>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QObject>
#include <QString>
#include <QTimer>

/*!
 * SSE (Server-Sent Events) client for the daemon's /sse/status stream.
 *
 * Same public interface as StatusSocket so ApiClient can swap between them
 * with a compile-time toggle.  Unlike the WebSocket variant this works over
 * HTTPS because it rides on QNetworkAccessManager.
 */
class StatusSse : public QObject
{
    Q_OBJECT

public:
    explicit StatusSse(QObject *parent = 0);

    void configure(const QString &baseUrl, const QString &token);
    bool isUp() const { return m_up; }

Q_SIGNALS:
    void textFrame(const QByteArray &payload);
    void upChanged();

private Q_SLOTS:
    void onReadyRead();
    void onFinished();
    void openRequest();

private:
    void scheduleReconnect();
    void setUp(bool up);
    void parseLines();

    QNetworkAccessManager m_nam;
    QTimer m_reconnectTimer;
    QString m_baseUrl;
    QString m_token;
    QNetworkReply *m_reply;
    QByteArray m_buffer;
    QByteArray m_eventData;
    bool m_up;
    int m_backoffMs;
};

#endif // STATUSSSE_HPP
