#ifndef RICHPAINT_HPP_
#define RICHPAINT_HPP_

#include <QObject>
#include <QString>
#include <QVariantMap>

struct RichPaintPrivate;

/**
 * Rich text -> PNG rasterizer for the "paint" chat render mode.
 *
 * This is the working replacement for the QTextDocument paint approach that
 * RichProbe showed crashing at the layout step (D1/E1): Qt 4.8's whole text
 * stack (QTextDocument / QFontMetrics / QFontDatabase) needs a QApplication
 * with an initialized GUI font database, and a Cascades app is
 * QCoreApplication-based - so QTextDocument layout dies every time. That is
 * a platform wall, not a fixable bug.
 *
 * RichPaint therefore never touches Qt's font stack. It rasterizes with
 * stb_truetype (vendored, public domain) using BB10's own system fonts from
 * /usr/fonts/font_repository (SlatePro + Monotype CJK), lays out and word-
 * wraps itself, blits into a QImage (raster only - safe without
 * QApplication) and saves a cached PNG that QML shows in a plain ImageView
 * (RichProbe: ImageView + PNG is the safe half of the approach).
 *
 * Supported markup (what ApiClient::normalizeBlock emits):
 *   <font color="#rrggbb">, </font>, <b>, </b>, <i>, </i>,
 *   <a href=...> (rendered in link color), <br/>, &amp; &lt; &gt; &quot;
 *   &#39; &nbsp; &#NN; &#xHH;. Unknown tags are skipped, text kept.
 *
 * All failures are soft: render() returns ok=false and the caller falls
 * back to the Label-based path.
 */
class RichPaint : public QObject
{
    Q_OBJECT
public:
    explicit RichPaint(QObject *parent = 0);
    ~RichPaint();

    /**
     * Rasterize markup into a cached PNG.
     * Returns {ok(bool), path(file:// url), w(int), h(int), err(QString)}.
     * widthPx: image width; fontPx: glyph pixel height; mono: use the
     * monospace face; defaultColor: "#rrggbb" for un-colored text.
     */
    QVariantMap render(const QString &markup, int widthPx, int fontPx,
                       bool mono, const QString &defaultColor);

    /** True once base fonts have been found and parsed (lazy, first use). */
    bool fontsReady();

    /** One-line description of the resolved font files (for the log). */
    QString fontDebugInfo();

private:
    RichPaintPrivate *d;
};

#endif
