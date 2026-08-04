#include "statussse.hpp"

#include <QNetworkRequest>
#include <QUrl>

namespace {
const int INITIAL_BACKOFF_MS = 5 * 1000;
const int MAX_BACKOFF_MS = 60 * 1000;
} // namespace

StatusSse::StatusSse(QObject *parent)
    : QObject(parent)
    , m_reply(0)
    , m_up(false)
    , m_backoffMs(INITIAL_BACKOFF_MS)
{
    m_reconnectTimer.setSingleShot(true);
    connect(&m_reconnectTimer, SIGNAL(timeout()), this, SLOT(openRequest()));
}

void StatusSse::configure(const QString &baseUrl, const QString &token)
{
    m_reconnectTimer.stop();
    m_backoffMs = INITIAL_BACKOFF_MS;
    if (m_reply) {
        m_reply->abort();
        m_reply->deleteLater();
        m_reply = 0;
    }
    setUp(false);
    m_buffer.clear();
    m_eventData.clear();

    m_baseUrl = baseUrl;
    m_token = token;

    if (!m_baseUrl.isEmpty() && !m_token.isEmpty())
        openRequest();
}

void StatusSse::openRequest()
{
    if (m_baseUrl.isEmpty() || m_token.isEmpty())
        return;
    if (m_reply)
        return;

    QUrl url(m_baseUrl);
    // Prefer a clean absolute path: empty or "/" + "/sse/status" must not
    // become "//sse/status" (some reverse proxies 404 that).
    QString p = url.path();
    if (p.isEmpty() || p == QLatin1String("/"))
        url.setPath(QLatin1String("/sse/status"));
    else if (p.endsWith(QLatin1Char('/')))
        url.setPath(p + QLatin1String("sse/status"));
    else
        url.setPath(p + QLatin1String("/sse/status"));

    QNetworkRequest req(url);
    req.setRawHeader("Accept", "text/event-stream");
    req.setRawHeader("X-Auth-Token", m_token.toUtf8());
    // Prevent Qt from buffering the entire response before signalling.
    req.setAttribute(QNetworkRequest::HttpPipeliningAllowedAttribute, false);

    m_reply = m_nam.get(req);
    connect(m_reply, SIGNAL(readyRead()), this, SLOT(onReadyRead()));
    connect(m_reply, SIGNAL(finished()), this, SLOT(onFinished()));
}

void StatusSse::onReadyRead()
{
    if (!m_reply)
        return;

    if (!m_up) {
        int status = m_reply->attribute(
                QNetworkRequest::HttpStatusCodeAttribute).toInt();
        if (status == 200) {
            m_backoffMs = INITIAL_BACKOFF_MS;
            setUp(true);
        }
    }

    m_buffer += m_reply->readAll();
    parseLines();
}

void StatusSse::parseLines()
{
    // SSE protocol: lines are separated by \n (or \r\n).
    // "data: <payload>\n" accumulates into m_eventData.
    // An empty line dispatches the accumulated event.
    for (;;) {
        int nl = m_buffer.indexOf('\n');
        if (nl < 0)
            return;
        QByteArray line = m_buffer.left(nl);
        m_buffer.remove(0, nl + 1);
        // Strip trailing \r if present.
        if (!line.isEmpty() && line.at(line.size() - 1) == '\r')
            line.chop(1);

        if (line.isEmpty()) {
            // Empty line = end of event; dispatch if we have data.
            if (!m_eventData.isEmpty()) {
                emit textFrame(m_eventData);
                m_eventData.clear();
            }
        } else if (line.startsWith("data:")) {
            // "data:" with optional space after the colon.
            QByteArray value = line.mid(5);
            if (!value.isEmpty() && value.at(0) == ' ')
                value = value.mid(1);
            if (!m_eventData.isEmpty())
                m_eventData.append('\n');
            m_eventData.append(value);
        }
        // Ignore "event:", "id:", "retry:", and comment lines (starting
        // with ':') - the daemon doesn't use them for the status stream.
    }
}

void StatusSse::onFinished()
{
    if (!m_reply)
        return;
    m_reply->deleteLater();
    m_reply = 0;
    setUp(false);
    m_buffer.clear();
    m_eventData.clear();
    scheduleReconnect();
}

void StatusSse::scheduleReconnect()
{
    if (m_baseUrl.isEmpty() || m_token.isEmpty())
        return;
    if (m_reconnectTimer.isActive())
        return;
    m_reconnectTimer.start(m_backoffMs);
    m_backoffMs = qMin(m_backoffMs * 2, MAX_BACKOFF_MS);
}

void StatusSse::setUp(bool up)
{
    if (m_up == up)
        return;
    m_up = up;
    emit upChanged();
}
