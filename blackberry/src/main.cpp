#include <bb/cascades/Application>
#include <bb/cascades/Color>
#include <bb/cascades/ThemeSupport>
#include <bb/cascades/VisualStyle>

#include <QColor>

#include "applicationui.hpp"
#include "brand.hpp"
#include "crashguard.hpp"

using namespace bb::cascades;

Q_DECL_EXPORT int main(int argc, char **argv)
{
    // Before anything else: fatal signals dump a backtrace to the sandbox
    // (shown in Settings -> Crash / error log on the next launch).
    CrashGuard::install();

    Application app(argc, argv);

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
    return Application::exec();
}
