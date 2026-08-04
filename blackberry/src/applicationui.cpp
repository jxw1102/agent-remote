#include "applicationui.hpp"

#include <bb/cascades/AbstractPane>
#include <bb/cascades/Application>
#include <bb/cascades/Label>
#include <bb/cascades/Page>
#include <bb/cascades/QmlDocument>
#include <bb/cascades/ScrollView>

#include <QDebug>

#include "apiclient.hpp"
#include "brand.hpp"

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

ApplicationUI::ApplicationUI()
    : QObject(Application::instance())
    , m_api(new ApiClient(this))
{
    // Refresh the sessions list whenever the app returns to the foreground
    // - the daemon keeps working while the app is thumbnailed.
    connect(Application::instance(), SIGNAL(fullscreen()),
            m_api, SLOT(refreshSessions()));

    s_prevHandler = qInstallMsgHandler(captureMessage);

    QmlDocument *qml = QmlDocument::create("asset:///main.qml").parent(this);
    qml->setContextProperty("_api", m_api);

    AbstractPane *root = 0;
    if (!qml->hasErrors())
        root = qml->createRootObject<AbstractPane>();

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
