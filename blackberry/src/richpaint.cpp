#include "richpaint.hpp"

#include <math.h>
#include <string.h>

#define STB_TRUETYPE_IMPLEMENTATION
#define STBTT_STATIC
#include "stb_truetype.h"

#include <QByteArray>
#include <QCryptographicHash>
#include <QDir>
#include <QDirIterator>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QImage>
#include <QList>
#include <QPair>
#include <QSet>
#include <QStringList>
#include <QUrl>
#include <QVariant>
#include <QVector>

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

namespace {

struct LoadedFont {
    QByteArray data;   // stbtt_fontinfo keeps pointers into this buffer
    stbtt_fontinfo info;
    int ascent;
    int descent;
    int lineGap;
    bool ok;
    QString path;

    LoadedFont() : ascent(0), descent(0), lineGap(0), ok(false)
    {
        memset(&info, 0, sizeof(info));
    }

    bool load(const QString &p)
    {
        QFile f(p);
        if (!f.open(QIODevice::ReadOnly))
            return false;
        data = f.readAll();
        f.close();
        if (data.size() < 12)
            return false;
        const unsigned char *raw =
            reinterpret_cast<const unsigned char *>(data.constData());
        const int off = stbtt_GetFontOffsetForIndex(raw, 0);
        if (off < 0)
            return false;
        if (!stbtt_InitFont(&info, raw, off))
            return false;
        stbtt_GetFontVMetrics(&info, &ascent, &descent, &lineGap);
        if (ascent <= 0)
            return false;
        ok = true;
        path = p;
        return true;
    }

    bool has(uint cp) const
    {
        return ok && stbtt_FindGlyphIndex(&info, int(cp)) > 0;
    }
};

struct CachedGlyph {
    QByteArray alpha;  // w*h 8-bit coverage
    int w;
    int h;
    int xoff;
    int yoff;
    int adv;           // advance in px at this size
    bool valid;        // glyph exists in the font
    CachedGlyph() : w(0), h(0), xoff(0), yoff(0), adv(0), valid(false) {}
};

struct Atom {
    uint cp;
    quint32 color;   // 0xffRRGGBB
    bool bold;
    bool italic;
    Atom() : cp(0), color(0xffd0d0d0), bold(false), italic(false) {}
};

struct Placed {
    int x;
    uint cp;
    quint32 color;
    bool bold;
    bool italic;
    LoadedFont *font;
    const CachedGlyph *g;
};

typedef QList<Placed> Line;

bool isSpaceCp(uint cp)
{
    return cp == 32 || cp == 0xA0;
}

bool isCjkCp(uint cp)
{
    // Blocks that wrap per-character (CJK ideographs, kana, fullwidth forms)
    return (cp >= 0x2E80 && cp <= 0x9FFF)
            || (cp >= 0x3000 && cp <= 0x303F)
            || (cp >= 0xAC00 && cp <= 0xD7AF)
            || (cp >= 0xF900 && cp <= 0xFAFF)
            || (cp >= 0xFF00 && cp <= 0xFFEF);
}

bool parseHexColor(const QString &in, quint32 *out)
{
    QString s = in.trimmed();
    if (s.startsWith(QLatin1Char('#')))
        s = s.mid(1);
    if (s.size() == 3) {
        QString e;
        for (int i = 0; i < 3; ++i) {
            e += s.at(i);
            e += s.at(i);
        }
        s = e;
    }
    if (s.size() < 6)
        return false;
    bool ok = false;
    const uint v = s.left(6).toUInt(&ok, 16);
    if (!ok)
        return false;
    *out = 0xff000000u | v;
    return true;
}

quint32 namedOrHexColor(const QString &v, quint32 fallback)
{
    quint32 c = 0;
    if (parseHexColor(v, &c))
        return c;
    const QString n = v.trimmed().toLower();
    if (n == QLatin1String("white")) return 0xffffffffu;
    if (n == QLatin1String("black")) return 0xff000000u;
    if (n == QLatin1String("red")) return 0xffe06c75u;
    if (n == QLatin1String("green")) return 0xff98c379u;
    if (n == QLatin1String("blue")) return 0xff61afefu;
    if (n == QLatin1String("yellow")) return 0xffe5c07bu;
    if (n == QLatin1String("cyan")) return 0xff56b6c2u;
    if (n == QLatin1String("magenta")) return 0xffc678ddu;
    if (n == QLatin1String("gray") || n == QLatin1String("grey"))
        return 0xff9a9a9au;
    if (n == QLatin1String("orange")) return 0xffd19a66u;
    return fallback;
}

const quint32 kLinkColor = 0xff67e8f9u;

} // namespace

// ---------------------------------------------------------------------------
// Private state
// ---------------------------------------------------------------------------

struct RichPaintPrivate {
    bool triedInit;
    bool ready;
    LoadedFont *regular;
    LoadedFont *fbold;    // may alias regular (synthetic bold then)
    LoadedFont *mono;     // may alias regular
    QList<LoadedFont *> owned;      // all heap fonts for cleanup
    QList<LoadedFont *> extras;     // lazily loaded fallbacks (e.g. CJK)
    QStringList fallbackPaths;      // candidates not yet loaded
    QHash<uint, LoadedFont *> cpFont;   // codepoint -> resolved fallback
    QHash<quint64, CachedGlyph> glyphs;
    QSet<uint> cpMissing;           // codepoints no font provides
    QString cacheDir;
    QString dbg;
    QHash<QString, QString> cacheIndex;  // sha -> "<sha>_WxH.png" on disk

    // Per-open profiling (reset by RichPaint::resetProfile before a build
    // loop; read via profileSummary). Nanoseconds accumulated across calls.
    qint64 profLookupNs;
    qint64 profRasterNs;
    qint64 profSaveNs;
    int profCalls;
    int profHits;

    RichPaintPrivate()
        : triedInit(false), ready(false), regular(0), fbold(0), mono(0),
          profLookupNs(0), profRasterNs(0), profSaveNs(0),
          profCalls(0), profHits(0) {}
};

// ---------------------------------------------------------------------------
// Font discovery
// ---------------------------------------------------------------------------

static QStringList richPaintFontDirs()
{
    QStringList dirs;
    // User/app overrides first, then the BB10 system font repository.
    dirs << QDir::homePath() + QLatin1String("/fonts");
    dirs << QString::fromLatin1("app/native/assets/fonts");
    dirs << QString::fromLatin1("/usr/fonts/font_repository/monotype");
    dirs << QString::fromLatin1("/usr/fonts/font_repository");
    dirs << QString::fromLatin1("/usr/fonts");
    return dirs;
}

static QStringList richPaintScanFonts()
{
    QStringList out;
    QSet<QString> seen;
    const QStringList dirs = richPaintFontDirs();
    for (int i = 0; i < dirs.size(); ++i) {
        QDir base(dirs.at(i));
        if (!base.exists())
            continue;
        QDirIterator it(dirs.at(i),
                        QStringList() << "*.ttf" << "*.TTF"
                                      << "*.ttc" << "*.TTC"
                                      << "*.otf" << "*.OTF",
                        QDir::Files,
                        QDirIterator::Subdirectories);
        while (it.hasNext()) {
            const QString p = it.next();
            const QString canon = QFileInfo(p).canonicalFilePath();
            const QString key = canon.isEmpty() ? p : canon;
            if (seen.contains(key))
                continue;
            seen.insert(key);
            out << p;
        }
    }
    return out;
}

static int scoreBase(const QString &b)
{
    int s = 0;
    if (b.contains(QLatin1String("italic"))
            || b.contains(QLatin1String("oblique")))
        s -= 15;
    if (b.contains(QLatin1String("light"))
            || b.contains(QLatin1String("thin"))
            || b.contains(QLatin1String("medium"))
            || b.contains(QLatin1String("condensed")))
        s -= 8;
    if (b.endsWith(QLatin1String(".ttf")))
        s += 2;
    return s;
}

static int scoreRegular(const QString &b)
{
    int s = scoreBase(b);
    if (b.contains(QLatin1String("slatepro"))) s += 20;
    if (b.contains(QLatin1String("regular"))) s += 6;
    if (b.contains(QLatin1String("sans"))) s += 4;
    if (b.contains(QLatin1String("bold"))) s -= 15;
    if (b.contains(QLatin1String("mono"))
            || b.contains(QLatin1String("courier"))) s -= 10;
    return s;
}

static int scoreBold(const QString &b)
{
    if (!b.contains(QLatin1String("bold")))
        return -1000;
    int s = scoreBase(b) + 10;
    if (b.contains(QLatin1String("slatepro"))) s += 20;
    if (b.contains(QLatin1String("mono"))
            || b.contains(QLatin1String("courier"))) s -= 10;
    return s;
}

static int scoreMono(const QString &b)
{
    const bool monoish = b.contains(QLatin1String("mono"))
            || b.contains(QLatin1String("courier"))
            || b.contains(QLatin1String("consol"))
            || b.contains(QLatin1String("andale"));
    if (!monoish)
        return -1000;
    int s = scoreBase(b) + 10;
    if (b.contains(QLatin1String("bold"))) s -= 12;
    return s;
}

static int scoreCjkHint(const QString &b)
{
    // Graded (higher = tried first) so the CJK fallback list sorts Chinese
    // faces ahead of the Japanese/Korean ones for the shared Han range -
    // 中文 must render with Chinese glyph variants, not Japanese ones. Kana
    // and Hangul still resolve because only the JP/KR faces carry those
    // glyphs, so lookup falls through to them regardless of rank.
    //
    // Pin JP/KR markers BEFORE the Chinese rules: "heisei" contains the
    // substring "hei", so a Chinese-Hei rule would otherwise steal the
    // Japanese Heisei faces.
    if (b.contains(QLatin1String("heisei"))
            || b.contains(QLatin1String("kaku"))
            || b.contains(QLatin1String("mincho"))
            || b.contains(QLatin1String("gothic"))
            || b.contains(QLatin1String("jpn")))
        return 20;
    if (b.contains(QLatin1String("malgun"))
            || b.contains(QLatin1String("hangul"))
            || b.contains(QLatin1String("kor")))
        return 10;
    // Chinese faces: GB18030 (Simplified) and Big5/cp950 (Traditional), plus
    // the classic Hei/Song/Sung/Ming/Kai/Ying family names.
    static const struct { const char *sub; int score; } zh[] = {
        { "gb18030", 100 }, { "18030", 100 },
        { "gb", 95 }, { "big5", 95 }, { "cp950", 95 },
        { "mhei", 80 }, { "hei", 80 }, { "song", 80 }, { "sung", 80 },
        { "ming", 80 }, { "kai", 80 }, { "ying", 80 },
        { "hans", 70 }, { "hant", 70 },
        { "noto", 40 }, { "droid", 40 }, { "worldtype", 40 },
        { "cjk", 40 }, { "sst", 40 },
        { 0, 0 }
    };
    int best = 0;
    for (int i = 0; zh[i].sub; ++i) {
        if (b.contains(QLatin1String(zh[i].sub)) && zh[i].score > best)
            best = zh[i].score;
    }
    return best;
}

static LoadedFont *richPaintPickAndLoad(const QStringList &files,
                                        int (*scorer)(const QString &),
                                        QList<LoadedFont *> *owned,
                                        int minScore)
{
    // Try candidates best-score first until one actually parses.
    QList<QPair<int, QString> > ranked;
    for (int i = 0; i < files.size(); ++i) {
        const QString base = QFileInfo(files.at(i)).fileName().toLower();
        const int s = scorer(base);
        if (s > minScore)
            ranked.append(qMakePair(s, files.at(i)));
    }
    for (int pass = 0; pass < ranked.size(); ++pass) {
        int best = -1;
        for (int i = 0; i < ranked.size(); ++i) {
            if (ranked.at(i).first >= 0
                    && (best < 0 || ranked.at(i).first > ranked.at(best).first))
                best = i;
        }
        if (best < 0)
            break;
        LoadedFont *f = new LoadedFont();
        if (f->load(ranked.at(best).second)) {
            owned->append(f);
            return f;
        }
        delete f;
        ranked[best].first = -1;  // failed to parse; try next best
    }
    return 0;
}

// ---------------------------------------------------------------------------
// RichPaint
// ---------------------------------------------------------------------------

RichPaint::RichPaint(QObject *parent)
    : QObject(parent)
    , d(new RichPaintPrivate())
{
    d->cacheDir = QDir::homePath() + QLatin1String("/paintcache");
    QDir().mkpath(d->cacheDir);

    // Prune old cache files so the sandbox does not grow without bound.
    QDir cd(d->cacheDir);
    QFileInfoList files = cd.entryInfoList(QStringList() << "*.png",
                                           QDir::Files, QDir::Time);
    const int keep = files.size() > 400 ? 300 : files.size();
    for (int i = keep; i < files.size(); ++i)
        QFile::remove(files.at(i).absoluteFilePath());

    // Build the in-memory cache index from the same scan so render() can
    // resolve a cache hit with an O(1) hash lookup instead of an O(dir)
    // wildcard directory glob per block (the measured cold/warm bottleneck).
    for (int i = 0; i < keep; ++i) {
        const QString name = files.at(i).fileName();  // <sha>_WxH.png
        const int us = name.indexOf(QLatin1Char('_'));
        if (us > 0)
            d->cacheIndex.insert(name.left(us), name);
    }
}

RichPaint::~RichPaint()
{
    for (int i = 0; i < d->owned.size(); ++i)
        delete d->owned.at(i);
    delete d;
    d = 0;
}

static LoadedFont *richPaintResolveFont(RichPaintPrivate *d, LoadedFont *base,
                                        uint cp);

static void richPaintInit(RichPaintPrivate *d)
{
    if (d->triedInit)
        return;
    d->triedInit = true;

    const QStringList files = richPaintScanFonts();
    d->regular = richPaintPickAndLoad(files, scoreRegular, &d->owned, -900);
    d->fbold = richPaintPickAndLoad(files, scoreBold, &d->owned, -900);
    d->mono = richPaintPickAndLoad(files, scoreMono, &d->owned, -900);
    if (!d->fbold)
        d->fbold = d->regular;   // synthetic bold at blit time
    if (!d->mono)
        d->mono = d->regular;

    // Fallback candidates for codepoints the base faces lack (CJK first).
    // Rank the CJK faces by scoreCjkHint so Chinese fonts precede Japanese
    // for the shared Han range (中文); non-CJK files keep enumeration order.
    QList<QPair<int, QString> > cjkRanked;
    QStringList rest;
    for (int i = 0; i < files.size(); ++i) {
        const QString p = files.at(i);
        if ((d->regular && p == d->regular->path)
                || (d->fbold && p == d->fbold->path)
                || (d->mono && p == d->mono->path))
            continue;
        const int s = scoreCjkHint(QFileInfo(p).fileName().toLower());
        if (s > 0)
            cjkRanked.append(qMakePair(s, p));
        else
            rest << p;
    }
    QStringList cjk;
    while (!cjkRanked.isEmpty()) {
        int best = 0;
        for (int i = 1; i < cjkRanked.size(); ++i) {
            if (cjkRanked.at(i).first > cjkRanked.at(best).first)
                best = i;
        }
        cjk << cjkRanked.at(best).second;
        cjkRanked.removeAt(best);
    }
    d->fallbackPaths = cjk + rest;

    d->ready = (d->regular != 0 && d->regular->ok);
    d->dbg = QString("fonts=%1 regular=%2 bold=%3 mono=%4 fallbacks=%5")
                 .arg(files.size())
                 .arg(d->regular ? QFileInfo(d->regular->path).fileName()
                                 : QString("NONE"))
                 .arg(d->fbold ? QFileInfo(d->fbold->path).fileName()
                               : QString("NONE"))
                 .arg(d->mono ? QFileInfo(d->mono->path).fileName()
                              : QString("NONE"))
                 .arg(d->fallbackPaths.size());

    // Diagnostic: which file actually serves CJK / kana / Hangul. Resolving
    // these also warms the fallback face.
    struct { uint cp; const char *label; } probe[] = {
        { 0x4E2D, "Han-zh" },   // 中
        { 0x6587, "Han-2" },    // 文
        { 0x3042, "kana" },     // あ
        { 0xAC00, "hangul" },   // 가
        { 0 , 0 }
    };
    QString probeStr;
    for (int i = 0; probe[i].cp; ++i) {
        LoadedFont *f = richPaintResolveFont(d, d->regular, probe[i].cp);
        probeStr += QString(" %1=%2")
                        .arg(QLatin1String(probe[i].label))
                        .arg(f ? QFileInfo(f->path).fileName()
                               : QString("MISS"));
    }
    d->dbg += QString("\nCJK:%1").arg(probeStr);
}

bool RichPaint::fontsReady()
{
    richPaintInit(d);
    return d->ready;
}

QString RichPaint::fontDebugInfo()
{
    richPaintInit(d);
    return d->dbg;
}

void RichPaint::resetProfile()
{
    d->profLookupNs = 0;
    d->profRasterNs = 0;
    d->profSaveNs = 0;
    d->profCalls = 0;
    d->profHits = 0;
}

QString RichPaint::profileSummary() const
{
    return QString("paint calls=%1 hits=%2 lookup=%3ms raster=%4ms save=%5ms")
        .arg(d->profCalls)
        .arg(d->profHits)
        .arg(d->profLookupNs / 1000000)
        .arg(d->profRasterNs / 1000000)
        .arg(d->profSaveNs / 1000000);
}

// ---------------------------------------------------------------------------
// Per-codepoint font resolution (with lazy CJK fallback loading)
// ---------------------------------------------------------------------------

static LoadedFont *richPaintResolveFont(RichPaintPrivate *d, LoadedFont *base,
                                        uint cp)
{
    if (base && base->has(cp))
        return base;
    if (d->cpMissing.contains(cp))
        return 0;
    QHash<uint, LoadedFont *>::const_iterator it = d->cpFont.find(cp);
    if (it != d->cpFont.end())
        return it.value();

    // Other already-loaded faces first
    LoadedFont *loadedSet[3] = { d->regular, d->fbold, d->mono };
    for (int i = 0; i < 3; ++i) {
        if (loadedSet[i] && loadedSet[i] != base && loadedSet[i]->has(cp)) {
            d->cpFont.insert(cp, loadedSet[i]);
            return loadedSet[i];
        }
    }
    for (int i = 0; i < d->extras.size(); ++i) {
        if (d->extras.at(i)->has(cp)) {
            d->cpFont.insert(cp, d->extras.at(i));
            return d->extras.at(i);
        }
    }

    // Load fallback files until one provides the glyph. Loaded files stay
    // (CJK face is large; load it once, keep it).
    while (!d->fallbackPaths.isEmpty()) {
        const QString p = d->fallbackPaths.takeFirst();
        LoadedFont *f = new LoadedFont();
        if (!f->load(p)) {
            delete f;
            continue;
        }
        d->owned.append(f);
        d->extras.append(f);
        if (f->has(cp)) {
            d->cpFont.insert(cp, f);
            return f;
        }
    }
    d->cpMissing.insert(cp);
    return 0;
}

static const CachedGlyph *richPaintGlyph(RichPaintPrivate *d, LoadedFont *f,
                                         uint cp, int sizePx)
{
    int fontIdx = d->owned.indexOf(f);
    if (fontIdx < 0)
        fontIdx = 0x7f;
    const quint64 key = (quint64(uint(fontIdx)) << 40)
            | (quint64(uint(sizePx) & 0xff) << 32)
            | quint64(cp);
    QHash<quint64, CachedGlyph>::const_iterator it = d->glyphs.find(key);
    if (it != d->glyphs.end())
        return &it.value();

    if (d->glyphs.size() > 3000)
        d->glyphs.clear();

    CachedGlyph g;
    if (f && f->ok) {
        const float scale = stbtt_ScaleForPixelHeight(&f->info, float(sizePx));
        int adv = 0, lsb = 0;
        stbtt_GetCodepointHMetrics(&f->info, int(cp), &adv, &lsb);
        g.adv = int(adv * scale + 0.5f);
        if (f->has(cp)) {
            g.valid = true;
            int w = 0, h = 0, xo = 0, yo = 0;
            unsigned char *bmp = stbtt_GetCodepointBitmap(
                &f->info, 0, scale, int(cp), &w, &h, &xo, &yo);
            if (bmp) {
                // Sanity-cap: a corrupt glyph must not allocate wildly
                if (w > 0 && h > 0 && w < 512 && h < 512) {
                    g.w = w;
                    g.h = h;
                    g.xoff = xo;
                    g.yoff = yo;
                    g.alpha = QByteArray(
                        reinterpret_cast<const char *>(bmp), w * h);
                }
                stbtt_FreeBitmap(bmp, 0);
            }
        }
    }
    if (g.adv <= 0)
        g.adv = g.valid ? sizePx / 3 : (sizePx * 3) / 5;

    QHash<quint64, CachedGlyph>::iterator ins = d->glyphs.insert(key, g);
    return &ins.value();
}

// ---------------------------------------------------------------------------
// Markup -> atoms
// ---------------------------------------------------------------------------

static bool richPaintEntity(const QString &s, int amp, int *consumed, uint *cp)
{
    const int semi = s.indexOf(QLatin1Char(';'), amp + 1);
    if (semi < 0 || semi - amp > 10)
        return false;
    const QString name = s.mid(amp + 1, semi - amp - 1).toLower();
    *consumed = semi - amp + 1;
    if (name == QLatin1String("amp")) { *cp = '&'; return true; }
    if (name == QLatin1String("lt")) { *cp = '<'; return true; }
    if (name == QLatin1String("gt")) { *cp = '>'; return true; }
    if (name == QLatin1String("quot")) { *cp = '"'; return true; }
    if (name == QLatin1String("apos")) { *cp = '\''; return true; }
    if (name == QLatin1String("nbsp")) { *cp = ' '; return true; }
    if (name.startsWith(QLatin1Char('#'))) {
        bool ok = false;
        uint v = 0;
        if (name.size() > 2 && (name.at(1) == QLatin1Char('x')))
            v = name.mid(2).toUInt(&ok, 16);
        else
            v = name.mid(1).toUInt(&ok, 10);
        if (ok && v > 8 && v < 0x110000) {
            *cp = v;
            return true;
        }
    }
    return false;
}

static QList<Atom> richPaintParse(const QString &markupIn, quint32 defColor,
                                  bool *truncated)
{
    QList<Atom> atoms;
    QString s = markupIn;
    if (s.size() > 9000) {
        s = s.left(9000);
        *truncated = true;
    }

    QList<quint32> colorStack;
    colorStack.append(defColor);
    int boldDepth = 0;
    int italicDepth = 0;

    const int n = s.size();
    int i = 0;
    while (i < n && atoms.size() < 7000) {
        const QChar ch = s.at(i);

        if (ch == QLatin1Char('<')) {
            const int gt = s.indexOf(QLatin1Char('>'), i + 1);
            if (gt < 0 || gt - i > 300) {
                // Not a tag - literal '<'
                Atom a;
                a.cp = '<';
                a.color = colorStack.last();
                a.bold = boldDepth > 0;
                a.italic = italicDepth > 0;
                atoms.append(a);
                ++i;
                continue;
            }
            QString tag = s.mid(i + 1, gt - i - 1).trimmed().toLower();
            i = gt + 1;
            if (tag.endsWith(QLatin1Char('/')))
                tag.chop(1);
            tag = tag.trimmed();
            if (tag.isEmpty())
                continue;

            if (tag.startsWith(QLatin1Char('/'))) {
                const QString name = tag.mid(1).trimmed();
                if (name == QLatin1String("font")
                        || name == QLatin1String("a")) {
                    if (colorStack.size() > 1)
                        colorStack.removeLast();
                } else if (name == QLatin1String("b")
                           || name == QLatin1String("strong")) {
                    if (boldDepth > 0)
                        --boldDepth;
                } else if (name == QLatin1String("i")
                           || name == QLatin1String("em")) {
                    if (italicDepth > 0)
                        --italicDepth;
                }
                continue;
            }

            if (tag == QLatin1String("br")) {
                Atom a;
                a.cp = '\n';
                atoms.append(a);
                continue;
            }
            if (tag.startsWith(QLatin1String("font"))) {
                quint32 c = colorStack.last();
                const int cpos = tag.indexOf(QLatin1String("color"));
                if (cpos >= 0) {
                    int eq = tag.indexOf(QLatin1Char('='), cpos);
                    if (eq >= 0) {
                        QString v = tag.mid(eq + 1).trimmed();
                        if (v.startsWith(QLatin1Char('"'))
                                || v.startsWith(QLatin1Char('\'')))
                            v = v.mid(1);
                        int end = 0;
                        while (end < v.size()
                               && v.at(end) != QLatin1Char('"')
                               && v.at(end) != QLatin1Char('\'')
                               && v.at(end) != QLatin1Char(' '))
                            ++end;
                        c = namedOrHexColor(v.left(end), c);
                    }
                }
                colorStack.append(c);
                continue;
            }
            if (tag == QLatin1String("b") || tag == QLatin1String("strong")) {
                ++boldDepth;
                continue;
            }
            if (tag == QLatin1String("i") || tag == QLatin1String("em")) {
                ++italicDepth;
                continue;
            }
            if (tag == QLatin1String("a")
                    || tag.startsWith(QLatin1String("a "))) {
                colorStack.append(kLinkColor);
                continue;
            }
            // Unknown tag (u/code/pre/img/...) - drop tag, keep content
            continue;
        }

        if (ch == QLatin1Char('&')) {
            int used = 0;
            uint cp = 0;
            if (richPaintEntity(s, i, &used, &cp)) {
                Atom a;
                a.cp = cp;
                a.color = colorStack.last();
                a.bold = boldDepth > 0;
                a.italic = italicDepth > 0;
                atoms.append(a);
                i += used;
                continue;
            }
        }

        uint cp = ch.unicode();
        if (ch.isHighSurrogate() && i + 1 < n && s.at(i + 1).isLowSurrogate()) {
            cp = QChar::surrogateToUcs4(ch, s.at(i + 1));
            ++i;
        }
        ++i;

        if (cp == '\r')
            continue;
        if (cp == '\t') {
            for (int t = 0; t < 4 && atoms.size() < 7000; ++t) {
                Atom a;
                a.cp = ' ';
                a.color = colorStack.last();
                a.bold = boldDepth > 0;
                a.italic = italicDepth > 0;
                atoms.append(a);
            }
            continue;
        }
        Atom a;
        a.cp = (cp == 0xA0) ? uint(' ') : cp;
        a.color = colorStack.last();
        a.bold = boldDepth > 0;
        a.italic = italicDepth > 0;
        atoms.append(a);
    }
    if (i < n)
        *truncated = true;
    return atoms;
}

// ---------------------------------------------------------------------------
// Layout + raster
// ---------------------------------------------------------------------------

static void richPaintBlit(QImage *img, const CachedGlyph *g, int penX,
                          int baselineY, quint32 color, bool italic)
{
    if (!g || g->w <= 0 || g->h <= 0 || g->alpha.size() < g->w * g->h)
        return;
    const int W = img->width();
    const int H = img->height();
    const unsigned char *src =
        reinterpret_cast<const unsigned char *>(g->alpha.constData());
    const quint32 rgb = color & 0x00ffffffu;

    for (int y = 0; y < g->h; ++y) {
        const int dy = baselineY + g->yoff + y;
        if (dy < 0 || dy >= H)
            continue;
        // Cheap oblique for <i>: shear rows above the baseline rightwards
        int shear = 0;
        if (italic) {
            const int above = baselineY - dy;
            if (above > 0)
                shear = (above * 21) / 100;
        }
        QRgb *dst = reinterpret_cast<QRgb *>(img->scanLine(dy));
        const unsigned char *srow = src + y * g->w;
        for (int x = 0; x < g->w; ++x) {
            const unsigned char v = srow[x];
            if (!v)
                continue;
            const int dx = penX + g->xoff + x + shear;
            if (dx < 0 || dx >= W)
                continue;
            if (uint(qAlpha(dst[dx])) < uint(v))
                dst[dx] = (quint32(v) << 24) | rgb;
        }
    }
}

static void richPaintDrawBox(QImage *img, int penX, int baselineY, int adv,
                             int fontPx, quint32 color)
{
    // .notdef box for codepoints no font provides
    const int W = img->width();
    const int H = img->height();
    const int bw = adv > 4 ? adv - 3 : 2;
    const int bh = (fontPx * 7) / 10;
    const quint32 px = 0xb4000000u | (color & 0x00ffffffu);
    for (int y = 0; y < bh; ++y) {
        const int dy = baselineY - bh + y;
        if (dy < 0 || dy >= H)
            continue;
        QRgb *dst = reinterpret_cast<QRgb *>(img->scanLine(dy));
        for (int x = 0; x < bw; ++x) {
            const int dx = penX + 1 + x;
            if (dx < 0 || dx >= W)
                continue;
            const bool edge = (y == 0 || y == bh - 1 || x == 0 || x == bw - 1);
            if (edge)
                dst[dx] = px;
        }
    }
}

QVariantMap RichPaint::render(const QString &markup, int widthPx, int fontPx,
                              bool mono, const QString &defaultColor)
{
    QVariantMap out;
    out.insert(QString("ok"), false);

    richPaintInit(d);
    if (!d->ready) {
        out.insert(QString("err"), QString("no usable fonts (%1)").arg(d->dbg));
        return out;
    }

    if (widthPx < 120)
        widthPx = 120;
    // Classic 720 / Passport 1440 - allow full-width paint (was capped at
    // 720, which left half the Passport screen empty).
    if (widthPx > 1600)
        widthPx = 1600;
    if (fontPx < 14)
        fontPx = 14;
    if (fontPx > 72)
        fontPx = 72;

    d->profCalls++;
    QElapsedTimer profTimer;
    profTimer.start();

    // ---- cache lookup ----
    const QString cacheKey = QString::number(widthPx) + QLatin1Char('|')
            + QString::number(fontPx) + QLatin1Char('|')
            + (mono ? QLatin1String("m") : QLatin1String("p"))
            + QLatin1Char('|') + defaultColor + QLatin1Char('|') + markup
            + QLatin1String("|v1");
    const QString sha = QString::fromLatin1(
        QCryptographicHash::hash(cacheKey.toUtf8(),
                                 QCryptographicHash::Sha1).toHex());
    {
        // O(1) hash lookup (index built at construction; updated on save)
        // then a single stat to confirm the file is still there.
        const QString name = d->cacheIndex.value(sha);
        if (!name.isEmpty()
                && QFile::exists(d->cacheDir + QLatin1Char('/') + name)) {
            // <sha>_<w>x<h>.png
            const int us = name.lastIndexOf(QLatin1Char('_'));
            const int xx = name.indexOf(QLatin1Char('x'), us);
            const int dot = name.lastIndexOf(QLatin1Char('.'));
            if (us > 0 && xx > us && dot > xx) {
                const int w = name.mid(us + 1, xx - us - 1).toInt();
                const int h = name.mid(xx + 1, dot - xx - 1).toInt();
                if (w > 0 && h > 0) {
                    d->profLookupNs += profTimer.nsecsElapsed();
                    d->profHits++;
                    out.insert(QString("ok"), true);
                    out.insert(QString("path"),
                               QUrl::fromLocalFile(
                                   d->cacheDir + QLatin1Char('/') + name)
                                   .toString());
                    out.insert(QString("w"), w);
                    out.insert(QString("h"), h);
                    return out;
                }
            }
        }
    }
    d->profLookupNs += profTimer.nsecsElapsed();
    profTimer.restart();  // ---- rasterize phase begins ----

    quint32 defC = 0xffd0d0d0u;
    parseHexColor(defaultColor, &defC);

    bool truncated = false;
    QList<Atom> atoms = richPaintParse(markup, defC, &truncated);

    LoadedFont *base = mono ? d->mono : d->regular;
    LoadedFont *baseBold = mono ? d->mono : d->fbold;
    const bool syntheticBold = (baseBold == base) || mono;

    const float baseScale =
        stbtt_ScaleForPixelHeight(&base->info, float(fontPx));
    int lineH = int((base->ascent - base->descent + base->lineGap)
                    * baseScale + 0.999f);
    if (lineH < fontPx)
        lineH = fontPx + 2;
    lineH += 2;
    const int ascentPx = int(base->ascent * baseScale + 0.5f);
    const int padX = 2;
    const int padY = 3;
    const int usable = widthPx - 2 * padX;
    const int maxLines = (1900 - 2 * padY) / lineH;

    // ---- layout (greedy wrap over tokens) ----
    QList<Line> lines;
    Line cur;
    int penx = padX;
    bool clipped = false;
    // Leading spaces are kept after explicit newlines (code indentation)
    // but dropped after soft wraps.
    bool afterWrap = false;

    const int N = atoms.size();
    int i = 0;
    while (i < N) {
        if (int(lines.size()) >= maxLines) {
            clipped = true;
            break;
        }
        const Atom &a0 = atoms.at(i);

        if (a0.cp == '\n') {
            lines.append(cur);
            cur = Line();
            penx = padX;
            afterWrap = false;
            ++i;
            continue;
        }

        // token = [i, j): single space, single CJK char, or a word
        int j = i + 1;
        if (!isSpaceCp(a0.cp) && !isCjkCp(a0.cp)) {
            while (j < N) {
                const uint c = atoms.at(j).cp;
                if (c == '\n' || isSpaceCp(c) || isCjkCp(c))
                    break;
                ++j;
            }
        }

        // measure token
        int tokenW = 0;
        for (int k = i; k < j; ++k) {
            const Atom &a = atoms.at(k);
            LoadedFont *bf = a.bold ? baseBold : base;
            LoadedFont *f = richPaintResolveFont(d, bf, a.cp);
            const CachedGlyph *g =
                richPaintGlyph(d, f ? f : base, a.cp, fontPx);
            tokenW += g->adv + ((a.bold && syntheticBold) ? 1 : 0);
        }

        if (isSpaceCp(a0.cp)) {
            if (penx + tokenW > usable + padX && !cur.isEmpty()) {
                lines.append(cur);   // wrap; drop the space
                cur = Line();
                penx = padX;
                afterWrap = true;
            } else if (!(cur.isEmpty() && afterWrap)) {
                Placed p;
                p.x = penx;
                p.cp = a0.cp;
                p.color = a0.color;
                p.bold = a0.bold;
                p.italic = a0.italic;
                LoadedFont *bf = a0.bold ? baseBold : base;
                p.font = bf;
                p.g = richPaintGlyph(d, bf, a0.cp, fontPx);
                cur.append(p);
                penx += tokenW;
            }
            i = j;
            continue;
        }

        if (penx + tokenW > usable + padX && !cur.isEmpty()) {
            lines.append(cur);
            cur = Line();
            penx = padX;
            afterWrap = true;
        }

        if (tokenW > usable) {
            // token longer than a whole line: hard char split
            for (int k = i; k < j; ++k) {
                if (int(lines.size()) >= maxLines) {
                    clipped = true;
                    break;
                }
                const Atom &a = atoms.at(k);
                LoadedFont *bf = a.bold ? baseBold : base;
                LoadedFont *f = richPaintResolveFont(d, bf, a.cp);
                LoadedFont *use = f ? f : base;
                const CachedGlyph *g = richPaintGlyph(d, use, a.cp, fontPx);
                const int aw = g->adv + ((a.bold && syntheticBold) ? 1 : 0);
                if (penx + aw > usable + padX && !cur.isEmpty()) {
                    lines.append(cur);
                    cur = Line();
                    penx = padX;
                    afterWrap = true;
                }
                Placed p;
                p.x = penx;
                p.cp = a.cp;
                p.color = a.color;
                p.bold = a.bold;
                p.italic = a.italic;
                p.font = f;
                p.g = g;
                cur.append(p);
                penx += aw;
            }
            i = j;
            continue;
        }

        for (int k = i; k < j; ++k) {
            const Atom &a = atoms.at(k);
            LoadedFont *bf = a.bold ? baseBold : base;
            LoadedFont *f = richPaintResolveFont(d, bf, a.cp);
            LoadedFont *use = f ? f : base;
            const CachedGlyph *g = richPaintGlyph(d, use, a.cp, fontPx);
            Placed p;
            p.x = penx;
            p.cp = a.cp;
            p.color = a.color;
            p.bold = a.bold;
            p.italic = a.italic;
            p.font = f;
            p.g = g;
            cur.append(p);
            penx += g->adv + ((a.bold && syntheticBold) ? 1 : 0);
        }
        i = j;
    }
    if (!cur.isEmpty() || lines.isEmpty())
        lines.append(cur);

    // ---- raster ----
    const int height = 2 * padY + int(lines.size()) * lineH;
    QImage img(widthPx, height, QImage::Format_ARGB32);
    if (img.isNull()) {
        out.insert(QString("err"),
                   QString("image alloc %1x%2 failed").arg(widthPx).arg(height));
        return out;
    }
    img.fill(0);

    for (int li = 0; li < lines.size(); ++li) {
        const int baseline = padY + li * lineH + ascentPx;
        const Line &L = lines.at(li);
        for (int k = 0; k < L.size(); ++k) {
            const Placed &p = L.at(k);
            if (!p.font || !p.g || !p.g->valid) {
                if (!isSpaceCp(p.cp))
                    richPaintDrawBox(&img, p.x, baseline,
                                     p.g ? p.g->adv : fontPx / 2,
                                     fontPx, p.color);
                continue;
            }
            richPaintBlit(&img, p.g, p.x, baseline, p.color, p.italic);
            // Synthesize bold (double blit) unless the glyph came from a
            // real bold face.
            if (p.bold && (syntheticBold || p.font != d->fbold))
                richPaintBlit(&img, p.g, p.x + 1, baseline, p.color,
                              p.italic);
        }
    }

    if (truncated || clipped) {
        // Ellipsis marker bottom-right so cut-off content is visible
        LoadedFont *f = richPaintResolveFont(d, base, 0x2026);
        const CachedGlyph *g =
            richPaintGlyph(d, f ? f : base, 0x2026, fontPx);
        if (g->valid)
            richPaintBlit(&img, g, widthPx - g->adv - padX - 1,
                          padY + (int(lines.size()) - 1) * lineH + ascentPx,
                          defC, false);
    }

    d->profRasterNs += profTimer.nsecsElapsed();
    profTimer.restart();  // ---- png save phase ----

    const QString fileName = sha + QString("_%1x%2.png").arg(widthPx).arg(height);
    const QString path = d->cacheDir + QLatin1Char('/') + fileName;
    if (!img.save(path, "PNG")) {
        out.insert(QString("err"), QString("png save failed: %1").arg(path));
        return out;
    }
    d->cacheIndex.insert(sha, fileName);
    d->profSaveNs += profTimer.nsecsElapsed();

    out.insert(QString("ok"), true);
    out.insert(QString("path"), QUrl::fromLocalFile(path).toString());
    out.insert(QString("w"), widthPx);
    out.insert(QString("h"), height);
    return out;
}
