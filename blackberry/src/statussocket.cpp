#include "statussocket.hpp"

#include <QDateTime>
#include <QStringList>
#include <QUrl>

namespace {
const int INITIAL_BACKOFF_MS = 5 * 1000;
const int MAX_BACKOFF_MS = 60 * 1000;
// Larger than anything the daemon sends; a bigger frame means we are
// desynced and must resync via reconnect.
const int MAX_FRAME_PAYLOAD = 256 * 1024;

QByteArray randomBytes(int n)
{
    QByteArray out;
    out.reserve(n);
    for (int i = 0; i < n; ++i)
        out.append(char(qrand() & 0xFF));
    return out;
}
} // namespace

StatusSocket::StatusSocket(QObject *parent)
    : QObject(parent)
    , m_port(0)
    , m_upgraded(false)
    , m_backoffMs(INITIAL_BACKOFF_MS)
{
    qsrand(uint(QDateTime::currentMSecsSinceEpoch() & 0xFFFFFFFF));

    connect(&m_socket, SIGNAL(connected()), this, SLOT(onConnected()));
    connect(&m_socket, SIGNAL(readyRead()), this, SLOT(onReadyRead()));
    connect(&m_socket, SIGNAL(disconnected()), this, SLOT(onClosed()));
    connect(&m_socket, SIGNAL(error(QAbstractSocket::SocketError)),
            this, SLOT(onClosed()));

    m_reconnectTimer.setSingleShot(true);
    connect(&m_reconnectTimer, SIGNAL(timeout()), this, SLOT(openSocket()));
}

void StatusSocket::configure(const QString &baseUrl, const QString &token)
{
    // Raw TCP means http only; an https daemon just loses the push banner
    // (job polling still covers it).
    QUrl url(baseUrl);
    if (url.scheme() == QLatin1String("http") && !url.host().isEmpty()) {
        m_host = url.host();
        m_port = url.port(80);
    } else {
        m_host.clear();
        m_port = 0;
    }
    m_token = token;
    m_reconnectTimer.stop();
    m_backoffMs = INITIAL_BACKOFF_MS;
    m_socket.abort();
    setUp(false);
    m_buffer.clear();
    if (!m_host.isEmpty() && !m_token.isEmpty())
        openSocket();
}

void StatusSocket::openSocket()
{
    if (m_host.isEmpty() || m_token.isEmpty())
        return;
    if (m_socket.state() != QAbstractSocket::UnconnectedState)
        return;
    m_buffer.clear();
    m_upgraded = false;
    m_socket.connectToHost(m_host, quint16(m_port));
}

void StatusSocket::onConnected()
{
    QByteArray key = randomBytes(16).toBase64();
    QByteArray request =
            "GET /ws/status HTTP/1.1\r\n"
            "Host: " + m_host.toUtf8() + ":" + QByteArray::number(m_port) + "\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: " + key + "\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "X-Auth-Token: " + m_token.toUtf8() + "\r\n\r\n";
    m_socket.write(request);
}

void StatusSocket::onReadyRead()
{
    m_buffer += m_socket.readAll();

    if (!m_upgraded) {
        int end = m_buffer.indexOf("\r\n\r\n");
        if (end < 0) {
            if (m_buffer.size() > 16 * 1024)
                m_socket.abort(); // not a websocket handshake
            return;
        }
        QByteArray statusLine = m_buffer.left(m_buffer.indexOf("\r\n"));
        m_buffer.remove(0, end + 4);
        if (!statusLine.contains(" 101 ")) {
            m_socket.abort(); // bad token / old daemon; retry with backoff
            return;
        }
        m_backoffMs = INITIAL_BACKOFF_MS;
        setUp(true);
    }

    parseFrames();
}

void StatusSocket::parseFrames()
{
    for (;;) {
        if (m_buffer.size() < 2)
            return;
        quint8 b0 = quint8(m_buffer.at(0));
        quint8 b1 = quint8(m_buffer.at(1));
        bool masked = (b1 & 0x80) != 0; // servers must not mask; tolerate
        qint64 n = b1 & 0x7F;
        int off = 2;
        if (n == 126) {
            if (m_buffer.size() < 4)
                return;
            n = (quint8(m_buffer.at(2)) << 8) | quint8(m_buffer.at(3));
            off = 4;
        } else if (n == 127) {
            if (m_buffer.size() < 10)
                return;
            n = 0;
            for (int i = 2; i < 10; ++i)
                n = (n << 8) | quint8(m_buffer.at(i));
            off = 10;
        }
        if (n > MAX_FRAME_PAYLOAD) {
            m_socket.abort();
            return;
        }
        QByteArray mask;
        if (masked) {
            if (m_buffer.size() < off + 4)
                return;
            mask = m_buffer.mid(off, 4);
            off += 4;
        }
        if (m_buffer.size() < off + int(n))
            return;
        QByteArray payload = m_buffer.mid(off, int(n));
        m_buffer.remove(0, off + int(n));
        if (masked) {
            for (int i = 0; i < payload.size(); ++i)
                payload[i] = char(payload.at(i) ^ mask.at(i % 4));
        }

        quint8 opcode = b0 & 0x0F;
        if (opcode == 0x1) {           // text
            emit textFrame(payload);
        } else if (opcode == 0x9) {    // ping -> masked pong
            sendFrame(0xA, payload);
        } else if (opcode == 0x8) {    // close
            m_socket.disconnectFromHost();
            return;
        }
        // binary/pong/continuation: ignored (the daemon sends none)
    }
}

void StatusSocket::sendFrame(quint8 opcode, const QByteArray &payload)
{
    if (m_socket.state() != QAbstractSocket::ConnectedState)
        return;
    QByteArray frame;
    frame.append(char(0x80 | opcode));
    int n = payload.size();
    if (n < 126) {
        frame.append(char(0x80 | n));
    } else {
        frame.append(char(0x80 | 126));
        frame.append(char((n >> 8) & 0xFF));
        frame.append(char(n & 0xFF));
    }
    QByteArray mask = randomBytes(4);
    frame.append(mask);
    for (int i = 0; i < n; ++i)
        frame.append(char(payload.at(i) ^ mask.at(i % 4)));
    m_socket.write(frame);
}

void StatusSocket::onClosed()
{
    setUp(false);
    m_buffer.clear();
    scheduleReconnect();
}

void StatusSocket::scheduleReconnect()
{
    if (m_host.isEmpty() || m_token.isEmpty())
        return;
    if (m_reconnectTimer.isActive())
        return;
    m_reconnectTimer.start(m_backoffMs);
    m_backoffMs = qMin(m_backoffMs * 2, MAX_BACKOFF_MS);
}

void StatusSocket::setUp(bool up)
{
    if (m_upgraded == up)
        return;
    m_upgraded = up;
    emit upChanged();
}
