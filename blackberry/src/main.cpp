#include <bb/cascades/Application>
#include <bb/cascades/Color>
#include <bb/cascades/ThemeSupport>
#include <bb/cascades/VisualStyle>

#include <QColor>
#include <QTextCodec>

#include <unistd.h>

#include "applicationui.hpp"
#include "brand.hpp"
#include "crashguard.hpp"
#include "socksproxy.hpp"
#include "trace.hpp"

using namespace bb::cascades;

Q_DECL_EXPORT int main(int argc, char **argv)
{
    // Before anything else: fatal signals dump a backtrace to the sandbox
    // (shown in Settings -> Crash / error log on the next launch).
    CrashGuard::install();
    traceInit("3.2.6");

    Application app(argc, argv);
    traceMark("Application constructed");

    // QNetworkAccessManager + QTcpSocket: V2Ray SOCKS5 127.0.0.1:10808 when up.
    installSocks10808ApplicationProxy();
    traceMark("app-wide SOCKS proxy factory installed");

    // The sources are UTF-8, but Qt 4.8 decodes plain char* literals and
    // tr() through Latin-1 by default — a source "·" (bytes C2 B7) rendered
    // as "Â·" on the status banner and the Usage sheet. Declare reality once
    // instead of wrapping every literal. (Qt 5 made this the default.)
    QTextCodec *utf8 = QTextCodec::codecForName("UTF-8");
    QTextCodec::setCodecForCStrings(utf8);
    QTextCodec::setCodecForTr(utf8);

    // Belt and suspenders with CASCADES_THEME=dark in the bar-descriptor:
    // force dark chrome at process start (GrokRemote pattern).
    if (app.themeSupport()) {
        app.themeSupport()->setVisualStyle(VisualStyle::Dark);
        // Recolor the OS accent (title-bar separator, indicators, ...) to the
        // brand color so there's a single brand line under the title bar,
        // not the default blue plus a drawn one.
        QColor a(QString::fromLatin1(BRAND_ACCENT_COLOR));
        if (a.isValid()) {
            const Color brand =
                Color::fromRGBA(a.redF(), a.greenF(), a.blueF(), 1.0f);
            // Pass the brand color as BOTH primary and primaryBase. The text
            // caret / selection follows primaryBase; leaving it default (the
            // framework auto-derives one) kept the cursor cyan. Same color for
            // both makes the caret the brand accent.
            app.themeSupport()->setPrimaryColor(brand, brand);
        }
    }

    ApplicationUI appui;
    Q_UNUSED(appui);
    traceMark("entering event loop");
    const int rc = Application::exec();
    traceMark("event loop returned rc=%d; _exit now", rc);
    // Skip teardown on purpose: it is where the proxied network stack
    // stalled, leaving a grey icon that would not relaunch (see
    // ApplicationUI::armQuitWatchdog). Settings are already on disk.
    _exit(rc);
    return rc;
}
