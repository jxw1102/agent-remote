#include "applicationui.hpp"

#include <bb/cascades/AbstractPane>
#include <bb/cascades/Application>
#include <bb/cascades/Label>
#include <bb/cascades/Page>
#include <bb/cascades/QmlDocument>
#include "qmlres.hpp"
#include <bb/cascades/ScrollView>
#include <bb/system/InvokeManager>
#include <bb/system/InvokeRequest>

#include <QDebug>

#include <pthread.h>
#include <unistd.h>

#include "apiclient.hpp"
#include "brand.hpp"

#include <QTimer>
#include <QUrl>

using namespace bb::cascades;

// The declarative engine reports QML load problems through qWarning; capture
// them during startup so a broken build shows its errors on screen instead
// of crashing with no diagnostics.
static QString s_startupLog;
static QtMsgHandler s_prevHandler = 0;

static void captureMessage(QtMsgType type, const char *msg)
{
    s_startupLog += QString::fromLocal8Bit(msg);
    s_startupLog += "\n";
    if (s_prevHandler)
        s_prevHandler(type, msg);
}

// ---- Close must end the process -----------------------------------------
//
// Since the V2Ray change every network path runs through a SOCKS5 proxy
// (socksproxy.cpp), and closing the app stopped ending the process: the
// icon went grey and the OS refused to start a second copy until a reboot.
// Qt's shutdown is where it stalls — the proxied network managers, their
// HTTP thread and the global proxy state are all torn down there — and
// nothing the app needs happens during teardown: every setting is synced
// to disk when it is written. So the process does not wait on teardown:
//   * main() calls _exit() the moment the event loop returns, and
//   * if quitting stalls before exec() even returns, this watchdog ends the
//     process a couple of seconds after aboutToQuit.
// _exit is async-signal-safe and runs no destructors, which is the point.
static void *quitWatchdog(void *)
{
    sleep(2);
    _exit(0);
    return 0;
}

void ApplicationUI::armQuitWatchdog()
{
    pthread_t t;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&t, &attr, quitWatchdog, 0) != 0)
        _exit(0);   // cannot arm it: end now rather than risk the hang
    pthread_attr_destroy(&attr);
}

// ---- agentremote:// voice-command links (bb10-assistant/DEEPLINKS.md) ----
// Any app or web page can open these, so a link only navigates: it opens a
// session's transcript or the sessions list. It never sends input.

// Value of one query parameter: UTF-8 percent-decoded, '+' read as space.
static QString linkQueryValue(const QUrl &url, const QString &key)
{
    const QList<QByteArray> pairs = url.encodedQuery().split('&');
    for (int i = 0; i < pairs.size(); ++i) {
        const QByteArray &pair = pairs.at(i);
        const int eq = pair.indexOf('=');
        QByteArray k = eq < 0 ? pair : pair.left(eq);
        k.replace('+', ' ');
        if (QUrl::fromPercentEncoding(k).compare(key, Qt::CaseInsensitive) != 0)
            continue;
        QByteArray v = eq < 0 ? QByteArray() : pair.mid(eq + 1);
        v.replace('+', ' ');
        return QUrl::fromPercentEncoding(v).trimmed();
    }
    return QString();
}

void ApplicationUI::onInvoked(const bb::system::InvokeRequest &request)
{
    if (request.action() != QLatin1String("bb.action.OPEN"))
        return;
    handleLink(request.uri());
}

void ApplicationUI::handleLink(const QUrl &uri)
{
    if (uri.scheme().compare(QLatin1String("agentremote"), Qt::CaseInsensitive) != 0)
        return;
    QString verb = uri.host();
    if (verb.isEmpty()) {
        verb = uri.path();
        while (verb.startsWith(QLatin1Char('/')))
            verb.remove(0, 1);
    }
    verb = verb.section(QLatin1Char('/'), 0, 0).trimmed().toLower();

    // session without a name, or an unknown link: the sessions list.
    m_pendingSession = (verb == QLatin1String("session"))
            ? linkQueryValue(uri, QLatin1String("name")) : QString();
    qDebug() << "link: verb=" << verb << "name=" << m_pendingSession;
    ++m_linkRev;
    // The sessions list fans out over every profile and can take a while on
    // a cold start; give up matching after that rather than yank the user
    // into a session much later.
    if (!m_pendingSession.isEmpty())
        m_linkExpiry->start();
    emit linkChanged();
}

void ApplicationUI::clearPendingSession()
{
    m_linkExpiry->stop();
    if (m_pendingSession.isEmpty())
        return;
    m_pendingSession.clear();
    emit linkChanged();
}

ApplicationUI::ApplicationUI()
    : QObject(Application::instance())
    , m_api(new ApiClient(this))
    , m_invoke(new bb::system::InvokeManager(this))
    , m_linkExpiry(new QTimer(this))
    , m_linkRev(0)
{
    // Connect before the event loop runs: a launch-by-link delivers invoked()
    // as soon as it spins.
    connect(m_invoke, SIGNAL(invoked(const bb::system::InvokeRequest&)),
            this, SLOT(onInvoked(const bb::system::InvokeRequest&)));
    m_linkExpiry->setSingleShot(true);
    m_linkExpiry->setInterval(15000);
    connect(m_linkExpiry, SIGNAL(timeout()), this, SLOT(clearPendingSession()));

    // Refresh the sessions list whenever the app returns to the foreground
    // - the daemon keeps working while the app is thumbnailed.
    connect(Application::instance(), SIGNAL(fullscreen()),
            m_api, SLOT(refreshSessions()));
    connect(Application::instance(), SIGNAL(aboutToQuit()),
            this, SLOT(armQuitWatchdog()));

    s_prevHandler = qInstallMsgHandler(captureMessage);

    // qrc first (see qml.qrc); assets/ if that copy will not build.
    AbstractPane *root = 0;
    QmlDocument *qml = 0;
    int qmlPass = -1;
    for (int pass = 0; pass < 2 && !root; ++pass) {
        if (qml) {
            qml->setParent(0);
            delete qml;
            qml = 0;
        }
        qml = QmlDocument::create(qmlres::urlForPass(
            QLatin1String("asset:///main.qml"), pass));
        if (!qml)
            continue;
        if (pass == 0 && qml->hasErrors())
            continue;
        qml->setParent(this);
        qml->setContextProperty("_api", m_api);
        qml->setContextProperty("_link", this);
        if (!qml->hasErrors()) {
            root = qml->createRootObject<AbstractPane>();
            if (root)
                qmlPass = pass;
        }
    }
    qmlres::noteScene(qmlPass);

    qInstallMsgHandler(s_prevHandler);

    if (root) {
        Application::instance()->setScene(root);
        return;
    }

    QString errorText = s_startupLog.isEmpty()
            ? QString("main.qml failed to load and no error output was captured.")
            : s_startupLog;
    Label *label = Label::create()
            .text(QString("%1 failed to start\n\n%2")
                          .arg(QLatin1String(BRAND_APP_NAME), errorText))
            .multiline(true);
    Page *page = Page::create().content(ScrollView::create(label));
    Application::instance()->setScene(page);
}
