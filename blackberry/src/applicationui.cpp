#include "applicationui.hpp"

#include <bb/cascades/AbstractPane>
#include <bb/cascades/Application>
#include <bb/cascades/Label>
#include <bb/cascades/Page>
#include <bb/cascades/QmlDocument>
#include "qmlres.hpp"
#include <bb/cascades/ScrollView>

#include <QDebug>

#include <pthread.h>
#include <unistd.h>

#include "apiclient.hpp"
#include "brand.hpp"
#include "trace.hpp"

#include <QTimer>

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

void ApplicationUI::heartbeat() { traceHeartbeat(); }
void ApplicationUI::onFullscreen() { traceMark("app: fullscreen"); }
void ApplicationUI::onThumbnail() { traceMark("app: thumbnail"); }
void ApplicationUI::onInvisible() { traceMark("app: invisible"); }

void ApplicationUI::armQuitWatchdog()
{
    traceMark("app: aboutToQuit (close requested); arming 2 s exit watchdog");
    pthread_t t;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&t, &attr, quitWatchdog, 0) != 0)
        _exit(0);   // cannot arm it: end now rather than risk the hang
    pthread_attr_destroy(&attr);
}

ApplicationUI::ApplicationUI()
    : QObject(Application::instance())
    , m_api(new ApiClient(this))
{
    // Refresh the sessions list whenever the app returns to the foreground
    // - the daemon keeps working while the app is thumbnailed.
    connect(Application::instance(), SIGNAL(fullscreen()),
            m_api, SLOT(refreshSessions()));
    connect(Application::instance(), SIGNAL(aboutToQuit()),
            this, SLOT(armQuitWatchdog()));
    connect(Application::instance(), SIGNAL(fullscreen()),
            this, SLOT(onFullscreen()));
    connect(Application::instance(), SIGNAL(thumbnail()),
            this, SLOT(onThumbnail()));
    connect(Application::instance(), SIGNAL(invisible()),
            this, SLOT(onInvisible()));
    QTimer *beat = new QTimer(this);
    beat->setInterval(500);
    connect(beat, SIGNAL(timeout()), this, SLOT(heartbeat()));
    beat->start();
    traceMark("ApplicationUI: signals + heartbeat wired");

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
