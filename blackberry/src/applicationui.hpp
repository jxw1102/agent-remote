#ifndef APPLICATIONUI_HPP
#define APPLICATIONUI_HPP

#include <QObject>
#include <QString>

class ApiClient;
class QTimer;
class QUrl;
namespace bb { namespace system { class InvokeManager; class InvokeRequest; } }

class ApplicationUI : public QObject
{
    Q_OBJECT
    // agentremote://session?name=<name> (bb10-assistant/DEEPLINKS.md) waiting
    // for the sessions list: QML opens the best-matching session, or stays on
    // the list. Cleared by QML, or after a few seconds if nothing matched.
    Q_PROPERTY(QString pendingSessionName READ pendingSessionName NOTIFY linkChanged)
    // Bumped on every agentremote:// link, even unknown ones.
    Q_PROPERTY(int linkRev READ linkRev NOTIFY linkChanged)
public:
    ApplicationUI();
    virtual ~ApplicationUI() {}

    QString pendingSessionName() const { return m_pendingSession; }
    int linkRev() const { return m_linkRev; }
    Q_INVOKABLE void clearPendingSession();

Q_SIGNALS:
    void linkChanged();

private Q_SLOTS:
    // Close must END the process. See applicationui.cpp.
    void armQuitWatchdog();
    void onInvoked(const bb::system::InvokeRequest &request);

private:
    void handleLink(const QUrl &uri);

    ApiClient *m_api;
    bb::system::InvokeManager *m_invoke;
    QTimer *m_linkExpiry;
    QString m_pendingSession;
    int m_linkRev;
};

#endif // APPLICATIONUI_HPP
