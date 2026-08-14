#include "apiclient.hpp"

#include <bb/cascades/Application>
#include <bb/cascades/Color>
#include <bb/cascades/Page>
#include <bb/cascades/QmlDocument>
#include <bb/cascades/ThemeSupport>
#include <bb/data/JsonDataAccess>
#include <bb/system/Clipboard>
#include <bb/system/InvokeManager>
#include <bb/system/InvokeRequest>
#include <bb/system/SystemPrompt>
#include <bb/system/SystemToast>
#include <bb/system/SystemUiButton>
#include <bb/system/SystemUiInputField>
#include <QtDeclarative/QDeclarativeError>
#include <QtAlgorithms>
#include <QColor>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QRegExp>
#include <QSettings>
#include <QStringList>
#include <QUrl>
#include <bb/device/DisplayInfo>

#include "brand.hpp"
#include "chime.hpp"
#include "richpaint.hpp"

namespace {
const int POLL_INTERVAL_MS = 1500;
// A status frame older than this can't vouch that the job has no new
// events; fall back to a plain poll (frames tick ~1/s during a job).
const int WS_FRESH_MS = 4000;
const int MAX_POLL_FAILURES = 6;
// Qt 4.8 QNAM has no default timeout: a hung request would otherwise
// wedge job polling forever. abort() makes the reply finish with an error.
const int REQUEST_TIMEOUT_MS = 20 * 1000;
// Full-text session search may open many transcript files.
const int SEARCH_TIMEOUT_MS = 60 * 1000;
// Usage can be slow on the grok daemon: it has no usage endpoint to call, so
// it resumes a dedicated TUI and reads its /usage output (~4s warm, but a
// cold grok start is tens of seconds). Claude's answers in well under 20s.
const int USAGE_TIMEOUT_MS = 90 * 1000;
// Keystroke coalesce for the sessions search field.
const int SEARCH_DEBOUNCE_MS = 50;
const int UPLOAD_TIMEOUT_MS = 90 * 1000;
const int DOWNLOAD_TIMEOUT_MS = 120 * 1000;
const int MAX_UPLOAD_BYTES = 16 * 1024 * 1024;
const int MAX_DROP_BYTES = 64 * 1024 * 1024;
const int PAGE_SIZE = 50;
// The first transcript window is smaller: RichPaint rasterizes every rich
// block to a PNG, so a big first page is the slowest part of opening a
// session. Show the last screenful fast, page the rest in via loadOlder.
const int INITIAL_PAGE_SIZE = 20;
const int MAX_QUEUED_PROMPTS = 10;
// Classic/Q20 reference: 720px screen, body/code paint insets (28 / 48).
const int REF_SCREEN_W = 720;
const int PAINT_INSET_BODY = 28;
const int PAINT_INSET_CODE = 48;
const int MAX_ERROR_LOG_CHARS = 6000;

// UI: only Interactive | Headless. Both always bypass tool permissions.
// Legacy acceptEdits / default / plan / bypassPermissions → headless.
static QString normalizeExecMode(const QString &mode)
{
    if (mode == QLatin1String("interactive"))
        return QLatin1String("interactive");
    return QLatin1String("headless");
}

// Kanban state tag as shown on a list row. Kept short: it shares the third
// line with the branch, the daemon name and the relative time.
static QString focusStateLabel(const QString &state)
{
    if (state == QLatin1String("needs_answer"))
        return QObject::tr("needs answer");
    if (state == QLatin1String("failed"))
        return QObject::tr("failed");
    if (state == QLatin1String("working"))
        return QObject::tr("working");
    if (state == QLatin1String("turn_finished"))
        return QObject::tr("turn finished");
    return QString();
}

// Daemon still expects "bypassPermissions" for non-interactive turns.
static QString wireExecMode(const QString &mode)
{
    return normalizeExecMode(mode) == QLatin1String("interactive")
            ? QLatin1String("interactive")
            : QLatin1String("bypassPermissions");
}

QDateTime parseIso(const QString &iso)
{
    // "2026-07-19T10:00:05.123Z" - Qt 4.8's ISODate chokes on the
    // fractional seconds, so parse the fixed-width prefix as UTC.
    if (iso.length() < 19)
        return QDateTime();
    QDateTime dt = QDateTime::fromString(iso.left(19), "yyyy-MM-ddThh:mm:ss");
    if (dt.isValid())
        dt.setTimeSpec(Qt::UTC);
    return dt;
}

QString timeAgo(const QString &iso)
{
    QDateTime dt = parseIso(iso);
    if (!dt.isValid())
        return QString();
    qint64 secs = dt.secsTo(QDateTime::currentDateTimeUtc());
    if (secs < 0)
        secs = 0;
    if (secs < 60)
        return QObject::tr("just now");
    if (secs < 3600)
        return QObject::tr("%1m ago").arg(secs / 60);
    if (secs < 86400)
        return QObject::tr("%1h ago").arg(secs / 3600);
    return QObject::tr("%1d ago").arg(secs / 86400);
}

QString normalizeBaseUrl(const QString &raw)
{
    QString url = raw.trimmed();
    while (url.endsWith(QLatin1Char('/')))
        url.chop(1);
    if (url.isEmpty())
        return url;
    // Bare "host:port" or "host" is what people type - default the scheme.
    if (!url.startsWith(QLatin1String("http://"))
            && !url.startsWith(QLatin1String("https://")))
        url = QLatin1String("http://") + url;
    return url;
}

// Status-banner snippets: collapse whitespace, then keep head + tail so a
// long path/command still shows where it starts and ends (middle ellipsis).
QString shortDetail(const QString &s, int maxLen = 48)
{
    QString t = s.simplified();
    if (t.length() <= maxLen)
        return t;
    // One Unicode ellipsis character ("..."); leave room for it in the budget.
    const QString ell = QString::fromUtf8("\xE2\x80\xA6");
    if (maxLen < 3)
        return t.left(maxLen);
    const int keep = maxLen - 1;
    const int head = keep / 2;
    const int tail = keep - head;
    return t.left(head) + ell + t.right(tail);
}

// Set once (ApiClient ctor) so the free blockItem() can stamp the client into
// every model row. It is the ONLY handle a long-press contextActions ActionItem
// can reach: ListItem.view and the _api context property are both null in that
// scope, but ListItemData (the row map) resolves - so we put the QObject there.
static QObject *s_modelApi = 0;

// Per-provider accent families for the unified variant (matches the Android
// app's Accent enum). The single-provider variants keep their compile-time
// brand.hpp palette; here the palette is a property of whichever daemon the
// active profile talks to.
struct ProviderPalette {
    const char *accent;     // brand accent (title bar, chips, titles)
    const char *bannerBg;   // live status banner background
    const char *bannerText; // live status banner text
    const char *liveWell;   // live transcript item tint
    const char *heading;    // rich-text heading color
    const char *thought;    // thinking/meta line color
    const char *inlineCode; // inline code + links
};
const ProviderPalette CLAUDE_PALETTE =
    {"#d97757", "#2a1f1a", "#e8a088", "#20180f", "#e08a5c", "#8a92e0", "#5a69ee"};
const ProviderPalette GROK_PALETTE =
    {"#00d4ff", "#0d1a1f", "#00d4ff", "#17171c", "#c678dd", "#9a8fb0", "#67e8f9"};
const ProviderPalette CODEX_PALETTE =
    {"#10a37f", "#0d1f1a", "#3dd68c", "#141a17", "#3dd68c", "#7a9a8a", "#6ee7b7"};

const ProviderPalette *palForProvider(const QString &provider)
{
    if (provider == QLatin1String("claude"))
        return &CLAUDE_PALETTE;
    if (provider == QLatin1String("grok"))
        return &GROK_PALETTE;
    if (provider == QLatin1String("codex"))
        return &CODEX_PALETTE;
    return 0; // unknown -> brand.hpp neutral fallbacks
}

// Merged-list ordering: newest activity first, whichever daemon it lives on.
bool sessionRowNewer(const QVariant &a, const QVariant &b)
{
    return a.toMap().value("_sortKey").toLongLong()
            > b.toMap().value("_sortKey").toLongLong();
}

QString formatBytes(qint64 n)
{
    if (n < 1024)
        return QString("%1 B").arg(n);
    if (n < 1024 * 1024)
        return QString("%1 KB").arg(n / 1024.0, 0, 'f', n < 10 * 1024 ? 1 : 0);
    if (n < 1024LL * 1024 * 1024)
        return QString("%1 MB").arg(n / (1024.0 * 1024.0), 0, 'f',
                                    n < 10 * 1024 * 1024 ? 1 : 0);
    return QString("%1 GB").arg(n / (1024.0 * 1024.0 * 1024.0), 0, 'f', 1);
}

// Device-wide shared/downloads so File Manager sees the file (same pattern
// as the BerryBox Dropbox client). Falls back to the app sandbox.
QString dropDownloadDir()
{
    QDir shared(QLatin1String("/accounts/1000/shared/downloads"));
    QString base = shared.exists() ? shared.absolutePath() : QDir::homePath();
    QDir dir(base);
    dir.mkpath(QLatin1String("Inbox"));
    return dir.absoluteFilePath(QLatin1String("Inbox"));
}

QString safeLocalFileName(const QString &name)
{
    QString out;
    for (int i = 0; i < name.size(); ++i) {
        const QChar c = name.at(i);
        if (c.isLetterOrNumber() || c == QLatin1Char('.') || c == QLatin1Char('-')
                || c == QLatin1Char('_') || c == QLatin1Char(' '))
            out += c;
        else
            out += QLatin1Char('_');
    }
    out = out.trimmed();
    if (out.isEmpty() || out == QLatin1String(".") || out == QLatin1String(".."))
        out = QLatin1String("file");
    return out;
}

// Transcript items are typed display blocks (GrokRemote's model): the
// daemon renders markdown into Cascades-safe HTML per block, the phone
// rasterizes rich blocks to PNGs (paint pipeline) and routes the rest by
// kind. Kinds the QML knows: user, p, h, li, code, hr, gap, meta,
// paintimg, th, tr, older.
// Unique id stamped on every user row so the long-press "Rewind to here"
// action can find its row again (only ListItemData crosses into a
// contextActions ActionItem, and maps copy by value into the QML model).
static int s_userRowSeq = 0;

QVariantMap blockItem(const QString &kind, const QString &text,
                      const QString &rich, bool live)
{
    QVariantMap item;
    item["kind"] = kind;
    item["text"] = text;
    item["rich"] = rich;
    item["live"] = live;
    if (kind == QLatin1String("user"))
        item["rowId"] = ++s_userRowSeq;
    // Stamp the variant's live-well tint into the model. QML must read it from
    // ListItemData (reliable at bind time), NOT ListItem.view - a structural
    // `background:` paint bound through ListItem.view evaluates before the view
    // attaches and stays transparent (the tint vanishes).
    // Read through the client: on the unified variant the tint follows the
    // active profile's provider, not the compile-time brand.
    item["liveWell"] = s_modelApi
            ? static_cast<ApiClient *>(s_modelApi)->themeLiveWell()
            : QString::fromLatin1(BRAND_LIVE_WELL);
    if (s_modelApi)
        item["api"] = QVariant::fromValue<QObject *>(s_modelApi);
    return item;
}
} // namespace

ApiClient::ApiClient(QObject *parent)
    : QObject(parent)
    , m_workingCount(0)
    , m_activeProfile(0)
    , m_unifiedGen(0)
    , m_unifiedPending(0)
    , m_dropGen(0)
    , m_dropPending(0)
    , m_usageGen(0)
    , m_usagePending(0)
    , m_capPermissions(BRAND_DEFAULT_CAP_PERMISSIONS)
    , m_capRequiresCwd(BRAND_DEFAULT_CAP_REQUIRES_CWD)
    , m_capSetModel(true)
    , m_capSetEffort(false)
    , m_capShowUsage(false)
    // Both providers host a TUI; an older daemon's ping clears this.
    , m_capInteractive(true)
    , m_capLiveTui(true)
    , m_capRewind(false)
    , m_rewindConfirmRev(0)
    , m_rewindConfirmSteps(0)
    , m_pingState(0)
    , m_usageRev(0)
    , m_projectsRev(0)
    , m_sessionsRev(0)
    , m_searchQuery()
    , m_messageRev(0)
    , m_earliestOffset(0)
    , m_loadingOlder(false)
    , m_scrollToEnd(true)
    , m_since(0)
    , m_pollFailures(0)
    , m_pollInFlight(false)
    , m_awaitingJob(false)
    , m_jobStartMs(0)
    , m_wsNextSeq(-1)
    , m_wsFrameMs(0)
    , m_wsJobSeen(false)
    , m_wsDoorbell(false)
    , m_wsQueuedCount(0)
    , m_wsPendingPerm(false)
    , m_newSessionRequestRev(0)
    , m_attachRev(0)
    , m_screenWidth(REF_SCREEN_W)
    , m_largeDisplay(false)
    , m_paintWidthBody(REF_SCREEN_W - PAINT_INSET_BODY)
    , m_paintWidthCode(REF_SCREEN_W - PAINT_INSET_CODE)
    , m_fontBodyPx(27)
    , m_fontCodePx(24)
    , m_fontHeadingPx(33)
    , m_dropRev(0)
    , m_richPaint(0)
    , m_chime(new Chime(this))
    , m_richPaintWarned(false)
    , m_renamePrompt(0)
    , m_renameProfileIndex(-1)
    , m_renameSessionId()
    , m_stepEditRev(0)
    , m_stepEditIndex(-1)
{
    s_modelApi = this;
    // Classic/Q20 = 720; Passport = 1440. Same insets as the proven Classic
    // layout so list + paint fill the real screen without half-empty rows.
    {
        bb::device::DisplayInfo di;
        const int w = di.pixelSize().width();
        if (w >= 480)
            m_screenWidth = w;
        m_largeDisplay = (m_screenWidth >= 1000);
        // Same insets on every device: Classic 720 -> 692/672, Passport
        // 1440 -> 1412/1392. One formula, no per-device special cases.
        m_paintWidthBody = m_screenWidth - PAINT_INSET_BODY;
        m_paintWidthCode = m_screenWidth - PAINT_INSET_CODE;
        // Painted fonts only (Cascades UI labels stay system sizes).
        // Classic (720): scale 1.0 -> proven 27/24/33.
        // Passport (1440): full pixel ratio would be 2.0 (too big on device);
        // 1.35 was too small - use damped scale so width growth maps to
        // ~65% of the linear ratio -> Passport ≈ 1.65× (45/40/54).
        const double widthRatio = m_screenWidth / double(REF_SCREEN_W);
        const double fontScale = 1.0 + (widthRatio - 1.0) * 0.65;
        m_fontBodyPx = qRound(27 * fontScale);
        m_fontCodePx = qRound(24 * fontScale);
        m_fontHeadingPx = qRound(33 * fontScale);
    }
    m_dropLocalDir = dropDownloadDir();
    loadProfiles();

    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    // Both brands default to the interactive host TUI now. Installs made
    // before a brand gained that default stored a headless mode, so they get
    // a one-time reset: the stored migration level (legacy bool = level 1)
    // below the brand's own level means "never saw this default". Bumping
    // only the brand whose default changed keeps a deliberate headless
    // choice on the OTHER brand intact.
    int execMigration = settings.value(
        "execModeMigration",
        settings.contains("execModeSplit") ? 1 : 0).toInt();
    if (execMigration < BRAND_EXEC_MIGRATION) {
        settings.setValue("execModeMigration", BRAND_EXEC_MIGRATION);
        settings.setValue("permissionMode", BRAND_DEFAULT_PERMISSION_MODE);
    }
    m_permissionMode = normalizeExecMode(settings.value(
        "permissionMode", BRAND_DEFAULT_PERMISSION_MODE).toString());
    // "" / "default" -> let the daemon (CLI) pick the model / effort.
    m_modelOverride = settings.value("modelOverride", "default").toString();
    m_effortOverride = settings.value("effortOverride", "default").toString();
    // Sessions whose transcript also shows the agent's working steps.
    m_processViewSessions = QSet<QString>::fromList(
        settings.value("processViewSessions").toStringList());
    // Progress cues are on by default; the Session sheet silences either one.
    m_soundCues = settings.value("soundCues", true).toBool();
    // Focus mode is a view preference; capFocus comes from /api/ping.
    m_focusMode = settings.value("focusMode", false).toBool();
    m_capFocus = false;
    m_ledCues = settings.value("ledCues", true).toBool();
    m_chime->setSoundEnabled(m_soundCues);
    m_chime->setLedEnabled(m_ledCues);

    connect(&m_nam, SIGNAL(finished(QNetworkReply*)),
            this, SLOT(onFinished(QNetworkReply*)));

    m_pollTimer.setInterval(POLL_INTERVAL_MS);
    connect(&m_pollTimer, SIGNAL(timeout()), this, SLOT(pollJob()));

    m_tuiOpen = false;
    m_tuiInFlight = false;
    m_tuiSeq = 0;
    m_tuiRev = 0;
    m_tuiAttached = false;
    m_tuiLive = false;
    m_tuiStatus = tr("Host TUI");
    m_tuiTimer.setInterval(400);
    connect(&m_tuiTimer, SIGNAL(timeout()), this, SLOT(pollTui()));

    m_searchDebounce.setSingleShot(true);
    m_searchDebounce.setInterval(SEARCH_DEBOUNCE_MS);
    connect(&m_searchDebounce, SIGNAL(timeout()), this, SLOT(runPendingSearch()));

    // Daemon-pushed status stream for the transcript banner + list counts.
    connect(&m_statusSocket, SIGNAL(textFrame(QByteArray)),
            this, SLOT(onStatusFrame(QByteArray)));

    loadPersistedError();
    adoptCrashDump();

    if (configured()) {
        m_statusSocket.configure(m_baseUrl, m_token);
        // Learn the daemon's provider + capabilities + slash commands up
        // front (the sheet shows the same result if it happens to be open).
        ping();
    }

#ifdef VARIANT_UNIFIED
    // Identity before network: the active profile's cached provider/caps
    // paint the UI correctly on the very first frame; the pings refresh it.
    applyCachedCaps(m_activeProfile);
    rebuildExtraStreams();
    pingProfiles();
#endif
}

QString ApiClient::brandName() const { return QLatin1String(BRAND_APP_NAME); }
QString ApiClient::brandVersion() const { return QLatin1String(BRAND_VERSION); }

bool ApiClient::isUnified() const
{
#ifdef VARIANT_UNIFIED
    return true;
#else
    return false;
#endif
}

// On the unified build the agent identity is whoever the active profile's
// daemon fronts; the compile-time name is only the pre-ping fallback.
QString ApiClient::agentName() const
{
#ifdef VARIANT_UNIFIED
    if (m_provider == QLatin1String("claude"))
        return QLatin1String("Claude");
    if (m_provider == QLatin1String("grok"))
        return QLatin1String("Grok");
    if (m_provider == QLatin1String("codex"))
        return QLatin1String("Codex");
#endif
    return QLatin1String(BRAND_AGENT_NAME);
}

QString ApiClient::providerAccent(const QString &provider) const
{
    const ProviderPalette *pal = palForProvider(provider);
    return pal ? QString::fromLatin1(pal->accent)
               : QString::fromLatin1(BRAND_ACCENT_COLOR);
}

// File-local helper: BB10 GCC 4.6 has no C++11 lambdas. Tags usage buckets
// with harness + account for multi-host merge (see uusage handler).
static void appendUsageBuckets(QVariantList *tagged,
                               const QVariantList &buckets,
                               const QString &provider,
                               const QString &account,
                               const QString &accountId,
                               const QString &defaultProvider,
                               const QString &defaultAccent,
                               const QString &profileName,
                               const ApiClient *client)
{
    if (!tagged || !client)
        return;
    const QString harness = provider.isEmpty() ? defaultProvider : provider;
    const QString accent = harness.isEmpty()
            ? defaultAccent : client->providerAccent(harness);
    const QString harnessLabel = harness.isEmpty()
            ? QString()
            : (harness.left(1).toUpper() + harness.mid(1));
    const QString prefix = harnessLabel.isEmpty()
            ? QString()
            : (harnessLabel + QLatin1String(" · "));
    for (int i = 0; i < buckets.size(); ++i) {
        QVariantMap b = buckets.at(i).toMap();
        QString title = b.value("title").toString();
        if (!prefix.isEmpty() && title.startsWith(prefix))
            title = title.mid(prefix.size());
        b["title"] = title;
        b["provider"] = harness;
        b["account"] = account;
        b["account_id"] = accountId.isEmpty() ? account : accountId;
        QString source = harnessLabel.isEmpty() ? profileName : harnessLabel;
        if (!account.isEmpty())
            source += QLatin1String(" · ") + account;
        else if (!profileName.isEmpty() && !harnessLabel.isEmpty())
            source = profileName + QLatin1String(" · ") + harnessLabel;
        b["source"] = source;
        b["accent"] = accent;
        b["host"] = profileName;
        tagged->append(b);
    }
}

QStringList ApiClient::profileHarnesses() const
{
    if (m_activeProfile < 0 || m_activeProfile >= m_profiles.size())
        return QStringList();
    QVariantMap prof = m_profiles.at(m_activeProfile).toMap();
    QStringList out;
    // Multi-harness catalogue from /api/ping. Prefer "providers" whenever
    // present (even if "multi" was missing from an older cached profile).
    QVariantList raw = prof.value("providers").toList();
    for (int i = 0; i < raw.size(); ++i) {
        const QString h = raw.at(i).toString().trimmed().toLower();
        if (!h.isEmpty() && !out.contains(h))
            out.append(h);
    }
    // Nested provider_details keys as a fallback catalogue.
    if (out.isEmpty()) {
        QVariantMap details = prof.value("provider_details").toMap();
        QStringList keys = details.keys();
        for (int i = 0; i < keys.size(); ++i) {
            const QString h = keys.at(i).trimmed().toLower();
            if (!h.isEmpty() && !out.contains(h))
                out.append(h);
        }
        out.sort();
    }
    if (out.isEmpty()) {
        const QString p = prof.value("provider").toString().trimmed().toLower();
        if (!p.isEmpty())
            out.append(p);
    }
    return out;
}

// One capability of ONE harness, straight from the profile's cached
// provider_details. A multi-harness daemon answers /api/ping with a UNION at
// the root (so single-harness clients see what the box can do at all), which
// is the wrong thing to gate a session's UI on when harnesses differ.
bool ApiClient::harnessCap(const QString &harness, const QString &cap,
                           bool fallback) const
{
    const QString h = harness.trimmed().toLower();
    if (!h.isEmpty() && m_activeProfile >= 0
            && m_activeProfile < m_profiles.size()) {
        QVariantMap prof = m_profiles.at(m_activeProfile).toMap();
        QVariantMap d = prof.value("provider_details").toMap().value(h).toMap();
        QVariantMap caps = d.value("caps").toMap();
        if (caps.contains(cap))
            return caps.value(cap).toBool();
    }
    return fallback;
}

// The slash commands of the OPEN session's harness. A multi-harness daemon
// lists them per provider (and its root list is only the default harness's),
// so validate against the OPEN session's own list.
QStringList ApiClient::sessionSlashCommands() const
{
    QStringList out;
    const QString h = m_sessionProvider.trimmed().toLower();
    if (!h.isEmpty() && m_activeProfile >= 0
            && m_activeProfile < m_profiles.size()) {
        QVariantMap prof = m_profiles.at(m_activeProfile).toMap();
        QVariantMap d = prof.value("provider_details").toMap().value(h).toMap();
        if (d.contains(QLatin1String("slash_commands"))) {
            QVariantList raw = d.value("slash_commands").toList();
            for (int i = 0; i < raw.size(); ++i)
                out.append(raw.at(i).toString());
        }
    }
    if (out.isEmpty())
        out = m_slashCommands;
    // Always allow even if an older daemon omits them from /api/ping.
    if (!out.contains(QLatin1String("/rewind")))
        out.append(QLatin1String("/rewind"));
    if (!out.contains(QLatin1String("/goal")))
        out.append(QLatin1String("/goal"));
    return out;
}

// Can the harness of the OPEN session rewind? The daemon (>= 2.5) rewinds
// the session journal itself, so any execution mode qualifies. Falls back to
// the daemon-level flag when the session's harness is unknown
// (single-harness daemons); an old daemon advertises nothing and stays off.
bool ApiClient::sessionCanRewind() const
{
    if (m_sessionProvider.isEmpty())
        return m_capRewind;
    return harnessCap(m_sessionProvider, QLatin1String("rewind"), m_capRewind);
}

// Per-harness model / effort lists for the Session sheet. Multi-host /api/ping
// puts a union on the root; using those for every open session made Claude
// sessions show Grok effort pickers (and vice versa).
QStringList ApiClient::sessionModels() const
{
    const QString h = m_sessionProvider.trimmed().toLower();
    if (!h.isEmpty() && m_activeProfile >= 0
            && m_activeProfile < m_profiles.size()) {
        QVariantMap prof = m_profiles.at(m_activeProfile).toMap();
        QVariantMap d = prof.value("provider_details").toMap().value(h).toMap();
        if (d.contains(QLatin1String("models"))) {
            QStringList out;
            QVariantList raw = d.value("models").toList();
            for (int i = 0; i < raw.size(); ++i)
                out.append(raw.at(i).toString());
            if (!out.isEmpty())
                return out;
        }
    }
    return m_models;
}

QStringList ApiClient::sessionEfforts() const
{
    const QString h = m_sessionProvider.trimmed().toLower();
    if (h.isEmpty())
        return m_efforts;
    if (m_activeProfile >= 0 && m_activeProfile < m_profiles.size()) {
        QVariantMap prof = m_profiles.at(m_activeProfile).toMap();
        QVariantMap d = prof.value("provider_details").toMap().value(h).toMap();
        if (d.contains(QLatin1String("efforts"))) {
            QStringList out;
            QVariantList raw = d.value("efforts").toList();
            for (int i = 0; i < raw.size(); ++i)
                out.append(raw.at(i).toString());
            return out; // may be empty (Claude has no effort picker)
        }
    }
    // Unknown detail: only Grok/Codex-like hosts use effort at the root.
    if (h == QLatin1String("claude"))
        return QStringList();
    return m_efforts;
}

bool ApiClient::sessionCapSetModel() const
{
    if (m_sessionProvider.isEmpty())
        return m_capSetModel;
    return harnessCap(m_sessionProvider, QLatin1String("can_set_model"),
                      m_capSetModel);
}

bool ApiClient::sessionCapSetEffort() const
{
    if (m_sessionProvider.isEmpty())
        return m_capSetEffort;
    // Claude never has effort; do not inherit multi-union true.
    if (m_sessionProvider == QLatin1String("claude"))
        return false;
    return harnessCap(m_sessionProvider, QLatin1String("can_set_effort"),
                      m_capSetEffort);
}

bool ApiClient::harnessRequiresCwd(const QString &harness) const
{
    const QString h = harness.trimmed().toLower();
    if (m_activeProfile >= 0 && m_activeProfile < m_profiles.size()) {
        QVariantMap prof = m_profiles.at(m_activeProfile).toMap();
        QVariantMap details = prof.value("provider_details").toMap();
        QVariantMap d = details.value(h).toMap();
        QVariantMap caps = d.value("caps").toMap();
        if (caps.contains(QLatin1String("requires_cwd")))
            return caps.value("requires_cwd").toBool();
    }
    if (h == QLatin1String("grok"))
        return false;
    return m_capRequiresCwd;
}

// Theme/chrome provider: open session's harness wins (multi hosts keep a
// neutral profile accent until you open a Claude/Grok/Codex row).
QString ApiClient::themeProvider() const
{
#ifdef VARIANT_UNIFIED
    if (!m_sessionProvider.isEmpty())
        return m_sessionProvider;
    return m_provider;
#else
    return QString();
#endif
}

QString ApiClient::accentColor() const
{
#ifdef VARIANT_UNIFIED
    return providerAccent(themeProvider());
#else
    return QLatin1String(BRAND_ACCENT_COLOR);
#endif
}

bool ApiClient::usageOpensBrowser() const
{
    // BB10 Qt 4.8 QLatin1String has no size(); check the C string directly.
    return BRAND_USAGE_URL[0] != '\0';
}
// One macro would hide which brand constant backs which role; spelled out.
QString ApiClient::themeBannerBg() const
{
#ifdef VARIANT_UNIFIED
    const ProviderPalette *pal = palForProvider(themeProvider());
    if (pal) return QString::fromLatin1(pal->bannerBg);
#endif
    return QLatin1String(BRAND_BANNER_BG);
}
QString ApiClient::themeBannerText() const
{
#ifdef VARIANT_UNIFIED
    const ProviderPalette *pal = palForProvider(themeProvider());
    if (pal) return QString::fromLatin1(pal->bannerText);
#endif
    return QLatin1String(BRAND_BANNER_TEXT);
}
QString ApiClient::themeUserWell() const { return QLatin1String(BRAND_USER_WELL); }
QString ApiClient::themeLiveWell() const
{
#ifdef VARIANT_UNIFIED
    const ProviderPalette *pal = palForProvider(themeProvider());
    if (pal) return QString::fromLatin1(pal->liveWell);
#endif
    return QLatin1String(BRAND_LIVE_WELL);
}
QString ApiClient::themeHeading() const
{
#ifdef VARIANT_UNIFIED
    const ProviderPalette *pal = palForProvider(themeProvider());
    if (pal) return QString::fromLatin1(pal->heading);
#endif
    return QLatin1String(BRAND_RICH_HEADING);
}
QString ApiClient::themeMetaThought() const
{
#ifdef VARIANT_UNIFIED
    const ProviderPalette *pal = palForProvider(themeProvider());
    if (pal) return QString::fromLatin1(pal->thought);
#endif
    return QLatin1String(BRAND_META_THOUGHT);
}

// Who the banner names — match the web client: harness label ("Grok is
// working"), not the raw model id ("grok-4.5 is working").
QString ApiClient::statusActor() const
{
    if (m_sessionProvider == QLatin1String("claude"))
        return QLatin1String("Claude");
    if (m_sessionProvider == QLatin1String("grok"))
        return QLatin1String("Grok");
    if (m_sessionProvider == QLatin1String("codex"))
        return QLatin1String("Codex");
    // Fallback: active profile provider, then brand agent name.
    if (m_provider == QLatin1String("claude"))
        return QLatin1String("Claude");
    if (m_provider == QLatin1String("grok"))
        return QLatin1String("Grok");
    if (m_provider == QLatin1String("codex"))
        return QLatin1String("Codex");
    return agentName();
}

QString ApiClient::workingLine() const
{
    return tr("%1 is working...").arg(statusActor());
}

int ApiClient::pendingQueueSize() const
{
    return m_pendingQueue.value(m_currentSessionId).size();
}

// ---------------------------------------------------------------- profiles

void ApiClient::loadProfiles()
{
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    QString raw = settings.value("profiles").toString();
    if (!raw.isEmpty()) {
        bb::data::JsonDataAccess jda;
        QVariant data = jda.loadFromBuffer(raw);
        if (!jda.hasError())
            m_profiles = data.toList();
    }
    if (m_profiles.isEmpty()) {
        // Migrate the pre-profile settings (baseUrl, or host/port before
        // that) into profile #0 so upgrades keep their connection.
        QString url = normalizeBaseUrl(settings.value("baseUrl").toString());
        if (url.isEmpty()) {
            QString host = settings.value("host").toString().trimmed();
            int port = settings.value("port", 80).toInt();
            if (!host.isEmpty())
                url = QString("http://%1:%2").arg(host).arg(port);
        }
        if (url.isEmpty())
            url = QLatin1String(BRAND_DEFAULT_URL);
        QVariantMap profile;
        profile["name"] = tr("Default");
        profile["baseUrl"] = url;
        profile["token"] = settings.value(
                "token", QLatin1String(BRAND_DEFAULT_TOKEN)).toString();
        m_profiles.append(profile);
    }
    m_activeProfile = settings.value("activeProfile", 0).toInt();
    if (m_activeProfile < 0 || m_activeProfile >= m_profiles.size())
        m_activeProfile = 0;

    QVariantMap active = m_profiles.at(m_activeProfile).toMap();
    m_baseUrl = normalizeBaseUrl(active.value("baseUrl").toString());
    m_token = active.value("token").toString().trimmed();
    // Cached from the last ping; "" until a daemon has ever answered.
    m_provider = active.value("provider").toString();
}

void ApiClient::persistProfiles()
{
    bb::data::JsonDataAccess jda;
    QByteArray raw;
    jda.saveToBuffer(m_profiles, &raw);
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("profiles", QString::fromUtf8(raw));
    settings.setValue("activeProfile", m_activeProfile);
    // Keep the legacy keys mirrored so a downgrade still connects.
    settings.setValue("baseUrl", m_baseUrl);
    settings.setValue("token", m_token);
    settings.sync();
}

void ApiClient::applyActiveProfile(bool resetState)
{
    QVariantMap active = m_profiles.value(m_activeProfile).toMap();
    m_baseUrl = normalizeBaseUrl(active.value("baseUrl").toString());
    m_token = active.value("token").toString().trimmed();

    if (resetState) {
        // A different daemon: nothing session/job-related carries over.
        m_pollTimer.stop();
        m_pollInFlight = false;
        m_awaitingJob = false;
        m_jobId.clear();
        m_since = 0;
        m_jobStartMs = 0;
        m_jobToolLine.clear();
        setJobTicker(QString());
        clearPendingPermission();
        m_sessionJobs.clear();
        m_pendingQueue.clear();
        m_queued.clear();
        emit queueChanged();
        emit jobRunningChanged();

        m_currentSessionId.clear();
        m_sessionCwd.clear();
        emit currentSessionChanged();
        m_messages.clear();
        m_earliestOffset = 0;
        bumpMessages(true);
        m_jobEndStatus.clear();
        setTranscriptStatus(QString());

        m_sessions.clear();
        m_sessionsStatus.clear();
        m_searchQuery.clear();
        m_sessionsRev++;
        emit sessionsChanged();
        m_projects.clear();
        m_projectsRev++;
        emit projectsChanged();
        m_projectFilter.clear();
        m_projectFilterName.clear();
        emit filterChanged();

        m_activeStatuses.clear();
        updateWorkingSet();
        recomputeLiveStatus();

        // Provider may differ - fall back to brand defaults until ping.
        m_capPermissions = BRAND_DEFAULT_CAP_PERMISSIONS;
        m_capRequiresCwd = BRAND_DEFAULT_CAP_REQUIRES_CWD;
        m_capSetModel = true;
        m_capSetEffort = false;
        m_capShowUsage = false;
        m_capInteractive = true;
        m_capRewind = false;
        m_usageBuckets.clear();
        m_usageStatus.clear();
        m_usageRev++;
        emit usageChanged();
        m_slashCommands.clear();
        m_models.clear();
        m_efforts.clear();
        m_sessionModel.clear();
        emit capsChanged();

#ifdef VARIANT_UNIFIED
        // The brand defaults above are only the floor; this profile's cached
        // ping restores its real caps/provider (accent included) instantly.
        applyCachedCaps(m_activeProfile);
#endif
    }

    m_pingState = 0;
    m_pingInfo = QString();
    emit pingChanged();
    emit settingsChanged();

    m_statusSocket.configure(m_baseUrl, m_token);
#ifdef VARIANT_UNIFIED
    rebuildExtraStreams();
    pingProfiles();
#endif
    if (configured()) {
        ping();
        refreshSessions();
    }
}

void ApiClient::saveProfile(int index, const QString &name,
                            const QString &baseUrl, const QString &token)
{
    QVariantMap profile;
    profile["name"] = name.trimmed().isEmpty() ? tr("Profile %1").arg(m_profiles.size() + 1)
                                               : name.trimmed();
    profile["baseUrl"] = normalizeBaseUrl(baseUrl);
    profile["token"] = token.trimmed();

    if (index >= 0 && index < m_profiles.size()) {
        // Carry over the keys the form does not edit (caps learned by ping,
        // and the enabled flag) - a plain replace() dropped them.
        QVariantMap prev = m_profiles.at(index).toMap();
        if (prev.contains("caps"))
            profile["caps"] = prev.value("caps");
        if (prev.contains("enabled"))
            profile["enabled"] = prev.value("enabled");
        m_profiles.replace(index, profile);
    }
    else {
        m_profiles.append(profile);
        index = m_profiles.size() - 1;
    }
    m_activeProfile = index;
    persistProfiles();
    emit profilesChanged();
    applyActiveProfile(true);
}

void ApiClient::deleteProfile(int index)
{
    if (index < 0 || index >= m_profiles.size())
        return;
    m_profiles.removeAt(index);
    if (m_profiles.isEmpty()) {
        QVariantMap profile;
        profile["name"] = tr("Default");
        profile["baseUrl"] = QLatin1String(BRAND_DEFAULT_URL);
        profile["token"] = QLatin1String(BRAND_DEFAULT_TOKEN);
        m_profiles.append(profile);
    }
    if (m_activeProfile >= m_profiles.size())
        m_activeProfile = m_profiles.size() - 1;
    persistProfiles();
    emit profilesChanged();
    applyActiveProfile(true);
}

void ApiClient::activateProfile(int index)
{
    if (index < 0 || index >= m_profiles.size() || index == m_activeProfile)
        return;
    m_activeProfile = index;
    persistProfiles();
    emit profilesChanged();
    applyActiveProfile(true);
}

// ------------------------------------------------- unified (AgentRemote)

void ApiClient::switchProfile(int index)
{
#ifndef VARIANT_UNIFIED
    // Single-provider variants: the list is single-daemon, so a switch IS
    // the full reset.
    activateProfile(index);
#else
    if (index < 0 || index >= m_profiles.size() || index == m_activeProfile)
        return;
    m_activeProfile = index;
    persistProfiles();
    emit profilesChanged();

    QVariantMap active = m_profiles.value(m_activeProfile).toMap();
    m_baseUrl = normalizeBaseUrl(active.value("baseUrl").toString());
    m_token = active.value("token").toString().trimmed();

    // Job/transcript state is daemon-scoped and cannot carry over; the
    // merged session list is exactly what must survive - that is the whole
    // reason this is not activateProfile.
    m_pollTimer.stop();
    m_pollInFlight = false;
    m_awaitingJob = false;
    m_jobId.clear();
    m_since = 0;
    m_jobStartMs = 0;
    m_jobToolLine.clear();
    setJobTicker(QString());
    clearPendingPermission();
    clearPendingQuestion();
    m_sessionJobs.clear();
    m_pendingQueue.clear();
    m_queued.clear();
    emit queueChanged();
    emit jobRunningChanged();
    m_currentSessionId.clear();
    m_sessionCwd.clear();
    m_sessionModel.clear();
    emit currentSessionChanged();
    m_messages.clear();
    m_earliestOffset = 0;
    bumpMessages(true);
    m_jobEndStatus.clear();
    setTranscriptStatus(QString());
    m_activeStatuses.clear();
    m_usageBuckets.clear();
    m_usageStatus.clear();
    m_usageRev++;
    emit usageChanged();

    // Cached identity first (instant accent + gating), live ping after.
    applyCachedCaps(m_activeProfile);

    m_pingState = 0;
    m_pingInfo = QString();
    emit pingChanged();
    emit settingsChanged();

    m_statusSocket.configure(m_baseUrl, m_token);
    rebuildExtraStreams();
    updateWorkingSet();
    recomputeLiveStatus();
    if (configured())
        ping();
#endif
}

void ApiClient::openSessionRow(int profileIndex, const QString &sessionId)
{
    // Capture harness identity from the list row BEFORE switchProfile —
    // switching reloads m_sessions and can wipe the row we came from,
    // leaving m_sessionProvider empty so Session sheet falls back to the
    // multi-host union (effort picker etc. looks like Grok).
    QString rowProvider, rowModel, rowCwd;
    for (int i = 0; i < m_sessions.size(); ++i) {
        QVariantMap s = m_sessions.at(i).toMap();
        if (s.value("id").toString() == sessionId) {
            rowProvider = s.value("provider").toString().trimmed().toLower();
            rowModel = s.value("model").toString();
            rowCwd = s.value("cwd").toString();
            break;
        }
    }
    if (profileIndex >= 0 && profileIndex != m_activeProfile)
        switchProfile(profileIndex);
    openTranscript(sessionId);
    if (!rowProvider.isEmpty() && m_sessionProvider != rowProvider) {
        m_sessionProvider = rowProvider;
        if (!rowModel.isEmpty())
            m_sessionModel = rowModel;
        if (!rowCwd.isEmpty())
            m_sessionCwd = rowCwd;
        applyProviderTheme();
        emit capsChanged();
        emit currentSessionChanged();
    }
}

void ApiClient::applyCachedCaps(int index)
{
    QVariantMap prof = m_profiles.value(index).toMap();
    QVariantMap caps = prof.value("caps").toMap();
    if (!caps.isEmpty())
        updateCaps(caps);
    // Slash/model/effort lists are auth-gated and daemon-specific: the
    // previous daemon's must not gate (or offer) anything on this one.
    if (!m_slashCommands.isEmpty() || !m_models.isEmpty()
            || !m_efforts.isEmpty()) {
        m_slashCommands.clear();
        m_models.clear();
        m_efforts.clear();
        emit capsChanged();
    }
    setProvider(prof.value("provider").toString());
}

void ApiClient::setProvider(const QString &provider)
{
    if (provider == m_provider)
        return;
    m_provider = provider;
    // The whole accent family (accentColor/theme*/agentName) reads from
    // m_provider and notifies on capsChanged.
    emit capsChanged();
    applyProviderTheme();
}

void ApiClient::applyProviderTheme()
{
#ifdef VARIANT_UNIFIED
    // Recolor the OS chrome (title separator, TextField caret/selection) the
    // way main() does at startup - but per provider, on every switch/ping.
    bb::cascades::Application *app = bb::cascades::Application::instance();
    if (!app || !app->themeSupport())
        return;
    QColor a(accentColor());
    if (!a.isValid())
        return;
    const bb::cascades::Color brand =
        bb::cascades::Color::fromRGBA(a.redF(), a.greenF(), a.blueF(), 1.0f);
    // Both args on purpose: the caret follows primaryBase, and letting the
    // framework derive it leaves the cursor cyan (memory: setPrimaryColor).
    app->themeSupport()->setPrimaryColor(brand, brand);
#endif
}

void ApiClient::rebuildExtraStreams()
{
#ifdef VARIANT_UNIFIED
    for (int i = 0; i < m_extraStreams.size(); ++i)
        m_extraStreams.at(i)->deleteLater();
    m_extraStreams.clear();
    m_extraStatuses.clear();
    for (int i = 0; i < m_profiles.size(); ++i) {
        if (i == m_activeProfile)
            continue; // m_statusSocket already covers the active daemon
        if (!profileEnabled(i))
            continue;
        QVariantMap p = m_profiles.at(i).toMap();
        const QString base = normalizeBaseUrl(p.value("baseUrl").toString());
        const QString token = p.value("token").toString().trimmed();
        if (base.isEmpty() || token.isEmpty())
            continue;
        StatusSse *stream = new StatusSse(this);
        stream->setProperty("profileIndex", i);
        connect(stream, SIGNAL(textFrame(QByteArray)),
                this, SLOT(onExtraStatusFrame(QByteArray)));
        stream->configure(base, token);
        m_extraStreams.append(stream);
    }
#endif
}

void ApiClient::onExtraStatusFrame(const QByteArray &payload)
{
#ifdef VARIANT_UNIFIED
    QObject *stream = sender();
    if (!stream)
        return;
    const int profileIndex = stream->property("profileIndex").toInt();
    bool ok = false;
    QVariant data = parseBody(payload, &ok);
    if (!ok)
        return;
    m_extraStatuses[profileIndex] = data.toMap().value("active").toList();
    // Working markers / count only. Banner, doorbell and job adoption stay
    // active-profile concerns (onStatusFrame).
    updateWorkingSet();
#else
    Q_UNUSED(payload);
#endif
}

// Does this profile's daemon support Focus (cached from its ping)?
// The active profile also has the live m_capFocus, which is fresher.
// Rows currently in Focus. In Focus mode every listed row qualifies; in All
// mode only the flagged ones do, which is exactly the number worth advertising.
int ApiClient::focusCount() const
{
    int n = 0;
    for (int i = 0; i < m_sessions.size(); ++i) {
        if (m_sessions.at(i).toMap().value("focus").toBool())
            ++n;
    }
    return n;
}

bool ApiClient::profileSupportsFocus(int profileIndex) const
{
    if (profileIndex == m_activeProfile && m_capFocus)
        return true;
    if (profileIndex < 0 || profileIndex >= m_profiles.size())
        return false;
    return m_profiles.at(profileIndex).toMap().value("focus").toBool();
}

void ApiClient::pingProfiles()
{
#ifdef VARIANT_UNIFIED
    // /api/ping is unauthenticated and carries provider + caps: enough to
    // badge rows and pre-gate features for daemons we are not connected to.
    for (int i = 0; i < m_profiles.size(); ++i) {
        if (i == m_activeProfile)
            continue; // the active one goes through ping()
        if (!profileEnabled(i))
            continue;
        QVariantMap p = m_profiles.at(i).toMap();
        const QString base = normalizeBaseUrl(p.value("baseUrl").toString());
        if (base.isEmpty())
            continue;
        QNetworkReply *reply = getFrom(
            base, p.value("token").toString().trimmed(),
            QLatin1String("/api/ping"), QLatin1String("profilePing"));
        reply->setProperty("profileIndex", i);
    }
#endif
}

void ApiClient::annotateProviderRows(int profileIndex, const QString &provider)
{
    bool changed = false;
    for (int i = 0; i < m_sessions.size(); ++i) {
        QVariantMap s = m_sessions.at(i).toMap();
        if (!s.contains("profileIndex")
                || s.value("profileIndex").toInt() != profileIndex)
            continue;
        // Multi-harness hosts already tag each session; only fill empties.
        QString rowProv = s.value("provider").toString();
        if (rowProv.isEmpty())
            rowProv = provider;
        const QString accent = providerAccent(rowProv);
        if (s.value("provider").toString() == rowProv
                && s.value("accent").toString() == accent)
            continue;
        s["provider"] = rowProv;
        s["accent"] = accent;
        m_sessions.replace(i, s);
        changed = true;
    }
    if (changed) {
        m_sessionsRev++;
        emit sessionsChanged();
    }
}

// ---------------------------------------------------------------- pages

QObject *ApiClient::createPageFromAsset(const QString &asset)
{
    bb::cascades::QmlDocument *qml =
            bb::cascades::QmlDocument::create(asset).parent(this);
    if (!qml) {
        reportUiError(asset + ": QmlDocument::create failed");
        return 0;
    }
    if (qml->hasErrors()) {
        QString detail = asset + ":";
        const QList<QDeclarativeError> errs = qml->errors();
        for (int i = 0; i < errs.size(); ++i)
            detail += "\n" + errs.at(i).toString();
        reportUiError(detail);
        qml->deleteLater();
        return 0;
    }
    // Fallback for any bare `_api` left in the document; pages should use
    // their pinned `api` property (context lookups break after push/pop).
    qml->setContextProperty("_api", this);
    bb::cascades::Page *page = qml->createRootObject<bb::cascades::Page>();
    if (!page) {
        reportUiError(asset + ": createRootObject returned null");
        qml->deleteLater();
        return 0;
    }
    // Let page.destroy() (on pop) reclaim the document too.
    qml->setParent(page);
    return page;
}

QObject *ApiClient::createTranscriptPage()
{
    return createPageFromAsset("asset:///TranscriptPage.qml");
}

QObject *ApiClient::createProjectsPage()
{
    return createPageFromAsset("asset:///ProjectsPage.qml");
}

void ApiClient::reportUiError(const QString &message)
{
    m_sessionsStatus = message;
    emit sessionsChanged();
    appendLog("ERROR: " + message);
}

void ApiClient::copyToClipboard(const QString &text)
{
    // NOTE: do NOT clear() first. On BB10 the clipboard service processes
    // clear() and insert() asynchronously over PPS; the clear can land after
    // the insert and wipe the just-written slot, leaving an empty clipboard
    // ("Copy did nothing"). insert() already overwrites the text/plain slot.
    bb::system::Clipboard clipboard;
    const bool ok = clipboard.insert("text/plain", text.toUtf8());
    // Visible confirmation (also a diagnostic: no toast at all = the QML
    // handler never reached here, so the failure is in the item scope).
    bb::system::SystemToast *toast = new bb::system::SystemToast(this);
    toast->setBody(ok ? tr("Copied %1 chars").arg(text.size())
                      : tr("Copy failed"));
    QObject::connect(toast,
                     SIGNAL(finished(bb::system::SystemUiResult::Type)),
                     toast, SLOT(deleteLater()));
    toast->show();
    if (!ok)
        appendLog(QString("copyToClipboard: insert FAILED (%1 chars)")
                      .arg(text.size()));
}

// ------------------------------------------------- error / crash log

QString ApiClient::logFilePath()
{
    // Home is writable for sideloaded/devMode apps on BB10
    return QDir::homePath() + QLatin1String(BRAND_UI_ERROR_FILE);
}

QString ApiClient::crashFilePath()
{
    // Must match CrashGuard::install() ($HOME + BRAND_CRASH_FILE)
    return QDir::homePath() + QLatin1String(BRAND_CRASH_FILE);
}

QString ApiClient::crashPrevFilePath()
{
    return QDir::homePath() + QLatin1String(BRAND_CRASH_PREV_FILE);
}

void ApiClient::appendLog(const QString &line)
{
    const QString stamped =
        QDateTime::currentDateTime().toString(QLatin1String("hh:mm:ss"))
        + QLatin1String(" ") + line;
    if (!m_errorLog.isEmpty())
        m_errorLog += QLatin1Char('\n');
    m_errorLog += stamped;
    if (m_errorLog.size() > MAX_ERROR_LOG_CHARS)
        m_errorLog = m_errorLog.right(MAX_ERROR_LOG_CHARS - 1000);
    persistErrorLog();
    // Also scream to stderr -> device slog2 (visible via slog2info)
    fprintf(stderr, "%s: %s\n", BRAND_SETTINGS_APP,
            stamped.toLocal8Bit().constData());
    fflush(stderr);
    emit errorLogChanged();
}

void ApiClient::persistErrorLog()
{
    QFile f(logFilePath());
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate))
        f.write(m_errorLog.toUtf8());
}

void ApiClient::loadPersistedError()
{
    QFile f(logFilePath());
    if (f.open(QIODevice::ReadOnly))
        m_errorLog = QString::fromUtf8(f.readAll());
}

void ApiClient::adoptCrashDump()
{
    // CrashGuard wrote this during a fatal signal on a previous run.
    QFile f(crashFilePath());
    if (!f.exists())
        return;
    QByteArray dump;
    if (f.open(QIODevice::ReadOnly)) {
        dump = f.readAll();
        f.close();
    }
    if (dump.isEmpty()) {
        QFile::remove(crashFilePath());
        return;
    }
    // Keep the full dump on disk (rotated to -prev), show the tail in the UI.
    QFile::remove(crashPrevFilePath());
    QFile::rename(crashFilePath(), crashPrevFilePath());

    QString text = QString::fromUtf8(dump);
    if (text.size() > 2500)
        text = QLatin1String("...") + text.right(2500);
    if (!m_errorLog.isEmpty())
        m_errorLog += QLatin1Char('\n');
    m_errorLog += QLatin1String("===== CRASH DUMP (previous run) =====\n");
    m_errorLog += text;
    persistErrorLog();
    emit errorLogChanged();
}

void ApiClient::clearErrorLog()
{
    m_errorLog.clear();
    persistErrorLog();
    QFile::remove(crashFilePath());
    QFile::remove(crashPrevFilePath());
    emit errorLogChanged();
}

// ---------------------------------------------------------------- settings

void ApiClient::setPermissionMode(const QString &mode)
{
    const QString next = normalizeExecMode(mode);
    if (next.isEmpty() || next == m_permissionMode)
        return;
    m_permissionMode = next;
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("permissionMode", m_permissionMode);
    settings.sync();
    // Headless has no Live TUI menu slot — stop polling if we switched away.
    if (!interactiveMode() && m_tuiOpen)
        stopLiveTui();
    emit settingsChanged();
    emit currentSessionChanged();
}

void ApiClient::setModelOverride(const QString &model)
{
    QString m = model.trimmed();
    if (m.isEmpty())
        m = "default";
    if (m == m_modelOverride)
        return;
    m_modelOverride = m;
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("modelOverride", m_modelOverride);
    settings.sync();
    emit settingsChanged();
}

void ApiClient::setSoundCues(bool on)
{
    if (on == m_soundCues)
        return;
    m_soundCues = on;
    m_chime->setSoundEnabled(on);
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("soundCues", m_soundCues);
    settings.sync();
    emit settingsChanged();
}

void ApiClient::setFocusMode(bool on)
{
    if (on == m_focusMode)
        return;
    m_focusMode = on;
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("focusMode", m_focusMode);
    settings.sync();
    emit settingsChanged();
    // Focus and All come from different endpoints, so the list must refetch.
    refreshSessions();
}

void ApiClient::setLedCues(bool on)
{
    if (on == m_ledCues)
        return;
    m_ledCues = on;
    m_chime->setLedEnabled(on);
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("ledCues", m_ledCues);
    settings.sync();
    emit settingsChanged();
}

void ApiClient::setEffortOverride(const QString &effort)
{
    QString e = effort.trimmed();
    if (e.isEmpty())
        e = "default";
    if (e == m_effortOverride)
        return;
    m_effortOverride = e;
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("effortOverride", m_effortOverride);
    settings.sync();
    emit settingsChanged();
}

void ApiClient::resolvePermission(bool allow)
{
    if (m_permissionRequestId.isEmpty() || m_jobId.isEmpty())
        return;
    QVariantMap body;
    body["request_id"] = m_permissionRequestId;
    body["allow"] = allow;
    post(QString("/api/jobs/%1/permission").arg(m_jobId), body, "permission");
    // Optimistically clear; the next poll confirms via pending_permission.
    clearPendingPermission();
}

void ApiClient::resolveQuestion(const QVariantList &answers,
                                const QVariantList &notes)
{
    if (m_questionRequestId.isEmpty() || m_jobId.isEmpty())
        return;
    QVariantMap body;
    body["request_id"] = m_questionRequestId;
    body["answers"] = answers;
    bool anyNote = false;
    for (int i = 0; i < notes.size(); ++i) {
        if (!notes.at(i).toString().trimmed().isEmpty())
            anyNote = true;
    }
    if (anyNote)
        body["notes"] = notes;
    post(QString("/api/jobs/%1/question").arg(m_jobId), body, "question");
    clearPendingQuestion();
}

// The question sheet renders its body through the transcript's own pipeline
// (RichPaint -> PNG rows, Label rows when a paint fails).
QVariantList ApiClient::renderBlocks(const QVariantList &blocks)
{
    QVariantList out;
    if (!blocks.isEmpty())
        appendRenderedBlocks(out, blocks, false);
    return out;
}

void ApiClient::cancelQuestion()
{
    if (m_questionRequestId.isEmpty() || m_jobId.isEmpty())
        return;
    QVariantMap body;
    body["request_id"] = m_questionRequestId;
    body["cancel"] = true;
    post(QString("/api/jobs/%1/question").arg(m_jobId), body, "question");
    clearPendingQuestion();
}

void ApiClient::updateCaps(const QVariantMap &caps)
{
    if (caps.isEmpty())
        return;
    bool perms = caps.value("permissions",
                            QVariant(BRAND_DEFAULT_CAP_PERMISSIONS)).toBool();
    bool needsCwd = caps.value("requires_cwd",
                               QVariant(BRAND_DEFAULT_CAP_REQUIRES_CWD)).toBool();
    bool setModel = caps.value("can_set_model", QVariant(true)).toBool();
    bool setEffort = caps.value("can_set_effort", QVariant(false)).toBool();
    bool showUsage = caps.value("can_show_usage", QVariant(false)).toBool();
    bool interactive = caps.value("interactive", QVariant(false)).toBool();
    // live_tui is the explicit cap (daemon ≥ 2.4); older hosts still expose
    // interactive, which is enough to open the sheet (pane may be empty).
    bool liveTui = caps.value("live_tui", QVariant(interactive)).toBool();
    bool rewind = caps.value("rewind", QVariant(false)).toBool();
    if (perms == m_capPermissions && needsCwd == m_capRequiresCwd
            && setModel == m_capSetModel && setEffort == m_capSetEffort
            && showUsage == m_capShowUsage && interactive == m_capInteractive
            && liveTui == m_capLiveTui
            && rewind == m_capRewind)
        return;
    m_capPermissions = perms;
    m_capRequiresCwd = needsCwd;
    m_capSetModel = setModel;
    m_capSetEffort = setEffort;
    m_capShowUsage = showUsage;
    m_capInteractive = interactive;
    m_capLiveTui = liveTui;
    m_capRewind = rewind;
    emit capsChanged();
    emit currentSessionChanged(); // liveTuiEnabled may flip with caps
}

// ---------------------------------------------------------------- requests

QNetworkRequest ApiClient::makeRequest(const QString &pathAndQuery) const
{
    QUrl url(m_baseUrl + pathAndQuery);
    QNetworkRequest request(url);
    request.setRawHeader("X-Auth-Token", m_token.toUtf8());
    return request;
}

QNetworkReply *ApiClient::get(const QString &pathAndQuery, const QString &kind)
{
    QNetworkReply *reply = m_nam.get(makeRequest(pathAndQuery));
    reply->setProperty("kind", kind);
    const int timeoutMs = (kind == QLatin1String("search")) ? SEARCH_TIMEOUT_MS
            : (kind == QLatin1String("usage")) ? USAGE_TIMEOUT_MS
            : REQUEST_TIMEOUT_MS;
    QTimer::singleShot(timeoutMs, reply, SLOT(abort()));
    return reply;
}

QNetworkReply *ApiClient::post(const QString &path, const QVariantMap &body,
                               const QString &kind)
{
    QNetworkRequest request = makeRequest(path);
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");

    bb::data::JsonDataAccess jda;
    QByteArray payload;
    jda.saveToBuffer(body, &payload);

    QNetworkReply *reply = m_nam.post(request, payload);
    reply->setProperty("kind", kind);
    QTimer::singleShot(REQUEST_TIMEOUT_MS, reply, SLOT(abort()));
    return reply;
}

QVariant ApiClient::parseBody(const QByteArray &body, bool *ok) const
{
    bb::data::JsonDataAccess jda;
    QVariant data = jda.loadFromBuffer(QString::fromUtf8(body));
    *ok = !jda.hasError();
    return data;
}

QString ApiClient::httpErrorText(int httpStatus, const QVariant &data, bool parseOk,
                                 const QString &networkError) const
{
    if (httpStatus == 401)
        return tr("Auth failed - check the token in Settings");
    QString message = networkError;
    if (message.isEmpty() && parseOk)
        message = data.toMap()["error"].toString();
    if (message.isEmpty())
        message = tr("HTTP %1").arg(httpStatus);
    return message;
}

// ---------------------------------------------------------------- paint

QVariantMap ApiClient::renderRichBlock(const QString &rich, int widthPx,
                                       bool code, bool heading)
{
    if (!m_richPaint) {
        // No font inventory on the happy path: it was logged on every launch
        // (and mirrored to stderr/slog2), burying the errors the log exists
        // for. It is attached to the failure below instead, where it is the
        // thing you actually want to read.
        m_richPaint = new RichPaint(this);
    }
    const int w = widthPx > 0 ? widthPx : m_paintWidthBody;
    const int px = code ? m_fontCodePx
                        : (heading ? m_fontHeadingPx : m_fontBodyPx);
    const QString color = code
            ? QString("#6dce6d")
            : (heading ? QString(QLatin1String(BRAND_RICH_HEADING))
                       : QString("#d0d0d0"));
    QVariantMap r = m_richPaint->render(rich, w, px, code, color);
    if (!r.value("ok").toBool() && !m_richPaintWarned) {
        m_richPaintWarned = true;
        appendLog(QString("RichPaint fail (falls back to Labels): %1 [fonts: %2]")
                      .arg(r.value("err").toString())
                      .arg(m_richPaint->fontDebugInfo()));
    }
    return r;
}

// Rasterize one rich block; empty map = paint failed, use the Label row.
QVariantMap ApiClient::paintedItem(const QString &rich, const QString &plainText,
                                   bool code, bool heading, const QString &lang,
                                   bool live)
{
    if (rich.isEmpty())
        return QVariantMap();
    QString markup = rich;
    if (markup.indexOf(QLatin1Char('<')) >= 0)
        markup.replace(QLatin1String("\n"), QLatin1String("<br/>"));
    QVariantMap r = renderRichBlock(markup,
                                    code ? m_paintWidthCode : m_paintWidthBody,
                                    code, heading);
    if (!r.value("ok").toBool())
        return QVariantMap();
    QVariantMap item = blockItem("paintimg", plainText, QString(), live);
    item["imgPath"] = r.value("path");
    item["imgW"] = r.value("w");
    item["imgH"] = r.value("h");
    item["codeBg"] = code;
    item["lang"] = lang;
    return item;
}

// The daemon bakes Grok's TUI palette (WIRE_*) into every rich-text span;
// rewrite it to this variant's accents before the block is painted or shown
// as an Html fallback. Compiled out on the Grok build (palettes identical).
QString ApiClient::remapAccent(const QString &rich) const
{
#if defined(VARIANT_GROK)
    return rich;
#elif defined(VARIANT_UNIFIED)
    // The daemon always bakes Grok's TUI palette onto the wire. Remap to the
    // active provider's palette; for grok (and unknown) that is the wire
    // palette itself, so the replace would be a no-op - skip it.
    if (rich.isEmpty())
        return rich;
    const ProviderPalette *pal = palForProvider(m_provider);
    if (!pal || m_provider == QLatin1String("grok"))
        return rich;
    QString s = rich;
    s.replace(QLatin1String(WIRE_RICH_INLINE),
              QLatin1String(pal->inlineCode), Qt::CaseInsensitive);
    s.replace(QLatin1String(WIRE_RICH_HEADING),
              QLatin1String(pal->heading), Qt::CaseInsensitive);
    s.replace(QLatin1String(WIRE_META_THOUGHT),
              QLatin1String(pal->thought), Qt::CaseInsensitive);
    return s;
#else
    if (rich.isEmpty())
        return rich;
    QString s = rich;
    s.replace(QLatin1String(WIRE_RICH_INLINE),
              QLatin1String(BRAND_RICH_INLINE), Qt::CaseInsensitive);
    s.replace(QLatin1String(WIRE_RICH_HEADING),
              QLatin1String(BRAND_RICH_HEADING), Qt::CaseInsensitive);
    s.replace(QLatin1String(WIRE_META_THOUGHT),
              QLatin1String(BRAND_META_THOUGHT), Qt::CaseInsensitive);
    return s;
#endif
}

void ApiClient::appendRenderedBlocks(QVariantList &out, const QVariantList &blocks,
                                     bool live)
{
    for (int i = 0; i < blocks.size(); ++i) {
        QVariantMap b = blocks.at(i).toMap();
        QString k = b.value("k").toString();
        QString text = b.value("text").toString();
        QString rich = remapAccent(b.value("rich").toString());

        if (k == "gap" || k == "hr") {
            out.append(blockItem(k, QString(), QString(), live));
            continue;
        }
        if (k == "user") {
            // Plain body only - QML draws the left ">" chevron chrome so
            // the row matches the desktop chat bar (no timestamp).
            out.append(blockItem(k, text, QString(), live));
            continue;
        }
        if (k == "meta") {
            // Thought for / Worked for timing rows - small italic Label.
            QVariantMap item = blockItem("meta", text, rich, live);
            item["metaKind"] = b.value("metaKind").toString();
            out.append(item);
            continue;
        }
        if (k == "th" || k == "tr") {
            // Real table columns (GrokRemote's multi-column rows): pass the
            // daemon's per-cell fields straight through to the QML row.
            QVariantMap item = blockItem(k, text, rich, live);
            item["ncols"] = b.value("ncols");
            item["rowWidth"] = b.value("rowWidth");
            for (int c = 0; c < 6; ++c) {
                const QString n = QString::number(c);
                item["c" + n] = b.value("c" + n);
                item["c" + n + "r"] = remapAccent(b.value("c" + n + "r").toString());
                item["w" + n] = b.value("w" + n);
                item["hasC" + n] = b.value("hasC" + n);
            }
            out.append(item);
            continue;
        }
        if (k == "li") {
            // Paint the whole line with an inline bullet; the Label
            // fallback keeps the two-column hanging indent.
            QString prefix = b.value("prefix").toString().trimmed();
            if (prefix.isEmpty())
                prefix = QString::fromUtf8("\xE2\x80\xA2");
            if (text.isEmpty() && rich.isEmpty())
                continue;
            QVariantMap painted = paintedItem(
                    QString("<font color=\"#9a9a9a\">%1 </font>").arg(prefix) + rich,
                    text, false, false, QString(), live);
            if (!painted.isEmpty()) {
                out.append(painted);
                continue;
            }
            QVariantMap item = blockItem(k, text, rich, live);
            item["prefix"] = prefix;
            out.append(item);
            continue;
        }
        if (k == "img") {
            k = "p";
            rich = QString();
        } else if (k != "p" && k != "h" && k != "code") {
            k = "p"; // anything newer renders as body text
        }
        if (text.isEmpty() && rich.isEmpty())
            continue;

        // The paint pipeline (RichPaint -> PNG -> ImageView) is the one
        // render mode; a failed paint degrades to the Label row, never
        // breaks the transcript.
        QString lang = b.value("lang").toString();
        QVariantMap painted = paintedItem(rich, text, k == "code", k == "h",
                                          lang, live);
        if (!painted.isEmpty()) {
            out.append(painted);
            continue;
        }
        QVariantMap item = blockItem(k, text, rich, live);
        if (!lang.isEmpty())
            item["lang"] = lang;
        out.append(item);
    }
}

// A just-typed prompt, echoed live in the transcript.
static QVariantMap userLiveItem(const QString &text)
{
    QVariantMap item;
    item["kind"] = "user";
    item["text"] = text;
    item["rich"] = QString();
    item["live"] = true;
    item["rowId"] = ++s_userRowSeq;
    if (s_modelApi)
        item["api"] = QVariant::fromValue<QObject *>(s_modelApi);
    return item;
}

// A gap separates messages; the user prompt stands out via the QML chevron
// + dark well (desktop chat-bar style, no timestamp).
void ApiClient::appendMessageItemsFor(QVariantList &out, const QVariantMap &m)
{
    QString role = m.value("role").toString();
    if (!out.isEmpty())
        out.append(blockItem("gap", QString(), QString(), false));

    QVariantList blocks = m.value("blocks").toList();
    if (!blocks.isEmpty()) {
        appendRenderedBlocks(out, blocks, false);
    } else if (!m.value("text").toString().isEmpty()) {
        // Older daemon without a renderer: plain text block
        QString text = m.value("text").toString();
        if (role == "user")
            out.append(blockItem("user", text, QString(), false));
        else
            out.append(blockItem("p", text, QString(), false));
    }
    // Process view: the daemon attaches each step to the message it FOLLOWED,
    // so appending here keeps top-to-bottom the order it happened.
    QVariantList steps = m.value("steps").toList();
    if (!steps.isEmpty())
        appendStepItems(out, steps);
}

// ---------------------------------------------------------------- API calls

void ApiClient::ping()
{
    m_pingState = 1;
    m_pingInfo = QString();
    emit pingChanged();
    get("/api/ping", "ping");
}

void ApiClient::fetchUsage()
{
#ifdef VARIANT_UNIFIED
    // Every daemon's headroom in one sheet. Profiles whose cached ping says
    // "no usage" are skipped outright - an option that would fail is worse
    // than no option.
    m_usageGen++;
    m_usagePending = 0;
    m_usageByProfile.clear();
    m_usageErrors.clear();
    m_usageBuckets.clear();
    m_usageStatus = tr("Loading...");
    m_usageRev++;
    emit usageChanged();
    for (int i = 0; i < m_profiles.size(); ++i) {
        QString base, token;
        if (!profileEndpoint(i, &base, &token))
            continue;
        QVariantMap caps = m_profiles.at(i).toMap().value("caps").toMap();
        if (!caps.isEmpty() && !caps.value("can_show_usage").toBool())
            continue;
        QNetworkReply *reply = getFrom(base, token,
                                       QLatin1String("/api/usage"),
                                       QLatin1String("uusage"));
        reply->setProperty("profileIndex", i);
        reply->setProperty("usageGen", m_usageGen);
        m_usagePending++;
    }
    if (m_usagePending == 0) {
        m_usageStatus = tr("No daemon reports usage");
        m_usageRev++;
        emit usageChanged();
    }
    return;
#endif
    if (!configured())
        return;
    m_usageStatus = tr("Loading...");
    emit usageChanged();
    get("/api/usage", "usage");
}

void ApiClient::openUsageInBrowser()
{
    const QString url = QLatin1String(BRAND_USAGE_URL);
    if (url.isEmpty())
        return;
    // No explicit target: the invocation framework routes bb.action.OPEN
    // for https:// to the system browser (same pattern as mdreader / Flipper).
    static bb::system::InvokeManager manager;
    bb::system::InvokeRequest request;
    request.setAction("bb.action.OPEN");
    request.setUri(QUrl(url));
    manager.invoke(request);
}

void ApiClient::fetchProjects()
{
    m_projectsStatus = tr("Loading...");
    emit projectsChanged();
    get("/api/projects", "projects");
}

void ApiClient::refreshSessions()
{
    if (!configured())
        return;
    // Keep the user in search results across foreground refreshes / project
    // filter changes - only re-run the active query, don't drop it.
    if (!m_searchQuery.isEmpty()) {
        searchSessions(m_searchQuery);
        return;
    }
#ifdef VARIANT_UNIFIED
    startUnifiedFetch(QString());
    return;
#endif
    m_sessionsStatus = tr("Loading...");
    emit sessionsChanged();
    // Focus mode asks the daemon for the rows instead of filtering the
    // session list here: a project untouched for weeks falls outside the
    // recency window, and that is exactly the row that must not be lost.
    if (m_focusMode && m_capFocus) {
        get(QLatin1String("/api/focus"), "sessions");
        return;
    }
    QString path = QString("/api/sessions?limit=%1").arg(PAGE_SIZE);
    if (!m_projectFilter.isEmpty())
        path += "&project=" + QString::fromUtf8(QUrl::toPercentEncoding(m_projectFilter));
    get(path, "sessions");
}

void ApiClient::scheduleSearchSessions(const QString &query)
{
    // TextField fires textChanging on every key. Empty = leave search mode
    // immediately (restore the normal list); non-empty waits 50ms after the
    // last key so a fast typist only pays for one request.
    m_pendingSearchQuery = query;
    if (query.trimmed().isEmpty()) {
        m_searchDebounce.stop();
        searchSessions(QString());
        return;
    }
    m_searchDebounce.start();
}

void ApiClient::runPendingSearch()
{
    searchSessions(m_pendingSearchQuery);
}

void ApiClient::searchSessions(const QString &query)
{
    if (!configured())
        return;
    const QString q = query.trimmed();
    if (q.isEmpty()) {
        // Leaving search mode: drop pending debounce, clear highlighted
        // results immediately, then reload the normal sessions list.
        m_searchDebounce.stop();
        m_pendingSearchQuery.clear();
        m_searchQuery.clear();
        m_sessions.clear();
        m_sessionsStatus = tr("Loading...");
        m_sessionsRev++;
        emit sessionsChanged();
#ifdef VARIANT_UNIFIED
        startUnifiedFetch(QString());
        return;
#endif
        QString path = QString("/api/sessions?limit=%1").arg(PAGE_SIZE);
        if (!m_projectFilter.isEmpty())
            path += "&project=" + QString::fromUtf8(QUrl::toPercentEncoding(m_projectFilter));
        get(path, "sessions");
        return;
    }
    // Skip a no-op re-issue of the same active query (e.g. debounce fires
    // twice for an unchanged field after a focus glitch).
    if (q == m_searchQuery && m_sessionsStatus != tr("Searching...")
            && m_sessionsStatus != tr("Loading..."))
        return;
    m_searchQuery = q;
    m_sessionsStatus = tr("Searching...");
    emit sessionsChanged();
#ifdef VARIANT_UNIFIED
    startUnifiedFetch(q);
    return;
#endif
    QString path = QString("/api/sessions/search?q=%1&limit=%2")
            .arg(QString::fromUtf8(QUrl::toPercentEncoding(q)))
            .arg(PAGE_SIZE);
    if (!m_projectFilter.isEmpty())
        path += "&project=" + QString::fromUtf8(QUrl::toPercentEncoding(m_projectFilter));
    QNetworkReply *reply = get(path, "search");
    // Tag so a late reply for an older query cannot clobber newer results
    // (or a clear-back-to-list).
    reply->setProperty("searchQuery", q);
}

QNetworkReply *ApiClient::getFrom(const QString &baseUrl, const QString &token,
                                  const QString &pathAndQuery, const QString &kind)
{
    // Like get(), but against an explicit daemon - the unified fan-out
    // queries every profile, not just the active connection.
    QUrl url(baseUrl + pathAndQuery);
    QNetworkRequest request(url);
    request.setRawHeader("X-Auth-Token", token.toUtf8());
    QNetworkReply *reply = m_nam.get(request);
    reply->setProperty("kind", kind);
    // Downloads get the download budget, usage the TUI-scrape budget;
    // everything else the generous fan-out one (a cross-file search on a
    // slow VPS takes a while).
    const int timeoutMs = (kind == QLatin1String("drop_dl"))
            ? DOWNLOAD_TIMEOUT_MS
            : (kind == QLatin1String("uusage"))
              ? USAGE_TIMEOUT_MS : SEARCH_TIMEOUT_MS;
    QTimer::singleShot(timeoutMs, reply, SLOT(abort()));
    return reply;
}

QNetworkReply *ApiClient::postTo(const QString &baseUrl, const QString &token,
                                 const QString &path, const QVariantMap &body,
                                 const QString &kind)
{
    QUrl url(baseUrl + path);
    QNetworkRequest request(url);
    request.setRawHeader("X-Auth-Token", token.toUtf8());
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    bb::data::JsonDataAccess jda;
    QByteArray payload;
    jda.saveToBuffer(body, &payload);
    QNetworkReply *reply = m_nam.post(request, payload);
    reply->setProperty("kind", kind);
    QTimer::singleShot(REQUEST_TIMEOUT_MS, reply, SLOT(abort()));
    return reply;
}

// A profile the user switched off stays configured but is never contacted:
// no status socket, no ping, no session/drop/usage fan-out. Absent key =
// enabled, so profiles saved before this existed keep working.
bool ApiClient::profileEnabled(int profileIndex) const
{
    if (profileIndex < 0 || profileIndex >= m_profiles.size())
        return false;
    return m_profiles.at(profileIndex).toMap()
            .value("enabled", QVariant(true)).toBool();
}

void ApiClient::setProfileEnabled(int index, bool on)
{
    if (index < 0 || index >= m_profiles.size())
        return;
    QVariantMap p = m_profiles.at(index).toMap();
    if (p.value("enabled", QVariant(true)).toBool() == on)
        return;
    int nextActive = m_activeProfile;
    if (!on && index == m_activeProfile) {
        // Something has to serve the open UI: hand over to the first other
        // enabled profile, and refuse to switch this one off if there is none.
        nextActive = -1;
        for (int i = 0; i < m_profiles.size(); ++i) {
            if (i != index && profileEnabled(i)) {
                nextActive = i;
                break;
            }
        }
        if (nextActive < 0) {
            setTranscriptStatus(tr("Can't disable the only enabled profile."));
            return;
        }
    }
    p["enabled"] = on;
    m_profiles.replace(index, p);
    const bool switched = nextActive != m_activeProfile;
    m_activeProfile = nextActive;
    persistProfiles();
    emit profilesChanged();
    if (switched)
        applyActiveProfile(true);
    else
        refreshSessions();
}

bool ApiClient::profileEndpoint(int profileIndex, QString *baseUrl,
                                QString *token) const
{
    if (!profileEnabled(profileIndex))
        return false;
    QVariantMap p = m_profiles.value(profileIndex).toMap();
    const QString base = normalizeBaseUrl(p.value("baseUrl").toString());
    const QString tok = p.value("token").toString().trimmed();
    if (base.isEmpty() || tok.isEmpty())
        return false;
    *baseUrl = base;
    *token = tok;
    return true;
}

void ApiClient::startUnifiedFetch(const QString &query)
{
    // Every (re)start invalidates in-flight replies of the previous one:
    // they land, see a stale generation, and are dropped.
    m_unifiedGen++;
    m_unifiedPending = 0;
    m_unifiedRows.clear();
    m_unifiedErrors.clear();
    m_sessionsStatus = query.isEmpty() ? tr("Loading...") : tr("Searching...");
    emit sessionsChanged();

    for (int i = 0; i < m_profiles.size(); ++i) {
        if (!profileEnabled(i))
            continue;
        QVariantMap p = m_profiles.at(i).toMap();
        const QString base = normalizeBaseUrl(p.value("baseUrl").toString());
        const QString token = p.value("token").toString().trimmed();
        if (base.isEmpty() || token.isEmpty())
            continue;
        // A project filter names one daemon's project id - only its owner
        // (the profile that was active when it was picked) can answer it.
        if (!m_projectFilter.isEmpty() && i != m_activeProfile)
            continue;
        const bool focusHere = m_focusMode && query.isEmpty()
                && profileSupportsFocus(i);
        // A daemon too old for Focus contributes nothing in Focus mode:
        // sending its whole session list would silently fill Focus with
        // sessions the human never enrolled.
        if (m_focusMode && query.isEmpty() && !focusHere)
            continue;
        QString path = focusHere
                ? QString("/api/focus")
                : (query.isEmpty()
                   ? QString("/api/sessions?limit=%1").arg(PAGE_SIZE)
                   : QString("/api/sessions/search?q=%1&limit=%2")
                         .arg(QString::fromUtf8(QUrl::toPercentEncoding(query)))
                         .arg(PAGE_SIZE));
        if (!m_projectFilter.isEmpty() && !focusHere)
            path += "&project="
                    + QString::fromUtf8(QUrl::toPercentEncoding(m_projectFilter));
        QNetworkReply *reply = getFrom(base, token, path, "usessions");
        reply->setProperty("profileIndex", i);
        reply->setProperty("unifiedGen", m_unifiedGen);
        reply->setProperty("searchQuery", query);
        m_unifiedPending++;
    }

    if (m_unifiedPending == 0) {
        m_sessions.clear();
        m_sessionsStatus = tr("No configured profiles - add one in Settings");
        m_sessionsRev++;
        emit sessionsChanged();
    }
}

void ApiClient::finishUnifiedFetch()
{
    qStableSort(m_unifiedRows.begin(), m_unifiedRows.end(), sessionRowNewer);
    m_sessions = m_unifiedRows;
    m_unifiedRows.clear();
    annotateWorkingSessions(false);

    if (m_sessions.isEmpty()) {
        m_sessionsStatus = m_searchQuery.isEmpty()
                ? tr("No sessions found")
                : tr("No matches for \"%1\"").arg(m_searchQuery);
    } else if (!m_searchQuery.isEmpty()) {
        m_sessionsStatus = tr("%1 match(es) for \"%2\"")
                .arg(m_sessions.size()).arg(m_searchQuery);
    } else {
        m_sessionsStatus = QString();
    }
    // An unreachable profile is routine here - a laptop asleep, a VPS behind
    // a flaky link - and Agent Remote sweeps every profile, so those errors
    // used to spam the home screen with HTTP text on every refresh. Stay
    // quiet while ANY daemon answered; the failures are still reported where
    // they are actionable (Settings > Test connection).
    //
    // Only when nothing answered at all is silence wrong: the list would read
    // as "no sessions" when really nothing was asked. Say that in one neutral
    // line instead of pasting the per-profile errors.
    if (!m_unifiedErrors.isEmpty() && m_sessions.isEmpty()) {
        m_sessionsStatus = m_searchQuery.isEmpty()
                ? tr("No daemon reachable")
                : tr("No daemon reachable - can't search");
    }
    m_sessionsRev++;
    emit sessionsChanged();
}

void ApiClient::setProjectFilter(const QString &projectId, const QString &name)
{
    m_projectFilter = projectId;
    m_projectFilterName = name;
    emit filterChanged();
    refreshSessions();
}

void ApiClient::requestNewSession(const QString &cwd, const QString &projectName)
{
    m_newSessionCwd = cwd;
    m_newSessionProjectName = projectName;
    m_newSessionRequestRev++;
    emit newSessionRequestChanged();
}

void ApiClient::openTranscript(const QString &sessionId)
{
    // Detach the previous session's job UI first: only the OPEN session's
    // job may ever drive the banner/ticker/queue (parallel sessions).
    m_pollTimer.stop();
    m_pollInFlight = false;
    m_awaitingJob = false;
    m_jobId.clear();
    m_since = 0;
    m_jobStartMs = 0;
    m_jobToolLine.clear();
    setJobTicker(QString());
    clearPendingPermission();
    m_jobEndStatus.clear();
    if (!m_queued.isEmpty()) {
        m_queued.clear();
        emit queueChanged();
    }

    m_currentSessionId = sessionId;
    // Model + cwd + provider from the list row we came from. cwd is where
    // "!cmd" shell escapes run on the daemon. Prefer preserving a provider
    // already set by openSessionRow (before switchProfile wiped the list).
    const QString keepProvider = m_sessionProvider;
    m_sessionModel.clear();
    m_sessionCwd.clear();
    m_sessionProvider.clear();
    for (int i = 0; i < m_sessions.size(); ++i) {
        QVariantMap s = m_sessions.at(i).toMap();
        if (s.value("id").toString() == sessionId) {
            m_sessionModel = s.value("model").toString();
            m_sessionCwd = s.value("cwd").toString();
            // The row's own provider, not the active profile's: the unified
            // list merges daemons, so the open session may belong to another
            // one — that is what made the banner read "Agent is ...".
            m_sessionProvider = s.value("provider").toString().trimmed().toLower();
            break;
        }
    }
    if (m_sessionProvider.isEmpty() && !keepProvider.isEmpty())
        m_sessionProvider = keepProvider.trimmed().toLower();
    // Multi-host profiles keep a neutral chrome on the list; once a session
    // is open, recolor to that harness (Grok cyan banner, Claude orange, …).
    if (!m_sessionProvider.isEmpty()) {
        applyProviderTheme();
        emit capsChanged(); // themeBannerBg / accentColor rebind in QML
    }
    emit currentSessionChanged();
    m_messages.clear();
    m_earliestOffset = 0;
    bumpMessages(true);

    // Re-attach to this session's job: what we started ourselves, or -
    // after an app restart / job started elsewhere - whatever the daemon's
    // status stream says is running for this session.
    QString jobId = m_sessionJobs.value(sessionId);
    if (isSyntheticJobId(jobId)) {
        m_sessionJobs.remove(sessionId);
        jobId.clear();
    }
    if (jobId.isEmpty()) {
        for (int i = 0; i < m_activeStatuses.size(); ++i) {
            QVariantMap s = m_activeStatuses.at(i).toMap();
            if (s.value("session_id").toString() == sessionId
                    || s.value("new_session_id").toString() == sessionId) {
                const QString candidate = s.value("job_id").toString();
                // Prefer a real job; synthetic tui-* only paints "working".
                if (!candidate.isEmpty() && !isSyntheticJobId(candidate)) {
                    jobId = candidate;
                    break;
                }
            }
        }
    }
    if (!jobId.isEmpty() && !isSyntheticJobId(jobId)) {
        m_sessionJobs[sessionId] = jobId;
        attachToJob(jobId);
    } else {
        emit jobRunningChanged();
    }

    recomputeLiveStatus();
    refreshTranscript();
}

// Forget what the status stream said about the previous tracked job; until
// a frame mentions the new one, pollJob() falls back to plain polling.
void ApiClient::resetWsJobState()
{
    m_wsNextSeq = -1;
    m_wsFrameMs = 0;
    m_wsJobSeen = false;
    m_wsDoorbell = false;
    m_wsQueuedCount = 0;
    m_wsPendingPerm = false;
}

bool ApiClient::isSyntheticJobId(const QString &jobId)
{
    // Matches daemon active_tui_status: "tui-%s" % sid_without_dashes[:12]
    return jobId.startsWith(QLatin1String("tui-"));
}

void ApiClient::attachToJob(const QString &jobId)
{
    if (jobId.isEmpty() || isSyntheticJobId(jobId))
        return;
    m_jobId = jobId;
    m_since = 0;
    m_pollFailures = 0;
    m_pollInFlight = false;
    resetWsJobState();
    m_jobStartMs = QDateTime::currentMSecsSinceEpoch();
    m_jobToolLine.clear();
    setJobTicker(workingLine());
    m_pollTimer.start();
    emit jobRunningChanged();
}

void ApiClient::refreshTranscript()
{
    if (m_currentSessionId.isEmpty())
        return;
    setTranscriptStatus(tr("Loading..."));
    fetchMessages(-1, INITIAL_PAGE_SIZE, false);
}

void ApiClient::loadOlder()
{
    if (m_currentSessionId.isEmpty() || m_earliestOffset <= 0)
        return;
    if (m_loadingOlder)
        return;   // a page is already in flight; ignore repeat taps
    int offset = m_earliestOffset - PAGE_SIZE;
    if (offset < 0)
        offset = 0;
    setLoadingOlder(true);
    fetchMessages(offset, m_earliestOffset - offset, true);
}

void ApiClient::setLoadingOlder(bool v)
{
    if (m_loadingOlder == v)
        return;
    m_loadingOlder = v;
    emit loadingOlderChanged();
}

void ApiClient::fetchMessages(int offset, int limit, bool older)
{
    QString path = QString("/api/sessions/%1/messages?limit=%2")
            .arg(m_currentSessionId).arg(limit);
    if (offset >= 0)
        path += QString("&offset=%1").arg(offset);
    // Process view: each message also carries the working steps that
    // followed it. Off = byte-identical to the pre-steps response.
    if (processView())
        path += QLatin1String("&detail=steps");
    QNetworkReply *reply = get(path, "messages");
    reply->setProperty("sid", m_currentSessionId);
    reply->setProperty("older", older);
    // Stamp the send time so onFinished can report round-trip latency.
    reply->setProperty("t_send", QDateTime::currentMSecsSinceEpoch());
}

// Long-press "Rewind to here" on a user prompt: the daemon's /rewind N
// restores to before the Nth-last user message, so N = how many user rows
// sit at or after this one. The daemon (>= 2.5) edits the harness's own
// session journal, so this works in headless AND interactive execution.
//
// Destructive, so the gesture only STAGES the rewind: the page raises a
// confirmation dialog off the rewindConfirmRev pin, and confirmRewind()
// performs it.
void ApiClient::rewindToRow(int rowId)
{
    if (!sessionCanRewind()) {
        setTranscriptStatus(m_sessionProvider.isEmpty()
                ? tr("This daemon can't rewind (needs 2.5+).")
                : tr("%1 can't rewind (needs daemon 2.5+).").arg(agentName()));
        return;
    }
    int back = 0;
    for (int i = m_messages.size() - 1; i >= 0; --i) {
        QVariantMap item = m_messages.at(i).toMap();
        if (item.value("kind").toString() != QLatin1String("user"))
            continue;
        ++back;
        if (item.value("rowId").toInt() == rowId) {
            if (jobRunning()) {
                setTranscriptStatus(
                        tr("Finish or stop the turn before rewinding."));
                return;
            }
            if (m_currentSessionId.isEmpty())
                return;
            QString quote = item.value("text").toString();
            if (quote.startsWith(QLatin1String("> ")))
                quote = quote.mid(2);
            quote = quote.section(QLatin1Char('\n'), 0, 0).left(120);
            m_rewindConfirmSteps = back;
            m_rewindConfirmText =
                (back == 1
                     ? tr("The conversation goes back to just before this "
                          "message, dropping your last message and the "
                          "reply to it.")
                     : tr("The conversation goes back to just before this "
                          "message, dropping the last %1 of your messages "
                          "and everything after them.").arg(back))
                + tr("\n\nThis cannot be undone. Conversation only - file "
                     "changes on the host are not reverted.")
                + QString("\n\n“%1”").arg(quote);
            m_rewindConfirmRev++;
            emit rewindConfirmChanged();
            return;
        }
    }
    setTranscriptStatus(tr("Can't rewind: message not found."));
}

// The confirmation dialog's Rewind button. Re-checks the guards - the world
// may have moved on while the dialog sat open.
void ApiClient::confirmRewind()
{
    const int back = m_rewindConfirmSteps;
    m_rewindConfirmSteps = 0;
    if (back <= 0 || m_currentSessionId.isEmpty())
        return;
    if (jobRunning()) {
        setTranscriptStatus(tr("Finish or stop the turn before rewinding."));
        return;
    }
    // NOT sendPrompt(): that routes a mid-turn message into the TUI (or,
    // before the job id lands, into the local pending queue, where it sat
    // silently and nothing ever happened). A rewind is a control action -
    // it has to run as its own turn.
    const QString p = QString("/rewind %1").arg(back);
    m_messages.append(blockItem("gap", QString(), QString(), true));
    appendLiveItem(userLiveItem(p));
    m_jobEndStatus.clear();
    setTranscriptStatus(QString());
    postPrompt(p);
}

void ApiClient::sendPrompt(const QString &prompt)
{
    QString p = prompt.trimmed();
    if (p.isEmpty())
        return;

    // Shell escape: "!command" runs on the daemon in the open session's
    // project folder, then feeds the output into the conversation so the
    // AI can see it (with a silent directive - no reply expected).
    if (p.startsWith(QLatin1Char('!'))) {
        QString cmd = p.mid(1).trimmed();
        if (!cmd.isEmpty() && !m_currentSessionId.isEmpty()) {
            m_messages.append(blockItem("gap", QString(), QString(), true));
            appendLiveItem(userLiveItem(p));
            setTranscriptStatus(tr("Running..."));
            QVariantMap body;
            body["command"] = cmd;
            // Always send session_id so the daemon can resolve cwd even if
            // the phone's cached m_sessionCwd is empty (e.g. after restart).
            body["session_id"] = m_currentSessionId;
            if (!m_sessionCwd.isEmpty())
                body["cwd"] = m_sessionCwd;
            QNetworkReply *reply = post("/api/shell", body, "shell");
            reply->setProperty("cmd", cmd);
            reply->setProperty("sid", m_currentSessionId);
        }
        return;
    }

    // A slash command the daemon doesn't know wastes a whole HEADLESS turn -
    // the CLIs only run TUI commands interactively (grok writes an essay
    // about it, claude -p errors "Unknown skill"). Validate the first token
    // against the daemon's list before sending anything. Things like
    // "/root/x is missing" don't look like a command and pass through.
    // There is no hardcoded whitelist any more: the daemon advertises each
    // harness's real built-ins (claude/grok/codex: /compact /exit /rewind —
    // /rewind is served by the daemon itself, so it works headless too), so
    // anything not on the OPEN session's list is refused here rather than
    // wasting a turn on a command the harness would not understand.
    if (p.startsWith(QLatin1Char('/'))) {
        QString cmd = p.section(QRegExp("\\s"), 0, 0);
        const QStringList allowed = sessionSlashCommands();
        if (QRegExp("/[A-Za-z][A-Za-z0-9_-]*").exactMatch(cmd)
                && !allowed.contains(cmd)) {
            setTranscriptStatus(allowed.isEmpty()
                ? tr("%1 can't run slash commands from here").arg(agentName())
                : tr("%1 doesn't have %2 - available: %3")
                      .arg(agentName(), cmd, allowed.join(" ")));
            return;
        }
    }

    // While the open session's job runs, prompts queue *on the daemon*
    // (attached to the job): the phone dying or losing Wi-Fi must not lose
    // them. The tiny window before the job id arrives buffers locally and
    // flushes when the "continue" response lands.
    //
    // Synthetic tui-* ids (busy host TUI after a real job expired) are NOT
    // jobs — treat them as idle and fall through to postPrompt/continue so
    // the daemon actually receives the text.
    if (jobRunning() && !isSyntheticJobId(m_jobId)) {
        // Interactive mode has no daemon queue: the hosted TUI owns one, so
        // the message is typed straight into its input and runs there when
        // the current turn ends (the job stays on watch, so the reply still
        // streams here). Everything else queues on the daemon.
        if (interactiveMode()) {
            m_messages.append(blockItem("gap", QString(), QString(), true));
            appendLiveItem(userLiveItem(p));
            if (!m_jobId.isEmpty()) {
                postDirectInput(m_jobId, p);
            } else {
                // The job id has not landed yet. Buffering is right, but say
                // so — this used to be silent, so a send that never left the
                // phone looked exactly like a daemon that ignored it.
                m_pendingQueue[m_currentSessionId].append(p);
                emit queueChanged();
                setTranscriptStatus(tr("Waiting for the turn to start..."));
            }
            return;
        }
        if (queuedCount() >= MAX_QUEUED_PROMPTS)
            return;
        m_messages.append(blockItem("gap", QString(), QString(), true));
        appendLiveItem(userLiveItem(p));
        if (!m_jobId.isEmpty()) {
            postQueuePrompt(m_jobId, p);
        } else {
            m_pendingQueue[m_currentSessionId].append(p);
            emit queueChanged();
        }
        return;
    }
    // Stale synthetic attachment (older builds / race): drop it so we do not
    // keep looking "busy" with no real job to send into.
    if (isSyntheticJobId(m_jobId)) {
        m_jobId.clear();
        m_pollTimer.stop();
        m_awaitingJob = false;
        setJobTicker(QString());
        emit jobRunningChanged();
    }

    if (m_currentSessionId.isEmpty())
        return;
    m_messages.append(blockItem("gap", QString(), QString(), true));
    appendLiveItem(userLiveItem(p));
    m_jobEndStatus.clear();
    setTranscriptStatus(QString());
    postPrompt(p);
}

void ApiClient::postPrompt(const QString &prompt)
{
    m_awaitingJob = true;
    emit jobRunningChanged();
    QVariantMap body;
    body["prompt"] = prompt;
    body["permission_mode"] = wireExecMode(m_permissionMode);
    body["model"] = m_modelOverride;
    body["effort"] = m_effortOverride;
    QNetworkReply *reply = post(
            QString("/api/sessions/%1/continue").arg(m_currentSessionId),
            body, "continue");
    reply->setProperty("sid", m_currentSessionId);
}

// Interactive mode: hand the message to the TUI's own input instead of the
// daemon queue. A 409 means the TUI went away - surface it, don't lose the
// text silently.
void ApiClient::postDirectInput(const QString &jobId, const QString &prompt)
{
    QVariantMap body;
    body["prompt"] = prompt;
    QNetworkReply *reply =
            post(QString("/api/jobs/%1/input").arg(jobId), body, "input");
    reply->setProperty("prompt", prompt);
}

void ApiClient::postQueuePrompt(const QString &jobId, const QString &prompt)
{
    QVariantMap body;
    body["prompt"] = prompt;
    QNetworkReply *reply =
            post(QString("/api/jobs/%1/queue").arg(jobId), body, "queue");
    reply->setProperty("prompt", prompt);
    reply->setProperty("jobId", jobId);
}

void ApiClient::cancelQueued(const QString &queueId)
{
    if (m_jobId.isEmpty() || queueId.isEmpty())
        return;
    post(QString("/api/jobs/%1/queue/%2/cancel").arg(m_jobId, queueId),
         QVariantMap(), "qcancel");
}

void ApiClient::updateQueueFromServer(const QVariantList &queued)
{
    if (m_queued == queued)
        return;
    m_queued = queued;
    emit queueChanged();
}

// Remove the optimistic user-prompt echo of a canceled queued message.
void ApiClient::removeQueuedEcho(const QString &prompt)
{
    // Match plain body (current) or legacy "> body" rows from older builds.
    const QString needle = prompt;
    const QString legacy = QLatin1String("> ") + prompt;
    for (int i = m_messages.size() - 1; i >= 0; --i) {
        QVariantMap item = m_messages.at(i).toMap();
        const QString text = item.value("text").toString();
        if (item.value("kind").toString() == "user"
                && item.value("live").toBool()
                && (text == needle || text == legacy)) {
            m_messages.removeAt(i);
            // Its leading gap spacer goes with it.
            if (i > 0 && m_messages.at(i - 1).toMap()
                    .value("kind").toString() == "gap")
                m_messages.removeAt(i - 1);
            bumpMessages(false);
            return;
        }
    }
}

QString ApiClient::dropQueueNote(int droppedQueued)
{
    QStringList &pending = m_pendingQueue[m_currentSessionId];
    int n = droppedQueued + pending.size() + m_queued.size();
    if (!pending.isEmpty() || !m_queued.isEmpty()) {
        pending.clear();
        m_queued.clear();
        emit queueChanged();
    }
    if (n <= 0)
        return QString();
    return tr(" - %1 queued message(s) dropped").arg(n);
}

void ApiClient::startNewSession(const QString &cwd, const QString &prompt,
                                const QString &provider)
{
    // m_awaitingJob only guards the double-tap window while the previous
    // POST is in flight; a *running* job is no obstacle - the daemon runs
    // jobs concurrently, so just stop tracking the old one.
    // cwd may be empty when the daemon doesn't require one (grok falls
    // back to its workspace).
    if (m_awaitingJob || prompt.trimmed().isEmpty())
        return;
    const QString harness = provider.trimmed().toLower();
    const bool needsCwd = harness.isEmpty()
            ? m_capRequiresCwd
            : harnessRequiresCwd(harness);
    if (cwd.isEmpty() && needsCwd)
        return;
    detachJob();
    m_currentSessionId = QString();
    m_sessionCwd = cwd;
    if (!harness.isEmpty()) {
        m_sessionProvider = harness;
        setProvider(harness);
    }
    emit currentSessionChanged();
    m_messages.clear();
    m_earliestOffset = 0;
    appendLiveItem(userLiveItem(prompt));
    m_jobEndStatus.clear();
    setTranscriptStatus(QString());
    m_awaitingJob = true;
    emit jobRunningChanged();
    QVariantMap body;
    body["cwd"] = cwd;
    body["prompt"] = prompt;
    body["permission_mode"] = wireExecMode(m_permissionMode);
    body["model"] = m_modelOverride;
    body["effort"] = m_effortOverride;
    // Multi-harness root routes by provider; single-provider daemons ignore it.
    if (!harness.isEmpty())
        body["provider"] = harness;
    QNetworkReply *reply = post("/api/sessions/new", body, "continue");
    reply->setProperty("sid", QString());
}

// Stop tracking the current job *without* stopping it on the daemon -
// it keeps running (and chaining its queue) server-side; the /ws/status
// banner still reports it when its session is open.
void ApiClient::detachJob()
{
    if (m_jobId.isEmpty() && !m_pollTimer.isActive())
        return;
    m_pollTimer.stop();
    m_jobId.clear();
    m_since = 0;
    m_pollInFlight = false;
    m_pollFailures = 0;
    m_jobStartMs = 0;
    m_jobToolLine.clear();
    setJobTicker(QString());
    clearPendingPermission();
    if (!m_queued.isEmpty() || !m_pendingQueue.value(m_currentSessionId).isEmpty()) {
        // Only the local mirror: the daemon keeps and runs its queue.
        m_queued.clear();
        m_pendingQueue[m_currentSessionId].clear();
        emit queueChanged();
    }
    emit jobRunningChanged();
}

void ApiClient::stopJob()
{
    // Stop means stop: the daemon drops its queue with the job; drop the
    // not-yet-flushed local buffer too.
    QStringList &pending = m_pendingQueue[m_currentSessionId];
    if (!pending.isEmpty()) {
        pending.clear();
        emit queueChanged();
    }
    if (m_jobId.isEmpty())
        return;
    post(QString("/api/jobs/%1/stop").arg(m_jobId), QVariantMap(), "stop");
}

// ------------------------------------------------------------- attachments

// ---------------------------------------------------------------- Live TUI

void ApiClient::startLiveTui()
{
    if (m_currentSessionId.isEmpty()) {
        m_tuiText = tr("Open a session first.");
        m_tuiStatus = tr("No session");
        m_tuiAttached = false;
        m_tuiLive = false;
        m_tuiRev++;
        emit tuiChanged();
        return;
    }
    m_tuiOpen = true;
    m_tuiInFlight = false;
    m_tuiSeq = 0;
    m_tuiText = tr("Connecting to host TUI…");
    m_tuiStatus = tr("Host TUI");
    m_tuiAttached = false;
    m_tuiLive = false;
    m_tuiRev++;
    emit tuiChanged();
    pollTui();
    if (!m_tuiTimer.isActive())
        m_tuiTimer.start();
}

void ApiClient::stopLiveTui()
{
    m_tuiOpen = false;
    m_tuiInFlight = false;
    m_tuiTimer.stop();
}

void ApiClient::pollTui()
{
    if (!m_tuiOpen || m_currentSessionId.isEmpty())
        return;
    if (m_tuiInFlight)
        return;
    m_tuiInFlight = true;
    const QString path = QString("/api/sessions/%1/tui")
            .arg(QString::fromUtf8(QUrl::toPercentEncoding(m_currentSessionId)));
    QNetworkReply *reply = get(path, "tui");
    reply->setProperty("sid", m_currentSessionId);
}

void ApiClient::sendTuiKey(const QString &key)
{
    if (!m_tuiOpen || m_currentSessionId.isEmpty() || key.trimmed().isEmpty())
        return;
    QVariantMap body;
    QVariantList keys;
    keys.append(key.trimmed());
    body["keys"] = keys;
    const QString path = QString("/api/sessions/%1/tui/keys")
            .arg(QString::fromUtf8(QUrl::toPercentEncoding(m_currentSessionId)));
    post(path, body, "tui_keys");
}

void ApiClient::sendTuiLine(const QString &text)
{
    if (!m_tuiOpen || m_currentSessionId.isEmpty())
        return;
    const QString t = text; // allow trailing spaces; trim only all-empty
    if (t.trimmed().isEmpty())
        return;
    QVariantMap body;
    body["text"] = t;
    QVariantList keys;
    keys.append(QLatin1String("Enter"));
    body["keys"] = keys;
    const QString path = QString("/api/sessions/%1/tui/keys")
            .arg(QString::fromUtf8(QUrl::toPercentEncoding(m_currentSessionId)));
    post(path, body, "tui_keys");
}

void ApiClient::uploadAttachment(const QString &fileUrl)
{
    QString localPath = fileUrl;
    if (localPath.startsWith(QLatin1String("file://")))
        localPath = QUrl(fileUrl).toLocalFile();
    QFileInfo info(localPath);
    if (!info.isFile()) {
        setTranscriptStatus(tr("Attachment not found: %1").arg(localPath));
        return;
    }
    if (info.size() > MAX_UPLOAD_BYTES) {
        setTranscriptStatus(tr("Attachment too large (max 16 MB)"));
        return;
    }
    QFile f(localPath);
    if (!f.open(QIODevice::ReadOnly)) {
        setTranscriptStatus(tr("Could not read %1").arg(info.fileName()));
        return;
    }
    QByteArray payload = f.readAll();
    f.close();

    setTranscriptStatus(tr("Uploading %1...").arg(info.fileName()));
    QNetworkRequest request = makeRequest(
            "/api/attachments?name="
            + QString::fromUtf8(QUrl::toPercentEncoding(info.fileName())));
    request.setHeader(QNetworkRequest::ContentTypeHeader,
                      "application/octet-stream");
    QNetworkReply *reply = m_nam.post(request, payload);
    reply->setProperty("kind", QString("attach"));
    reply->setProperty("localName", info.fileName());
    // The prefill belongs to the session that uploaded, not whatever
    // transcript happens to be open when the reply lands.
    reply->setProperty("sid", m_currentSessionId);
    QTimer::singleShot(UPLOAD_TIMEOUT_MS, reply, SLOT(abort()));
}

// ------------------------------------------------------------- host->phone drop

void ApiClient::fetchDropFiles()
{
#ifdef VARIANT_UNIFIED
    // The inbox is one place for EVERY daemon's drop folder - the whole
    // point of the merged app is not remembering which machine produced
    // the artifact. Same generation-guarded fan-out as the session list.
    m_dropGen++;
    m_dropPending = 0;
    m_dropMergedRows.clear();
    m_dropErrors.clear();
    m_dropHostPaths.clear();
    m_dropStatus = tr("Loading...");
    m_dropRev++;
    emit dropChanged();
    for (int i = 0; i < m_profiles.size(); ++i) {
        QString base, token;
        if (!profileEndpoint(i, &base, &token))
            continue;
        QNetworkReply *reply = getFrom(base, token,
                                       QLatin1String("/api/drop"),
                                       QLatin1String("udrop_list"));
        reply->setProperty("profileIndex", i);
        reply->setProperty("dropGen", m_dropGen);
        m_dropPending++;
    }
    if (m_dropPending == 0) {
        m_dropFiles.clear();
        m_dropStatus = tr("Configure a server in Settings first");
        m_dropRev++;
        emit dropChanged();
    }
    return;
#endif
    if (!configured()) {
        m_dropFiles.clear();
        m_dropStatus = tr("Configure a server in Settings first");
        m_dropRev++;
        emit dropChanged();
        return;
    }
    m_dropStatus = tr("Loading...");
    m_dropRev++;
    emit dropChanged();
    get("/api/drop", "drop_list");
}

void ApiClient::downloadDropFile(const QString &name)
{
    downloadDropFrom(-1, name);
}

void ApiClient::downloadDropFrom(int profileIndex, const QString &name)
{
    const QString clean = QFileInfo(name).fileName().trimmed();
    if (clean.isEmpty() || clean == QLatin1String(".") || clean == QLatin1String("..")) {
        m_dropStatus = tr("Invalid file name");
        m_dropRev++;
        emit dropChanged();
        return;
    }
    // Which daemon holds the file: the row's profile, or the active one.
    QString base = m_baseUrl, token = m_token;
    if (profileIndex >= 0 && !profileEndpoint(profileIndex, &base, &token)) {
        m_dropStatus = tr("That profile is not configured");
        m_dropRev++;
        emit dropChanged();
        return;
    }
    if (base.isEmpty() || token.isEmpty()) {
        m_dropStatus = tr("Not connected");
        m_dropRev++;
        emit dropChanged();
        return;
    }
    m_dropStatus = tr("Downloading %1...").arg(clean);
    m_dropRev++;
    emit dropChanged();
    QNetworkReply *reply = getFrom(
            base, token,
            QString("/api/drop/%1")
                    .arg(QString::fromUtf8(QUrl::toPercentEncoding(clean))),
            "drop_dl");
    reply->setProperty("dropName", clean);
}

void ApiClient::deleteDropFile(const QString &name)
{
    deleteDropFrom(-1, name);
}

// --------------------------------------------------------------- focus list

void ApiClient::setSessionTitle(int profileIndex, const QString &sessionId,
                                const QString &title)
{
    QString base = m_baseUrl, token = m_token;
    if (profileIndex >= 0 && !profileEndpoint(profileIndex, &base, &token))
        return;
    if (base.isEmpty() || token.isEmpty() || sessionId.isEmpty())
        return;
    QVariantMap body;
    // An empty title clears the override: the daemon falls back to the name
    // the agent derived. Trimmed here so "   " is treated as empty too.
    body["title"] = title.trimmed();
    postTo(base, token,
           QString("/api/sessions/%1/title")
               .arg(QString::fromUtf8(QUrl::toPercentEncoding(sessionId))),
           body, QLatin1String("focusTitle"));
}

void ApiClient::promptRenameSession(int profileIndex, const QString &sessionId,
                                    const QString &currentTitle)
{
    if (sessionId.isEmpty())
        return;
    // One prompt, reused. The row that opened it is remembered on the client:
    // a ListItemComponent context action resolves nothing but ListItemData, so
    // neither a document-scope SystemPrompt nor its callback can carry the row.
    if (!m_renamePrompt) {
        m_renamePrompt = new bb::system::SystemPrompt(this);
        m_renamePrompt->setTitle(tr("Rename session"));
        m_renamePrompt->setBody(tr("Leave it empty to go back to the name "
                                   "the agent derived."));
        m_renamePrompt->confirmButton()->setLabel(tr("Save"));
        m_renamePrompt->cancelButton()->setLabel(tr("Cancel"));
        QObject::connect(
            m_renamePrompt,
            SIGNAL(finished(bb::system::SystemUiResult::Type)),
            this,
            SLOT(onRenamePromptFinished(bb::system::SystemUiResult::Type)));
    }
    m_renameProfileIndex = profileIndex;
    m_renameSessionId = sessionId;
    m_renamePrompt->inputField()->setDefaultText(currentTitle);
    m_renamePrompt->show();
}

void ApiClient::onRenamePromptFinished(bb::system::SystemUiResult::Type result)
{
    if (result != bb::system::SystemUiResult::ConfirmButtonSelection)
        return;
    if (m_renameSessionId.isEmpty() || !m_renamePrompt)
        return;
    setSessionTitle(m_renameProfileIndex, m_renameSessionId,
                    m_renamePrompt->inputFieldTextEntry());
}

// ---------------------------------------------------------- process view

bool ApiClient::processView() const
{
    return !m_currentSessionId.isEmpty()
            && m_processViewSessions.contains(m_currentSessionId);
}

void ApiClient::setProcessView(bool on)
{
    if (m_currentSessionId.isEmpty())
        return;
    if (on)
        m_processViewSessions.insert(m_currentSessionId);
    else
        m_processViewSessions.remove(m_currentSessionId);
    QSettings settings(BRAND_SETTINGS_ORG, BRAND_SETTINGS_APP);
    settings.setValue("processViewSessions",
                      QStringList(m_processViewSessions.toList()));
    emit processViewChanged();
    // Steps only travel with ?detail=steps — refetch either way so toggling
    // off also drops them from the transcript.
    fetchMessages(-1, INITIAL_PAGE_SIZE, false);
}

// One in-place model edit, mirrored to QML through the stepEdit pin. Never
// bump messageRev here: the full rebuild it triggers clears the ListView's
// model and snaps the reader back to the top of the transcript.
void ApiClient::publishStepEdit(const QString &action, int index,
                                const QVariantMap &item)
{
    m_stepEditAction = action;
    m_stepEditIndex = index;
    m_stepEditItem = item;
    m_stepEditRev++;
    emit stepEditChanged();
}

void ApiClient::toggleStep(const QString &ref)
{
    if (ref.isEmpty())
        return;
    for (int i = 0; i < m_messages.size(); ++i) {
        QVariantMap item = m_messages.at(i).toMap();
        if (item.value("kind").toString() != QLatin1String("step")
                || item.value("stepRef").toString() != ref)
            continue;
        // Collapse when this step's body row is already open below it.
        if (i + 1 < m_messages.size()) {
            QVariantMap next = m_messages.at(i + 1).toMap();
            if (next.value("kind").toString() == QLatin1String("stepbody")
                    && next.value("stepRef").toString() == ref) {
                m_messages.removeAt(i + 1);
                publishStepEdit(QLatin1String("remove"), i + 1, QVariantMap());
                return;
            }
        }
        if (item.value("stepSilent").toBool())
            return;
        QVariantMap body = blockItem("stepbody",
                                     item.value("stepPreview").toString(),
                                     QString(), false);
        body["stepRef"] = ref;
        m_messages.insert(i + 1, body);
        publishStepEdit(QLatin1String("insert"), i + 1, body);
        // Only the head of a big body travels with the window; the rest is
        // fetched on this first expand.
        if (item.value("stepTruncated").toBool()
                && !m_currentSessionId.isEmpty()) {
            QString path = QString("/api/sessions/%1/steps/%2")
                    .arg(QString::fromUtf8(
                             QUrl::toPercentEncoding(m_currentSessionId)),
                         QString::fromUtf8(QUrl::toPercentEncoding(ref)));
            QNetworkReply *reply = get(path, "stepfull");
            reply->setProperty("sid", m_currentSessionId);
            reply->setProperty("stepRef", ref);
        }
        return;
    }
}

void ApiClient::handleStepFull(QNetworkReply *reply, const QVariant &data)
{
    if (reply->property("sid").toString() != m_currentSessionId)
        return;
    const QString ref = reply->property("stepRef").toString();
    const QString text = data.toMap().value("text").toString();
    if (ref.isEmpty() || text.isEmpty())
        return;
    for (int i = 0; i < m_messages.size(); ++i) {
        QVariantMap item = m_messages.at(i).toMap();
        if (item.value("kind").toString() == QLatin1String("stepbody")
                && item.value("stepRef").toString() == ref) {
            item["text"] = text;
            m_messages.replace(i, item);
            publishStepEdit(QLatin1String("replace"), i, item);
            return;
        }
    }
}

/** One display row per step, appended under the message it followed. */
void ApiClient::appendStepItems(QVariantList &out, const QVariantList &steps)
{
    for (int i = 0; i < steps.size(); ++i) {
        QVariantMap s = steps.at(i).toMap();
        const QString kind = s.value("kind").toString();
        const bool isErr = kind == QLatin1String("tool_result")
                && !s.value("ok", true).toBool();
        const bool silent = kind == QLatin1String("thinking")
                && !s.value("recorded", true).toBool();
        QString mark, title;
        if (kind == QLatin1String("tool_use")) {
            mark = QString::fromUtf8("\xE2\x96\xB8");  // ▸
            title = s.value("name").toString();
            if (title.isEmpty())
                title = tr("tool");
        } else if (kind == QLatin1String("tool_result")) {
            mark = QString::fromUtf8("\xE2\x86\xB3");  // ↳
            title = isErr ? tr("error") : tr("result");
        } else if (kind == QLatin1String("thinking")) {
            mark = QString::fromUtf8("\xE2\x9C\xBB");  // ✻
            title = tr("thinking");
        } else {
            mark = QLatin1String("-");
            title = kind;
        }
        QString detail = s.value("detail").toString();
        if (detail.isEmpty())
            detail = s.value("preview").toString()
                    .section(QLatin1Char('\n'), 0, 0);
        detail = detail.simplified();
        if (detail.length() > 110)
            detail = detail.left(109) + QString::fromUtf8("\xE2\x80\xA6");
        QString head = mark + QLatin1String(" ") + title;
        if (silent)
            head += QLatin1String("  ") + tr("(not recorded by this CLI)");
        else if (!detail.isEmpty())
            head += QLatin1String("  ") + detail;
        const qlonglong bytes = s.value("bytes").toLongLong();
        if (bytes >= 1024)
            head += QString::fromLatin1("  %1KB").arg(bytes / 1024);
        QVariantMap item = blockItem("step", head, QString(), false);
        item["stepRef"] = s.value("ref").toString();
        item["stepPreview"] = s.value("preview").toString();
        item["stepTruncated"] = s.value("truncated").toBool();
        item["stepErr"] = isErr;
        item["stepSilent"] = silent;
        out.append(item);
    }
}

void ApiClient::regenerateSessionTitle(int profileIndex, const QString &sessionId)
{
    QString base = m_baseUrl, token = m_token;
    if (profileIndex >= 0 && !profileEndpoint(profileIndex, &base, &token))
        return;
    if (base.isEmpty() || token.isEmpty() || sessionId.isEmpty())
        return;
    m_sessionsStatus = tr("Naming the session...");
    emit sessionsChanged();
    postTo(base, token,
           QString("/api/sessions/%1/title/regenerate")
               .arg(QString::fromUtf8(QUrl::toPercentEncoding(sessionId))),
           QVariantMap(), QLatin1String("focusTitle"));
}

void ApiClient::markSessionSeen(int profileIndex, const QString &sessionId)
{
    QString base = m_baseUrl, token = m_token;
    if (profileIndex >= 0 && !profileEndpoint(profileIndex, &base, &token))
        return;
    if (base.isEmpty() || token.isEmpty() || sessionId.isEmpty())
        return;
    if (!profileSupportsFocus(profileIndex < 0 ? m_activeProfile : profileIndex))
        return;
    // Fire and forget: cosmetic only, and an old daemon just 404s.
    postTo(base, token,
           QString("/api/focus/%1/seen")
               .arg(QString::fromUtf8(QUrl::toPercentEncoding(sessionId))),
           QVariantMap(), QLatin1String("focusSeen"));
}

void ApiClient::setFocusMember(int profileIndex, const QString &sessionId,
                               bool member)
{
    QString base = m_baseUrl, token = m_token;
    if (profileIndex >= 0 && !profileEndpoint(profileIndex, &base, &token))
        return;
    if (base.isEmpty() || token.isEmpty() || sessionId.isEmpty())
        return;
    postTo(base, token,
           QString("/api/focus/%1/%2")
               .arg(QString::fromUtf8(QUrl::toPercentEncoding(sessionId)))
               .arg(member ? QLatin1String("restore") : QLatin1String("done")),
           QVariantMap(), QLatin1String("focusAction"));
}

void ApiClient::deleteDropFrom(int profileIndex, const QString &name)
{
    const QString clean = QFileInfo(name).fileName().trimmed();
    if (clean.isEmpty())
        return;
    QString base = m_baseUrl, token = m_token;
    if (profileIndex >= 0 && !profileEndpoint(profileIndex, &base, &token)) {
        m_dropStatus = tr("That profile is not configured");
        m_dropRev++;
        emit dropChanged();
        return;
    }
    if (base.isEmpty() || token.isEmpty()) {
        m_dropStatus = tr("Not connected");
        m_dropRev++;
        emit dropChanged();
        return;
    }
    m_dropStatus = tr("Deleting %1...").arg(clean);
    m_dropRev++;
    emit dropChanged();
    postTo(base, token,
           QString("/api/drop/%1/delete")
                   .arg(QString::fromUtf8(QUrl::toPercentEncoding(clean))),
           QVariantMap(), "drop_del");
}

// ---------------------------------------------------------------- job polling

void ApiClient::pollJob()
{
    if (m_jobId.isEmpty())
        return;
    // Synthetic status rows have no /api/jobs/<id> — polling them 404s and
    // used to call handleJobEnd("done"), then the stream re-attached tui-*.
    if (isSyntheticJobId(m_jobId)) {
        m_jobId.clear();
        m_pollTimer.stop();
        m_pollInFlight = false;
        setJobTicker(QString());
        emit jobRunningChanged();
        recomputeLiveStatus();
        return;
    }
    if (m_jobStartMs > 0) {
        qint64 secs = (QDateTime::currentMSecsSinceEpoch() - m_jobStartMs) / 1000;
        QString base = m_jobToolLine.isEmpty() ? workingLine() : m_jobToolLine;
        setJobTicker(base + QString::fromUtf8("  \xC2\xB7  %1s").arg(secs));
    }
    if (m_pollInFlight)
        return;
    // Doorbell suppression: a fresh status frame (~1 Hz while a job runs)
    // told us the daemon holds nothing past our cursor - skip the GET. The
    // ticker above still updated. Any doubt (stream down, stale frame, old
    // daemon without next_seq, job not yet in a frame) -> plain polling.
    qint64 nowMs = QDateTime::currentMSecsSinceEpoch();
    if (!m_wsDoorbell && m_statusSocket.isUp() && m_wsJobSeen
            && m_wsNextSeq >= 0 && m_wsNextSeq <= m_since
            && nowMs - m_wsFrameMs < WS_FRESH_MS)
        return;
    m_wsDoorbell = false;
    m_pollInFlight = true;
    QNetworkReply *reply =
            get(QString("/api/jobs/%1?since=%2").arg(m_jobId).arg(m_since), "job");
    // Tagged so a snapshot that raced a session switch is dropped instead
    // of being applied to whichever job is tracked by then.
    reply->setProperty("jobId", m_jobId);
}

void ApiClient::handleJobPoll(int httpStatus, const QVariant &data, bool parseOk,
                              const QString &networkError)
{
    m_pollInFlight = false;
    if (m_jobId.isEmpty())
        return; // job already finished (e.g. stop raced the last poll)

    // Job already pruned after a clean finish — treat as success, not "lost".
    if (httpStatus == 404) {
        handleJobEnd(QLatin1String("done"), QString(), QString(), 0);
        return;
    }
    if (!networkError.isEmpty() || httpStatus != 200 || !parseOk) {
        // Wi-Fi hiccups happen on a phone; tolerate a few misses.
        m_pollFailures++;
        if (m_pollFailures >= MAX_POLL_FAILURES) {
            handleJobEnd("error", QString(),
                         networkError.isEmpty() ? tr("Lost contact with the daemon")
                                                : networkError, 0);
        }
        return;
    }
    m_pollFailures = 0;

    QVariantMap snap = data.toMap();
    QVariantList events = snap["events"].toList();
    bool changed = false;
    for (int i = 0; i < events.size(); ++i) {
        QVariantMap event = events.at(i).toMap();
        QString kind = event.value("kind").toString();
        if (kind == "text") {
            QVariantList blocks = event.value("blocks").toList();
            if (!blocks.isEmpty())
                appendRenderedBlocks(m_messages, blocks, true);
            else
                m_messages.append(blockItem("p", event.value("text").toString(),
                                            QString(), true));
            changed = true;
        } else if (kind == "tool") {
            // Tool use is transient state: it shows in the status strip's
            // ticker while the job runs, never in the transcript. Match the
            // /ws/status path: shortDetail so a long shell command can't
            // push the banner past ~1-2 lines (same cap as phaseLine).
            QString line = QString::fromUtf8("\xE2\x9A\x99 ")
                    + shortDetail(event.value("name").toString(), 32);
            QString detail = shortDetail(event.value("detail").toString());
            if (!detail.isEmpty())
                line += "  " + detail;
            m_jobToolLine = line;
            setJobTicker(line);
            // With no status stream (https base URL, or the socket down)
            // these poll events are the only status the app ever sees, so
            // they have to drive the cue instead of recomputeLiveStatus.
            if (!m_statusSocket.isUp())
                notifyStatus("tool/" + event.value("name").toString());
        }
    }
    if (changed)
        bumpMessages(true);
    m_since = snap["next_seq"].toInt();

    // The daemon owns the queue; mirror its view.
    updateQueueFromServer(snap["queued"].toList());

    // Permission prompt state - the snapshot is the source of truth so a
    // missed event can't strand the UI (non-auto modes only).
    updatePendingPermission(snap);
    // Same for the AskUserQuestion panel blocking the interactive TUI.
    updatePendingQuestion(snap);

    // A brand-new session gets its id from the job's init event; adopt it
    // as soon as it appears so the title/Refresh state stays consistent -
    // and map the fork id to this job so re-opening finds it again.
    QString forkId = snap["new_session_id"].toString();
    if (!forkId.isEmpty()) {
        m_sessionJobs[forkId] = m_jobId;
        if (m_currentSessionId.isEmpty()) {
            m_currentSessionId = forkId;
            emit currentSessionChanged();
        }
    }

    QString status = snap["status"].toString().trimmed();
    // Status stream blip (reconnect / daemon restart): job left the active
    // list but is still running — keep polling, do not end as failure.
    if (status == QLatin1String("starting")
            || status == QLatin1String("running"))
        return;

    // The daemon chains queued prompts: when a job finishes cleanly it
    // starts the next queued one and points at it. Follow the chain
    // instead of tearing the job UI down.
    QString nextJobId = snap["next_job_id"].toString();
    if (status == "done" && !nextJobId.isEmpty()) {
        if (!forkId.isEmpty() && forkId != m_currentSessionId) {
            m_currentSessionId = forkId;
            emit currentSessionChanged();
        }
        m_sessionJobs[m_currentSessionId] = nextJobId;
        if (!forkId.isEmpty())
            m_sessionJobs[forkId] = nextJobId;
        m_jobId = nextJobId;
        m_since = 0;
        m_pollFailures = 0;
        resetWsJobState();
        m_jobStartMs = QDateTime::currentMSecsSinceEpoch();
        m_jobToolLine.clear();
        setJobTicker(workingLine());
        return;
    }

    if (status == "done" || status == "error" || status == "stopped")
        handleJobEnd(status, snap["new_session_id"].toString(),
                     snap["error"].toString(),
                     snap["dropped_queued"].toInt());
}

void ApiClient::handleJobEnd(const QString &status, const QString &newSessionId,
                             const QString &error, int droppedQueued)
{
    const QString endedJob = m_jobId;
    m_pollTimer.stop();
    m_pollInFlight = false;
    m_pollFailures = 0;
    m_jobId.clear();
    m_since = 0;
    resetWsJobState();
    m_awaitingJob = false;
    m_jobStartMs = 0;
    m_jobToolLine.clear();
    setJobTicker(QString());
    clearPendingPermission();
    emit jobRunningChanged();

    // Drop every session mapping that pointed at the finished job.
    if (!endedJob.isEmpty()) {
        QStringList stale;
        QMap<QString, QString>::const_iterator it = m_sessionJobs.constBegin();
        for (; it != m_sessionJobs.constEnd(); ++it) {
            if (it.value() == endedJob)
                stale.append(it.key());
        }
        for (int i = 0; i < stale.size(); ++i)
            m_sessionJobs.remove(stale.at(i));
    }

    // A headless resume forks the session; follow the fork so the
    // conversation appears continuous. The fork id is known from the job's
    // init event, so it is available even for stopped/failed runs.
    if (!newSessionId.isEmpty() && newSessionId != m_currentSessionId) {
        m_currentSessionId = newSessionId;
        emit currentSessionChanged();
    }
    // The daemon's WebSocket frame that removes this job from the active list
    // may not have arrived yet. Prune it now so recomputeLiveStatus clears
    // liveStatusLine immediately - otherwise statusBanner.active stays true
    // and masks the transcriptStatus error message.
    if (!endedJob.isEmpty()) {
        for (int i = m_activeStatuses.size() - 1; i >= 0; --i) {
            if (m_activeStatuses.at(i).toMap().value("job_id").toString() == endedJob)
                m_activeStatuses.removeAt(i);
        }
    }
    recomputeLiveStatus();

    // Only an explicit daemon "error" is a failure. Unknown/empty status
    // (and pruned jobs) are treated as success so stream blips never sound
    // like a failed turn after a clean finish.
    if (status == QLatin1String("error")) {
        m_chime->play(Chime::CueError);
        QString base = error.isEmpty() ? tr("Job failed")
                                       : (tr("Job failed: ") + error);
        m_jobEndStatus = base + dropQueueNote(droppedQueued);
        setTranscriptStatus(m_jobEndStatus);
        refreshTranscript();
    } else if (status == QLatin1String("stopped")) {
        m_jobEndStatus = tr("Job stopped") + dropQueueNote(droppedQueued);
        setTranscriptStatus(m_jobEndStatus);
        refreshTranscript();
    } else {
        // "done" or anything else terminal-but-not-error.
        m_chime->play(Chime::CueDone);
        m_jobEndStatus.clear();
        setTranscriptStatus(QString());
        refreshTranscript();
    }
    // Watching the turn finish on this transcript counts as having read it
    // — same as opening the row from the list (dims Focus "turn finished").
    if (!m_currentSessionId.isEmpty())
        markSessionSeen(m_activeProfile, m_currentSessionId);
    // The finished job changed previews/ordering on the sessions list.
    refreshSessions();
}

// ---------------------------------------------------------------- dispatch

void ApiClient::onFinished(QNetworkReply *reply)
{
    reply->deleteLater();
    const QString kind = reply->property("kind").toString();
    const int httpStatus =
            reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    const QByteArray body = reply->readAll();
    const QString networkError =
            reply->error() != QNetworkReply::NoError ? reply->errorString() : QString();

    bool parseOk = false;
    QVariant data = parseBody(body, &parseOk);
    const bool ok = networkError.isEmpty()
            && httpStatus >= 200 && httpStatus < 300 && parseOk;

    if (kind == "job") {
        if (reply->property("jobId").toString() == m_jobId)
            handleJobPoll(httpStatus, data, parseOk, networkError);
        else if (m_pollInFlight)
            m_pollInFlight = false; // stale poll answered; free the slot
        return;
    }

    if (kind == "ping") {
        if (ok) {
            QVariantMap map = data.toMap();
            updateCaps(map["caps"].toMap());
            // Focus support is a top-level ping flag, not one of the per-
            // harness caps: Focus spans every harness on the daemon.
            const bool focus = map.value("focus", QVariant(false)).toBool();
            if (focus != m_capFocus) {
                m_capFocus = focus;
                emit capsChanged();
            }
            QStringList commands;
            QVariantList rawCommands = map["slash_commands"].toList();
            for (int i = 0; i < rawCommands.size(); ++i)
                commands.append(rawCommands.at(i).toString());
            QStringList modelList;
            QVariantList rawModels = map["models"].toList();
            for (int i = 0; i < rawModels.size(); ++i)
                modelList.append(rawModels.at(i).toString());
            QStringList effortList;
            QVariantList rawEfforts = map["efforts"].toList();
            for (int i = 0; i < rawEfforts.size(); ++i)
                effortList.append(rawEfforts.at(i).toString());
            if (commands != m_slashCommands || modelList != m_models
                    || effortList != m_efforts) {
                m_slashCommands = commands;
                m_models = modelList;
                m_efforts = effortList;
                emit capsChanged();
            }
            m_pingState = 2;
            QString provider = map["provider"].toString();
            const bool multi = map.value("multi").toBool();
            QString multiLabel;
            if (multi) {
                QVariantList ps = map.value("providers").toList();
                QStringList names;
                for (int i = 0; i < ps.size(); ++i)
                    names.append(ps.at(i).toString());
                multiLabel = names.join(QLatin1String("+"));
            }
            m_pingInfo = QString("%1 (agentremoted %2%3)")
                    .arg(map["host"].toString(), map["version"].toString(),
                         multiLabel.isEmpty()
                         ? (provider.isEmpty() ? QString()
                                               : QString(", %1").arg(provider))
                         : QString(", multi %1").arg(multiLabel));
            // Cache identity on the profile: the unified list badges rows
            // from it, and a later switch gates features before its ping.
            QVariantMap prof = m_profiles.value(m_activeProfile).toMap();
            const QVariantMap newCaps = map["caps"].toMap();
            const QVariantMap newDetails = map.value("provider_details").toMap();
            const QVariantList newProviders = map.value("providers").toList();
            if (prof.value("provider").toString() != provider
                    || prof.value("caps").toMap() != newCaps
                    || prof.value("multi").toBool() != multi
                    || prof.value("providers").toList() != newProviders
                    || prof.value("provider_details").toMap() != newDetails) {
                prof["provider"] = provider;
                prof["caps"] = newCaps;
                // Always store multi catalogue so New Session can pick harnesses
                // even if an earlier code path only cached "provider".
                prof["multi"] = multi || newProviders.size() > 1;
                prof["providers"] = newProviders;
                prof["provider_details"] = newDetails;
                m_profiles.replace(m_activeProfile, prof);
                persistProfiles();
                emit profilesChanged();
                annotateProviderRows(m_activeProfile, provider);
            }
            // Multi host: keep neutral chrome (not first harness orange/cyan).
            if (multi || newProviders.size() > 1)
                setProvider(QString());
            else
                setProvider(provider);
#ifndef VARIANT_UNIFIED
            // Host->phone drop path (authed ping only) so the sheet can show
            // it before the first list fetch. The unified inbox builds a
            // per-daemon summary in the udrop_list merge instead - a single
            // daemon's ping must not overwrite it.
            QString dropPath = map.value("drop_path").toString();
            if (!dropPath.isEmpty() && dropPath != m_dropPath) {
                m_dropPath = dropPath;
                emit dropChanged();
            }
#endif
        } else {
            m_pingState = 3;
            m_pingInfo = networkError.isEmpty()
                    ? tr("Unexpected response (HTTP %1)").arg(httpStatus)
                    : networkError;
        }
        emit pingChanged();
        return;
    }

    // Unified usage: progressive merge. Each daemon answers when ready;
    // same (provider, account) seat across hosts is one row (higher %).
    if (kind == "uusage") {
        if (reply->property("usageGen").toInt() != m_usageGen)
            return;
        const int profileIndex = reply->property("profileIndex").toInt();
        QVariantMap prof = m_profiles.value(profileIndex).toMap();
        const QString profileName = prof.value("name").toString();
        const QString defaultProvider = prof.value("provider").toString();
        if (ok) {
            const QVariantMap root = data.toMap();
            const QString defaultAccent = providerAccent(defaultProvider);
            QVariantList tagged;
            // BB10 NDK is GCC 4.6 / pre-C++11 — no lambdas, no QStringLiteral.
            // Tag each bucket with harness + account so multi-host merge works.
            const QVariantList sections = root.value("sections").toList();
            if (root.value("multi").toBool() && !sections.isEmpty()) {
                for (int s = 0; s < sections.size(); ++s) {
                    const QVariantMap sec = sections.at(s).toMap();
                    if (sec.value("ok").toBool() == false
                            && sec.value("buckets").toList().isEmpty()) {
                        QString err = sec.value("error").toString();
                        if (err.isEmpty())
                            err = tr("Not available");
                        const QString prov = sec.value("provider").toString();
                        m_usageErrors.append(
                            QString("%1 · %2: %3")
                                .arg(profileName,
                                     prov.isEmpty() ? QLatin1String("agent") : prov,
                                     err));
                        continue;
                    }
                    appendUsageBuckets(&tagged,
                                       sec.value("buckets").toList(),
                                       sec.value("provider").toString(),
                                       sec.value("account").toString(),
                                       sec.value("account_id").toString(),
                                       defaultProvider, defaultAccent,
                                       profileName, this);
                }
            } else if (root.value("ok").toBool()) {
                appendUsageBuckets(&tagged,
                                   root.value("buckets").toList(),
                                   root.value("provider").toString().isEmpty()
                                       ? defaultProvider
                                       : root.value("provider").toString(),
                                   root.value("account").toString(),
                                   root.value("account_id").toString(),
                                   defaultProvider, defaultAccent,
                                   profileName, this);
            } else {
                QString err = root.value("error").toString();
                if (err.isEmpty())
                    err = httpErrorText(httpStatus, data, parseOk, networkError);
                m_usageErrors.append(
                    QString("%1: %2").arg(profileName, err));
            }
            m_usageByProfile[profileIndex] = tagged;
        } else {
            QString err = httpErrorText(httpStatus, data, parseOk, networkError);
            m_usageErrors.append(
                QString("%1: %2").arg(profileName, err));
        }
        // Merge every host's rows by (provider, account, title) so the same
        // seat on Mac + VPS is one bar (keep the higher percent).
        QMap<QString, QVariantMap> merged;
        QStringList order;
        QMap<int, QVariantList>::const_iterator it =
                m_usageByProfile.constBegin();
        for (; it != m_usageByProfile.constEnd(); ++it) {
            const QVariantList rows = it.value();
            for (int i = 0; i < rows.size(); ++i) {
                QVariantMap b = rows.at(i).toMap();
                const QString prov = b.value("provider").toString().toLower();
                QString acct = b.value("account_id").toString();
                if (acct.isEmpty())
                    acct = b.value("account").toString();
                acct = acct.toLower().trimmed();
                const QString title = b.value("title").toString();
                QString key;
                if (!prov.isEmpty() && !acct.isEmpty())
                    key = prov + QLatin1Char('|') + acct
                            + QLatin1Char('|') + title;
                else
                    key = b.value("source").toString()
                            + QLatin1Char('|') + title
                            + QLatin1Char('|') + QString::number(it.key())
                            + QLatin1Char('|') + QString::number(i);
                if (!merged.contains(key)) {
                    merged.insert(key, b);
                    order.append(key);
                } else {
                    QVariantMap prev = merged.value(key);
                    if (b.value("percent").toInt() > prev.value("percent").toInt()) {
                        // Keep better fill; preserve multi-host label.
                        QString host = prev.value("host").toString();
                        const QString other = b.value("host").toString();
                        if (!other.isEmpty() && !host.contains(other)) {
                            if (!host.isEmpty())
                                host += QLatin1String(" · ");
                            host += other;
                        }
                        b["host"] = host;
                        // source already names seat; append hosts if multi.
                        if (host.contains(QLatin1String(" · ")))
                            b["source"] = b.value("source").toString()
                                    + QLatin1String("  (") + host + QLatin1Char(')');
                        merged.insert(key, b);
                    } else {
                        QString host = prev.value("host").toString();
                        const QString other = b.value("host").toString();
                        if (!other.isEmpty() && !host.contains(other)) {
                            if (!host.isEmpty())
                                host += QLatin1String(" · ");
                            host += other;
                            prev["host"] = host;
                            if (host.contains(QLatin1String(" · ")))
                                prev["source"] = prev.value("source").toString()
                                        + QLatin1String("  (") + host + QLatin1Char(')');
                            merged.insert(key, prev);
                        }
                    }
                }
            }
        }
        m_usageBuckets.clear();
        for (int i = 0; i < order.size(); ++i)
            m_usageBuckets.append(merged.value(order.at(i)));
        if (--m_usagePending > 0) {
            // Keep the exact "Loading..." string: the sheet's spinner
            // compares against it.
            m_usageStatus = tr("Loading...");
        } else if (!m_usageErrors.isEmpty() && m_usageBuckets.isEmpty()) {
            m_usageStatus = m_usageErrors.join(QLatin1String("; "));
        } else if (!m_usageErrors.isEmpty()) {
            // Partial success: show bars; errors stay in status as a note.
            m_usageStatus = m_usageErrors.join(QLatin1String("; "));
        } else {
            m_usageStatus = m_usageBuckets.isEmpty()
                    ? tr("No usage data available") : QString();
        }
        m_usageRev++;
        emit usageChanged();
        return;
    }

    if (kind == "usage") {
        if (ok && data.toMap().value("ok").toBool()) {
            QVariantMap root = data.toMap();
            QVariantList buckets = root.value("buckets").toList();
            const QString account = root.value("account").toString();
            const QString accountId = root.value("account_id").toString();
            const QString provider = root.value("provider").toString();
            QVariantList tagged;
            for (int i = 0; i < buckets.size(); ++i) {
                QVariantMap b = buckets.at(i).toMap();
                if (!provider.isEmpty())
                    b["provider"] = provider;
                b["account"] = account;
                b["account_id"] = accountId.isEmpty() ? account : accountId;
                if (!account.isEmpty()) {
                    const QString harness = b.value("provider").toString();
                    const QString label = harness.isEmpty()
                            ? account
                            : (harness.left(1).toUpper() + harness.mid(1)
                               + QLatin1String(" · ") + account);
                    b["source"] = label;
                }
                if (!b.value("provider").toString().isEmpty())
                    b["accent"] = providerAccent(b.value("provider").toString());
                tagged.append(b);
            }
            m_usageBuckets = tagged;
            m_usageStatus = m_usageBuckets.isEmpty()
                    ? tr("No usage data available") : QString();
        } else {
            m_usageBuckets.clear();
            // The daemon's {ok:false,error} is a complete sentence; fall back
            // to the transport error otherwise.
            QString err = ok ? data.toMap().value("error").toString() : QString();
            if (err.isEmpty())
                err = httpErrorText(httpStatus, data, parseOk, networkError);
            m_usageStatus = err;
        }
        m_usageRev++;
        emit usageChanged();
        return;
    }

    if (kind == "projects") {
        if (ok) {
            m_projects = data.toMap()["projects"].toList();
            m_projectsStatus = m_projects.isEmpty() ? tr("No projects found") : QString();
        } else {
            m_projectsStatus = httpErrorText(httpStatus, data, parseOk, networkError);
        }
        m_projectsRev++;
        emit projectsChanged();
        return;
    }

    // Unified fan-out: one reply per profile; merge + sort when the last
    // one lands. Generation + query tags drop replies of a superseded fetch.
    if (kind == "usessions") {
        if (reply->property("unifiedGen").toInt() != m_unifiedGen)
            return;
        if (reply->property("searchQuery").toString() != m_searchQuery)
            return;
        const int profileIndex = reply->property("profileIndex").toInt();
        QVariantMap prof = m_profiles.value(profileIndex).toMap();
        if (ok) {
            QVariantMap map = data.toMap();
            QVariantList raw = m_searchQuery.isEmpty()
                    ? map.value("sessions").toList()
                    : map.value("results").toList();
            const QString profileProvider = prof.value("provider").toString();
            const QString profileName = prof.value("name").toString();
            for (int i = 0; i < raw.size(); ++i) {
                QVariantMap s = raw.at(i).toMap();
                s["profileIndex"] = profileIndex;
                s["profileName"] = profileName;
                // Multi-harness daemon tags each session; else profile provider.
                QString provider = s.value("provider").toString();
                if (provider.isEmpty())
                    provider = profileProvider;
                s["provider"] = provider;
                s["accent"] = providerAccent(provider);
                // Sort axis across daemons with different timestamp habits
                // (claude ms-ISO, grok ns-ISO): epoch seconds, 0 if unreadable.
                QDateTime dt = parseIso(s.value("last_active").toString());
                if (!dt.isValid())
                    dt = parseIso(s.value("started").toString());
                s["_sortKey"] = dt.isValid()
                        ? (qlonglong)dt.toMSecsSinceEpoch() : (qlonglong)0;
                decorateSessionRow(s);
                m_unifiedRows.append(s);
            }
        } else {
            m_unifiedErrors.append(QString("%1: %2").arg(
                prof.value("name").toString(),
                httpErrorText(httpStatus, data, parseOk, networkError)));
        }
        if (--m_unifiedPending <= 0)
            finishUnifiedFetch();
        return;
    }

    // Provider/caps discovery for the NON-active profiles (unified): badge
    // their rows and cache their gating without connecting to them.
    if (kind == "profilePing") {
        if (!ok)
            return; // silent - the daemon shows as error when actually used
        const int profileIndex = reply->property("profileIndex").toInt();
        if (profileIndex < 0 || profileIndex >= m_profiles.size())
            return;
        QVariantMap map = data.toMap();
        const QString provider = map.value("provider").toString();
        const bool multi = map.value("multi").toBool();
        QVariantMap prof = m_profiles.at(profileIndex).toMap();
        const QVariantList newProviders = map.value("providers").toList();
        const bool focus = map.value("focus", QVariant(false)).toBool();
        if (prof.value("provider").toString() != provider
                || prof.value("caps").toMap() != map.value("caps").toMap()
                || prof.value("multi").toBool() != multi
                || prof.value("focus").toBool() != focus
                || prof.value("providers").toList() != newProviders
                || prof.value("provider_details").toMap()
                   != map.value("provider_details").toMap()) {
            prof["provider"] = provider;
            prof["caps"] = map.value("caps").toMap();
            // Focus support is per daemon, cached so Focus mode knows which
            // profiles can answer /api/focus before it fans out.
            prof["focus"] = focus;
            prof["multi"] = multi || newProviders.size() > 1;
            prof["providers"] = newProviders;
            prof["provider_details"] = map.value("provider_details").toMap();
            m_profiles.replace(profileIndex, prof);
            persistProfiles();
            emit profilesChanged();
            annotateProviderRows(profileIndex, provider);
        }
        return;
    }

    // Focus writes: refetch so the row (or its absence) reflects the truth on
    // the daemon rather than a guess made here.
    if (kind == "focusSeen")
        return; // cosmetic; nothing to repaint until the next listing

    if (kind == "focusTitle" || kind == "focusAction") {
        if (!ok) {
            m_sessionsStatus =
                    httpErrorText(httpStatus, data, parseOk, networkError);
            m_sessionsRev++;
            emit sessionsChanged();
            return;
        }
        refreshSessions();
        return;
    }
    if (kind == "sessions" || kind == "search") {
        // Drop stale search replies: the user typed further (or cleared)
        // while this request was in flight.
        if (kind == "search") {
            const QString requested = reply->property("searchQuery").toString();
            if (requested != m_searchQuery)
                return;
        } else if (!m_searchQuery.isEmpty()) {
            // A list reload crossed a new search - keep the search path.
            return;
        }
        if (ok) {
            QVariantMap map = data.toMap();
            // Search returns {query, results}; list returns {sessions}.
            QVariantList raw = (kind == "search")
                    ? map.value("results").toList()
                    : map.value("sessions").toList();
            if (kind == "sessions")
                m_searchQuery.clear();
            m_sessions.clear();
            for (int i = 0; i < raw.size(); ++i) {
                QVariantMap s = raw.at(i).toMap();
                decorateSessionRow(s);
                m_sessions.append(s);
            }
            annotateWorkingSessions(false);
            if (m_sessions.isEmpty()) {
                m_sessionsStatus = m_searchQuery.isEmpty()
                        ? tr("No sessions found")
                        : tr("No matches for \"%1\"").arg(m_searchQuery);
            } else if (!m_searchQuery.isEmpty()) {
                m_sessionsStatus = tr("%1 match(es) for \"%2\"")
                        .arg(m_sessions.size()).arg(m_searchQuery);
            } else {
                m_sessionsStatus = QString();
            }
        } else {
            m_sessionsStatus = httpErrorText(httpStatus, data, parseOk, networkError);
        }
        m_sessionsRev++;
        emit sessionsChanged();
        return;
    }

    if (kind == "messages") {
        if (ok) {
            handleMessages(reply, data);
        } else {
            setTranscriptStatus(httpErrorText(httpStatus, data, parseOk, networkError));
        }
        // handleMessages has already built + rendered the new rows by now;
        // clearing here re-enables the "load older" row (success or error).
        setLoadingOlder(false);
        return;
    }

    if (kind == "stepfull") {
        // Full body behind a truncated process step; errors keep the preview.
        if (ok)
            handleStepFull(reply, data);
        return;
    }

    if (kind == "continue") {
        const QString sid = reply->property("sid").toString();
        // m_awaitingJob is global, so clear it whichever session answered.
        // It used to be cleared only for the still-open session: switch
        // session while a send was in flight and the flag stuck on forever,
        // which makes jobRunning() true with no job id — and in that state
        // every later send was silently buffered instead of sent.
        if (m_awaitingJob) {
            m_awaitingJob = false;
            emit jobRunningChanged();
        }
        if (ok) {
            const QString jobId = data.toMap()["job_id"].toString();
            m_sessionJobs[sid] = jobId;
            // Prompts typed before the job id arrived now go to the
            // daemon's queue - regardless of which page is open now.
            QStringList pending = m_pendingQueue.take(sid);
            for (int i = 0; i < pending.size(); ++i) {
                if (interactiveMode())
                    postDirectInput(jobId, pending.at(i));
                else
                    postQueuePrompt(jobId, pending.at(i));
            }
            if (!pending.isEmpty())
                emit queueChanged();
            if (sid == m_currentSessionId)
                attachToJob(jobId);
        } else if (sid == m_currentSessionId) {
            // If the send itself failed, queued follow-ups would fail too.
            setTranscriptStatus(httpErrorText(httpStatus, data, parseOk, networkError)
                                + dropQueueNote(0));
        } else {
            m_pendingQueue.remove(sid);
        }
        return;
    }

    if (kind == "input") {
        // Typed straight into the TUI: nothing to mirror on success. If the
        // TUI is gone (409) or we hit a synthetic/expired job id (404), fall
        // back to /continue so the prompt is not deleted from the UI as "lost".
        if (!ok) {
            const QString prompt = reply->property("prompt").toString();
            if (httpStatus == 404 || isSyntheticJobId(m_jobId)
                    || (!jobRunning() && !m_currentSessionId.isEmpty())) {
                if (isSyntheticJobId(m_jobId) || httpStatus == 404) {
                    m_jobId.clear();
                    m_pollTimer.stop();
                    emit jobRunningChanged();
                }
                if (!m_currentSessionId.isEmpty() && !prompt.isEmpty())
                    postPrompt(prompt);
                else {
                    removeQueuedEcho(prompt);
                    setTranscriptStatus(tr("Couldn't send message: ")
                                        + httpErrorText(httpStatus, data, parseOk,
                                                        networkError));
                }
            } else {
                removeQueuedEcho(prompt);
                setTranscriptStatus(tr("Couldn't send message: ")
                                    + httpErrorText(httpStatus, data, parseOk,
                                                    networkError));
            }
        }
        return;
    }

    if (kind == "queue") {
        QString prompt = reply->property("prompt").toString();
        QString jobId = reply->property("jobId").toString();
        if (ok) {
            if (jobId == m_jobId)
                updateQueueFromServer(data.toMap()["queued"].toList());
        } else if (jobId == m_jobId && !jobRunning() && !m_currentSessionId.isEmpty()) {
            // The job finished before the queue request landed - send the
            // prompt directly instead of losing it.
            postPrompt(prompt);
        } else if (jobId == m_jobId) {
            removeQueuedEcho(prompt);
            setTranscriptStatus(tr("Couldn't queue message: ")
                                + httpErrorText(httpStatus, data, parseOk,
                                                networkError));
        }
        return;
    }

    if (kind == "qcancel") {
        if (ok) {
            QVariantMap map = data.toMap();
            updateQueueFromServer(map["queued"].toList());
            removeQueuedEcho(map["prompt"].toString());
        }
        // Failure: the prompt already ran or was gone - the next poll's
        // snapshot reconciles the mirror.
        return;
    }

    if (kind == "shell") {
        // Drop replies for a session the user already left.
        if (reply->property("sid").toString() != m_currentSessionId) {
            setTranscriptStatus(QString());
            return;
        }
        if (ok) {
            QString output = data.toMap()["output"].toString();
            if (output.endsWith(QLatin1Char('\n')))
                output.chop(1);
            int exitCode = data.toMap()["exit_code"].toInt();
            if (output.isEmpty())
                output = QLatin1String("(no output)");
            // Show output immediately as a code block (live preview).
            appendLiveItem(blockItem("code", output, QString(), true));
            // Feed the command + output to the AI via continue so it is
            // persisted and available as context. [silent] tells the agent
            // not to reply (stripped from the phone's transcript renderer).
            // Format: "[shell] ! cmd\n[output]\n```\n...\n```\n[silent] ..."
            QString cmd = reply->property("cmd").toString();
            QString prompt = QString("[shell] ! %1\n[output]\n```\n%2")
                    .arg(cmd, output);
            if (exitCode != 0)
                prompt += QString("\n(exit code %1)").arg(exitCode);
            prompt += QLatin1String(
                    // No trailing "wait for the next user instruction": the model
                    // echoed that clause straight back instead of staying quiet.
                    "\n```\n[silent] Shell result for context only. "
                    "Do not reply or acknowledge this message.");
            setTranscriptStatus(QString());
            postPrompt(prompt);
        } else {
            setTranscriptStatus(httpErrorText(httpStatus, data, parseOk, networkError));
        }
        return;
    }

    if (kind == "attach") {
        if (ok) {
            if (reply->property("sid").toString() != m_currentSessionId) {
                // Finished after the user moved to another session - don't
                // prefill someone else's compose field with it.
                return;
            }
            m_lastAttachmentPath = data.toMap()["path"].toString();
            m_attachRev++;
            emit attachChanged();
            setTranscriptStatus(QString());
        } else {
            setTranscriptStatus(tr("Upload failed: ")
                                + httpErrorText(httpStatus, data, parseOk,
                                                networkError));
        }
        return;
    }

    // Unified inbox: one reply per profile, merged newest-first when the
    // last daemon answers (or fails - a dead daemon must say so, not look
    // like an empty folder).
    if (kind == "udrop_list") {
        if (reply->property("dropGen").toInt() != m_dropGen)
            return;
        const int profileIndex = reply->property("profileIndex").toInt();
        QVariantMap prof = m_profiles.value(profileIndex).toMap();
        if (ok) {
            QVariantMap map = data.toMap();
            const QString profileName = prof.value("name").toString();
            const QString provider = prof.value("provider").toString();
            const QString hostPath = map.value("path").toString();
            if (!hostPath.isEmpty())
                m_dropHostPaths.append(
                    QString("%1: %2").arg(profileName, hostPath));
            QVariantList raw = map.value("files").toList();
            for (int i = 0; i < raw.size(); ++i) {
                QVariantMap f = raw.at(i).toMap();
                qint64 size = f.value("size").toLongLong();
                f["size_text"] = formatBytes(size);
                qint64 mtime = f.value("mtime").toLongLong();
                if (mtime > 0) {
                    QDateTime dt = QDateTime::fromTime_t(
                            static_cast<uint>(mtime));
                    f["mtime_text"] = dt.toLocalTime()
                            .toString(QLatin1String("yyyy-MM-dd hh:mm"));
                } else {
                    f["mtime_text"] = QString();
                }
                f["api"] = QVariant::fromValue<QObject *>(this);
                f["profileIndex"] = profileIndex;
                f["profileName"] = profileName;
                f["accent"] = providerAccent(provider);
                f["_sortKey"] = (qlonglong)mtime;
                m_dropMergedRows.append(f);
            }
        } else {
            m_dropErrors.append(QString("%1: %2").arg(
                prof.value("name").toString(),
                httpErrorText(httpStatus, data, parseOk, networkError)));
        }
        if (--m_dropPending <= 0) {
            qStableSort(m_dropMergedRows.begin(), m_dropMergedRows.end(),
                        sessionRowNewer);
            // Identical (name, size, mtime) from two sources is one file -
            // typical when two profiles reach the SAME daemon via different
            // URLs (LAN + Cloudflare). Keep the copy from the profile listed
            // first (the user's preference order) and note the other source
            // on the surviving row, so "delete" reads honestly.
            QVariantList deduped;
            QHash<QString, int> posByKey;
            QHash<int, QStringList> alsoByPos;
            for (int i = 0; i < m_dropMergedRows.size(); ++i) {
                QVariantMap row = m_dropMergedRows.at(i).toMap();
                const QString key = QString("%1|%2|%3").arg(
                    row.value("name").toString(),
                    row.value("size").toString(),
                    row.value("mtime").toString());
                if (!posByKey.contains(key)) {
                    posByKey[key] = deduped.size();
                    deduped.append(row);
                    continue;
                }
                const int pos = posByKey[key];
                QVariantMap kept = deduped.at(pos).toMap();
                if (row.value("profileIndex").toInt()
                        < kept.value("profileIndex").toInt()) {
                    alsoByPos[pos].append(kept.value("profileName").toString());
                    deduped.replace(pos, row);
                } else {
                    alsoByPos[pos].append(row.value("profileName").toString());
                }
            }
            QHash<int, QStringList>::const_iterator alsoIt =
                    alsoByPos.constBegin();
            for (; alsoIt != alsoByPos.constEnd(); ++alsoIt) {
                QVariantMap row = deduped.at(alsoIt.key()).toMap();
                row["also"] = tr("also on %1")
                        .arg(alsoIt.value().join(QLatin1String(", ")));
                deduped.replace(alsoIt.key(), row);
            }
            m_dropFiles = deduped;
            m_dropMergedRows.clear();
            // The sheet's "Host:" line - one entry per daemon.
            m_dropPath = m_dropHostPaths.join(
                QString::fromUtf8("\n"));
            m_dropStatus = m_dropFiles.isEmpty()
                    ? tr("Empty - ask the agent to copy files into the drop folder")
                    : QString();
            if (!m_dropErrors.isEmpty()) {
                const QString errs = m_dropErrors.join(QLatin1String("; "));
                m_dropStatus = m_dropStatus.isEmpty()
                        ? errs
                        : m_dropStatus + QLatin1String(" - ") + errs;
            }
            m_dropRev++;
            emit dropChanged();
        }
        return;
    }

    if (kind == "drop_list") {
        if (ok) {
            QVariantMap map = data.toMap();
            m_dropPath = map.value("path").toString();
            QVariantList raw = map.value("files").toList();
            m_dropFiles.clear();
            for (int i = 0; i < raw.size(); ++i) {
                QVariantMap f = raw.at(i).toMap();
                qint64 size = f.value("size").toLongLong();
                f["size_text"] = formatBytes(size);
                // Host mtime (unix seconds) from GET /api/drop - show local.
                qint64 mtime = f.value("mtime").toLongLong();
                if (mtime > 0) {
                    QDateTime dt = QDateTime::fromTime_t(
                            static_cast<uint>(mtime));
                    f["mtime_text"] = dt.toLocalTime()
                            .toString(QLatin1String("yyyy-MM-dd hh:mm"));
                } else {
                    f["mtime_text"] = QString();
                }
                // Stamp api so a list-item context action can delete.
                f["api"] = QVariant::fromValue<QObject *>(this);
                m_dropFiles.append(f);
            }
            m_dropStatus = m_dropFiles.isEmpty()
                    ? tr("Empty - ask the agent to copy files into the drop folder")
                    : QString();
        } else {
            m_dropFiles.clear();
            m_dropStatus = httpErrorText(httpStatus, data, parseOk, networkError);
        }
        m_dropRev++;
        emit dropChanged();
        return;
    }

    if (kind == "drop_dl") {
        // Binary body - ignore JSON parse. Cap size so a huge reply can't
        // blow the phone's RAM (daemon already enforces max_drop_mb).
        const QString name = reply->property("dropName").toString();
        if (!networkError.isEmpty() || httpStatus < 200 || httpStatus >= 300) {
            // Error body is usually JSON {"error": "..."}.
            QString err = networkError;
            if (err.isEmpty()) {
                if (parseOk)
                    err = data.toMap().value("error").toString();
                if (err.isEmpty())
                    err = tr("HTTP %1").arg(httpStatus);
            }
            m_dropStatus = tr("Download failed: %1").arg(err);
            m_dropRev++;
            emit dropChanged();
            return;
        }
        if (body.size() > MAX_DROP_BYTES) {
            m_dropStatus = tr("File too large (max 64 MB)");
            m_dropRev++;
            emit dropChanged();
            return;
        }
        m_dropLocalDir = dropDownloadDir();
        QDir().mkpath(m_dropLocalDir);
        const QString localName = safeLocalFileName(name);
        const QString dest = QDir(m_dropLocalDir).absoluteFilePath(localName);
        // Same name again replaces the previous download (no -1/-2 suffixes).
        if (QFile::exists(dest) && !QFile::remove(dest)) {
            m_dropStatus = tr("Could not replace %1").arg(localName);
            m_dropRev++;
            emit dropChanged();
            return;
        }
        QFile out(dest);
        if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            m_dropStatus = tr("Could not write %1").arg(dest);
            m_dropRev++;
            emit dropChanged();
            return;
        }
        if (out.write(body) != body.size()) {
            out.close();
            out.remove();
            m_dropStatus = tr("Write incomplete: %1").arg(dest);
            m_dropRev++;
            emit dropChanged();
            return;
        }
        out.close();
        m_dropStatus = tr("Saved %1 (%2)").arg(QFileInfo(dest).fileName(),
                                               formatBytes(body.size()));
        m_dropRev++;
        emit dropChanged();
        // Heap toast: show() is async; a stack object would die first.
        bb::system::SystemToast *toast = new bb::system::SystemToast(this);
        toast->setBody(tr("Saved to Downloads/Inbox"));
        QObject::connect(toast,
                         SIGNAL(finished(bb::system::SystemUiResult::Type)),
                         toast, SLOT(deleteLater()));
        toast->show();
        return;
    }

    if (kind == "drop_del") {
        if (ok) {
            // Refresh the list so the deleted row vanishes.
            fetchDropFiles();
        } else {
            m_dropStatus = tr("Delete failed: ")
                    + httpErrorText(httpStatus, data, parseOk, networkError);
            m_dropRev++;
            emit dropChanged();
        }
        return;
    }

    if (kind == "tui") {
        m_tuiInFlight = false;
        if (!m_tuiOpen)
            return;
        if (reply->property("sid").toString() != m_currentSessionId)
            return;
        if (!ok) {
            m_tuiLive = false;
            m_tuiAttached = false;
            m_tuiStatus = tr("Error");
            m_tuiText = httpErrorText(httpStatus, data, parseOk, networkError);
            m_tuiRev++;
            emit tuiChanged();
            return;
        }
        const QVariantMap map = data.toMap();
        const bool attached = map.value("attached").toBool();
        const qint64 seq = map.value("seq").toLongLong();
        const QString err = map.value("error").toString();
        const QString text = map.value("text").toString();
        const QString jobId = map.value("job_id").toString();
        if (!attached) {
            m_tuiAttached = false;
            m_tuiLive = false;
            m_tuiStatus = err.isEmpty() ? tr("No host TUI attached") : err;
            if (m_tuiSeq == 0) {
                m_tuiText = err.isEmpty()
                        ? tr("No interactive TUI for this session. Start a turn in Interactive mode.")
                        : err;
                m_tuiRev++;
                emit tuiChanged();
            } else {
                m_tuiRev++;
                emit tuiChanged();
            }
            return;
        }
        m_tuiAttached = true;
        m_tuiLive = true;
        if (seq != m_tuiSeq || m_tuiSeq == 0) {
            m_tuiSeq = seq;
            // Default GET …/tui is already plain (no ?ansi=1). Keep a light
            // residual-escape strip for older daemons that always sent SGR.
            QString plain = text;
            plain.remove(QRegExp(QString::fromLatin1("\x1b\\][^\x07\x1b]*(?:\x07|\x1b\\\\)")));
            plain.remove(QRegExp(QString::fromLatin1("\x1b\\[[0-9;:<=>?]*[ -/]*[@-~]")));
            plain.remove(QRegExp(QString::fromLatin1("\x1b.")));
            plain.replace(QLatin1String("\r\n"), QLatin1String("\n"));
            plain.replace(QLatin1Char('\r'), QLatin1Char('\n'));
            m_tuiText = plain.isEmpty() ? tr("(empty pane)") : plain;
        }
        // ASCII only in status — TitleBar mojibakes UTF-8 middle dots.
        if (!jobId.isEmpty())
            m_tuiStatus = tr("job %1").arg(jobId.left(8));
        else
            m_tuiStatus = tr("Live");
        m_tuiRev++;
        emit tuiChanged();
        return;
    }

    if (kind == "tui_keys") {
        // Next pollTui tick refreshes the pane; surface hard failures.
        if (!ok && m_tuiOpen) {
            m_tuiStatus = tr("Key send failed: ")
                    + httpErrorText(httpStatus, data, parseOk, networkError);
            m_tuiRev++;
            emit tuiChanged();
        }
        return;
    }

    // kind == "stop" / "permission" / "question": nothing to do - the next job poll
    // reconciles status and pending_permission from the snapshot.
}

void ApiClient::handleMessages(QNetworkReply *reply, const QVariant &data)
{
    // Ignore responses for a session the user has already navigated away from.
    if (reply->property("sid").toString() != m_currentSessionId)
        return;
    const bool older = reply->property("older").toBool();

    // Round trip: send -> reply arrived. Covers daemon work + transfer,
    // NOT the local block-building that follows.
    const qint64 tSend = reply->property("t_send").toLongLong();
    const qint64 netMs = tSend > 0
            ? QDateTime::currentMSecsSinceEpoch() - tSend : -1;

    QVariantMap payload = data.toMap();
    QVariantList raw = payload["messages"].toList();

    // Local build: block -> display item, including RichPaint rasterizing
    // each rich block to a PNG (the heaviest client-side step).
    if (m_richPaint)
        m_richPaint->resetProfile();
    const qint64 tBuild = QDateTime::currentMSecsSinceEpoch();
    QVariantList items;
    for (int i = 0; i < raw.size(); ++i)
        appendMessageItemsFor(items, raw.at(i).toMap());
    const qint64 buildMs = QDateTime::currentMSecsSinceEpoch() - tBuild;

    if (older) {
        QVariantList merged = items;
        merged += m_messages;
        m_messages = merged;
        m_earliestOffset = payload["offset"].toInt();
        bumpMessages(false);
    } else {
        m_messages = items;
        m_earliestOffset = payload["offset"].toInt();
        bumpMessages(true);
    }
    if (!m_jobEndStatus.isEmpty()) {
        setTranscriptStatus(m_jobEndStatus);
        m_jobEndStatus.clear();
    } else {
        setTranscriptStatus(QString());
    }
    reportLoadTiming(payload["timing"].toMap(), netMs, buildMs, raw.size());
}

// Break the transcript-open cost into daemon / network / client phases and
// send it to the daemon's client-timing.log (fire-and-forget) for off-device
// analysis. Not shown in the UI - the status bar returns to normal.
void ApiClient::reportLoadTiming(const QVariantMap &t, qint64 netMs,
                                 qint64 buildMs, int count)
{
    const double parse = t.value("parse_ms").toDouble();
    const double render = t.value("render_ms").toDouble();
    const double ser = t.value("serialize_ms").toDouble();
    const double srv = parse + render + ser;
    const int bodyKb = t.value("body_bytes").toInt() / 1024;
    const int fileKb = t.value("file_bytes").toInt() / 1024;
    const int total = t.value("count_total").toInt();
    qint64 xfer = -1;
    if (netMs >= 0) {
        xfer = netMs - (qint64) (srv + 0.5);
        if (xfer < 0)
            xfer = 0;
    }
    QString xf = xfer >= 0 ? QString::number(xfer) : QString("?");
    QString line = QString("loaded %1 msgs in %2ms: daemon %3 (parse %4 + "
                           "render %5 + ser %6), net %7, build %8 | %9KB body, "
                           "%10KB file, %11 total")
            .arg(count).arg(netMs >= 0 ? netMs + buildMs : buildMs)
            .arg((qint64) (srv + 0.5)).arg((qint64) parse).arg((qint64) render)
            .arg((qint64) ser).arg(xf).arg(buildMs).arg(bodyKb).arg(fileKb)
            .arg(total);

    // Break the client build cost into RichPaint sub-phases (cache-lookup
    // directory glob vs stb rasterize vs PNG encode+write) for the log.
    if (m_richPaint)
        line += QString(" [%1]").arg(m_richPaint->profileSummary());

    // Fire-and-forget to the daemon log; the reply is ignored.
    QVariantMap logBody;
    logBody["line"] = line;
    logBody["app"] = QString("%1 %2").arg(QLatin1String(BRAND_APP_NAME),
                                          QLatin1String(BRAND_VERSION));
    post("/api/clientlog", logBody, "clientlog");
}

// ---------------------------------------------------------------- helpers

void ApiClient::appendLiveItem(const QVariantMap &item)
{
    m_messages.append(item);
    bumpMessages(true);
}

void ApiClient::setTranscriptStatus(const QString &text)
{
    if (m_transcriptStatus == text)
        return;
    m_transcriptStatus = text;
    emit transcriptStatusChanged();
}

void ApiClient::setJobTicker(const QString &text)
{
    if (m_jobTicker == text)
        return;
    m_jobTicker = text;
    emit jobTickerChanged();
}

// ------------------------------------------------------- status stream

void ApiClient::onStatusFrame(const QByteArray &payload)
{
    bool ok = false;
    QVariant data = parseBody(payload, &ok);
    if (!ok)
        return;
    m_activeStatuses = data.toMap()["active"].toList();

    // Keep the session->job map fresh for every session the daemon reports
    // (lets a later openTranscript re-attach without a scan), and adopt the
    // open session's job if we are not tracking one - e.g. after an app
    // restart or when the job was started from another device.
    QVariantMap tracked;
    bool trackedFound = false;
    for (int i = 0; i < m_activeStatuses.size(); ++i) {
        QVariantMap s = m_activeStatuses.at(i).toMap();
        QString jobId = s.value("job_id").toString();
        QString sid = s.value("session_id").toString();
        QString fork = s.value("new_session_id").toString();
        if (jobId.isEmpty())
            continue;
        // Never map synthetic tui-* ids into m_sessionJobs — that made
        // openTranscript re-attach a fake job and POST /input into a 404.
        if (!isSyntheticJobId(jobId)) {
            if (!sid.isEmpty())
                m_sessionJobs[sid] = jobId;
            if (!fork.isEmpty())
                m_sessionJobs[fork] = jobId;
        }
        bool matchesOpen = !m_currentSessionId.isEmpty()
                && (sid == m_currentSessionId || fork == m_currentSessionId);
        if (matchesOpen && m_jobId.isEmpty() && !m_awaitingJob
                && !isSyntheticJobId(jobId))
            attachToJob(jobId);
        if (!m_jobId.isEmpty() && jobId == m_jobId) {
            tracked = s;
            trackedFound = true;
        }
    }

    // Doorbell: only fetch /api/jobs/<id> when this frame proves there is
    // something new - events past our cursor, a queue/permission change
    // (those may not append events), or the job leaving the active list
    // (it ended; fetch the final snapshot).
    if (!m_jobId.isEmpty()) {
        if (trackedFound) {
            int nextSeq = tracked.contains("next_seq")
                    ? tracked.value("next_seq").toInt() : -1;
            int queued = tracked.value("queued_count").toInt();
            bool perm = tracked.value("pending_permission").toBool()
                    || tracked.value("pending_question").toBool();
            bool ring = (nextSeq > m_since)
                    || (m_wsJobSeen
                        && (queued != m_wsQueuedCount || perm != m_wsPendingPerm));
            m_wsNextSeq = nextSeq; // -1 (old daemon) keeps plain polling
            m_wsQueuedCount = queued;
            m_wsPendingPerm = perm;
            m_wsJobSeen = true;
            m_wsFrameMs = QDateTime::currentMSecsSinceEpoch();
            if (ring) {
                m_wsDoorbell = true;
                pollJob();
            }
        } else if (m_wsJobSeen) {
            m_wsJobSeen = false;
            m_wsNextSeq = -1;
            m_wsDoorbell = true;
            pollJob();
        }
    }

    updateWorkingSet();
    recomputeLiveStatus();
}

// Which sessions are being worked on daemon-wide (for the list page). On
// the unified variant this spans EVERY profile's daemon: the active one via
// m_statusSocket, the rest via their own StatusSse streams.
void ApiClient::updateWorkingSet()
{
    QSet<QString> fresh;
    for (int i = 0; i < m_activeStatuses.size(); ++i) {
        QVariantMap s = m_activeStatuses.at(i).toMap();
        QString sid = s.value("session_id").toString();
        QString fork = s.value("new_session_id").toString();
        if (!sid.isEmpty())
            fresh.insert(sid);
        if (!fork.isEmpty())
            fresh.insert(fork);
    }
    int count = m_activeStatuses.size();
#ifdef VARIANT_UNIFIED
    // Session ids are UUIDs, so a flat id set is collision-safe in practice
    // even across daemons.
    QHash<int, QVariantList>::const_iterator it = m_extraStatuses.constBegin();
    for (; it != m_extraStatuses.constEnd(); ++it) {
        const QVariantList &jobs = it.value();
        count += jobs.size();
        for (int i = 0; i < jobs.size(); ++i) {
            QVariantMap s = jobs.at(i).toMap();
            QString sid = s.value("session_id").toString();
            QString fork = s.value("new_session_id").toString();
            if (!sid.isEmpty())
                fresh.insert(sid);
            if (!fork.isEmpty())
                fresh.insert(fork);
        }
    }
#endif
    bool setChanged = (fresh != m_workingSet);
    bool countChanged = (count != m_workingCount);
    m_workingSet = fresh;
    m_workingCount = count;
    if (setChanged)
        annotateWorkingSessions(true);
    if (setChanged || countChanged)
        emit workingChanged();
}

void ApiClient::annotateWorkingSessions(bool bumpRev)
{
    bool changed = false;
    for (int i = 0; i < m_sessions.size(); ++i) {
        QVariantMap s = m_sessions.at(i).toMap();
        bool working = m_workingSet.contains(s.value("id").toString());
        if (s.value("working").toBool() != working) {
            s["working"] = working;
            m_sessions.replace(i, s);
            changed = true;
        }
    }
    if (changed && bumpRev) {
        m_sessionsRev++;
        emit sessionsChanged();
    }
}

void ApiClient::decorateSessionRow(QVariantMap &s) const
{
    // "main · 2h ago" for the list row's status column. Unified rows carry
    // their daemon identity too: "Mac Claude · main · 2h ago" - in a merged
    // list, WHICH machine answers is the fact the row must not hide.
    QString branch = s.value("git_branch").toString();
    QString when = timeAgo(s.value("last_active").toString());
    QString line = branch;
    if (!branch.isEmpty() && !when.isEmpty())
        line += QString::fromUtf8(" \xC2\xB7 ");
    line += when;
    const QString profileName = s.value("profileName").toString();
    if (!profileName.isEmpty()) {
        line = line.isEmpty()
                ? profileName
                : profileName + QString::fromUtf8(" \xC2\xB7 ") + line;
    }
    // Focus state tag, appended to the same status line rather than given a
    // row of its own: the board is a filter over this list, not a new layout.
    // Focus mode only — in the All list it is noise on rows the human never
    // enrolled, and "working" already has the blinking dot.
    QString bstate = m_focusMode ? s.value("focus_state").toString()
                                 : QString();
    // A finished turn is worth flagging only until you have opened it. The row
    // is one Label in one colour, so "lit vs dim" becomes "shown vs omitted" —
    // the same signal the other clients carry as brightness.
    if (bstate == QLatin1String("turn_finished")
            && !s.value("focus_unread").toBool()) {
        bstate.clear();
    }
    if (!bstate.isEmpty()) {
        const QString label = focusStateLabel(bstate);
        if (!label.isEmpty()) {
            line = line.isEmpty()
                    ? label
                    : line + QString::fromUtf8(" \xC2\xB7 ") + label;
        }
    }
    s["status_line"] = line;
    s["working"] = false;
    // Bake the client into the row: a contextActions ActionItem can only
    // reach C++ via ListItemData (ListItem.view and _api are both null).
    s["api"] = QVariant::fromValue<QObject *>(const_cast<ApiClient *>(this));

    // Full-text search: wrap the query in brand-accent HTML so the list
    // can show highlighted title + snippet. Cascades Html labels must NOT
    // set textStyle.color (it overrides <font color>), so every span is
    // colored explicitly.
    const QString q = m_searchQuery;
    if (q.isEmpty()) {
        s["title_html"] = QString();
        s["snippet_html"] = QString();
        s["snippet"] = QString();
        return;
    }
    // Unified rows highlight in their own provider's accent.
    const QString accent = s.contains("accent")
            ? s.value("accent").toString()
            : QString::fromLatin1(BRAND_ACCENT_COLOR);
    // Bright yellow reads on the black list for both Claude orange and Grok cyan.
    const QString hi = QLatin1String("#ffcc33");
    const QString muted = QLatin1String("#9a9a9a");
    s["title_html"] = highlightHtml(s.value("title").toString(), q, accent, hi);
    QString snippet = s.value("snippet").toString();
    if (snippet.isEmpty())
        snippet = s.value("last_text").toString();
    s["snippet"] = snippet;
    s["snippet_html"] = highlightHtml(snippet, q, muted, hi);
}

QString ApiClient::escapeHtml(const QString &text)
{
    QString out;
    out.reserve(text.size() + 8);
    for (int i = 0; i < text.size(); ++i) {
        const QChar c = text.at(i);
        if (c == QLatin1Char('&'))
            out += QLatin1String("&amp;");
        else if (c == QLatin1Char('<'))
            out += QLatin1String("&lt;");
        else if (c == QLatin1Char('>'))
            out += QLatin1String("&gt;");
        else if (c == QLatin1Char('"'))
            out += QLatin1String("&quot;");
        else
            out += c;
    }
    return out;
}

QString ApiClient::highlightHtml(const QString &text, const QString &query,
                                 const QString &baseColor, const QString &hiColor)
{
    if (text.isEmpty())
        return QString();
    if (query.isEmpty()) {
        return QString::fromLatin1("<font color=\"%1\">%2</font>")
                .arg(baseColor, escapeHtml(text));
    }

    const QString lowerText = text.toLower();
    const QString lowerQuery = query.toLower();
    const int qLen = lowerQuery.size();
    if (qLen <= 0)
        return QString::fromLatin1("<font color=\"%1\">%2</font>")
                .arg(baseColor, escapeHtml(text));

    QString html;
    html.reserve(text.size() * 2 + 64);
    int pos = 0;
    while (pos < text.size()) {
        const int hit = lowerText.indexOf(lowerQuery, pos);
        if (hit < 0) {
            html += QString::fromLatin1("<font color=\"%1\">%2</font>")
                    .arg(baseColor, escapeHtml(text.mid(pos)));
            break;
        }
        if (hit > pos) {
            html += QString::fromLatin1("<font color=\"%1\">%2</font>")
                    .arg(baseColor, escapeHtml(text.mid(pos, hit - pos)));
        }
        html += QString::fromLatin1("<font color=\"%1\"><b>%2</b></font>")
                .arg(hiColor, escapeHtml(text.mid(hit, qLen)));
        pos = hit + qLen;
    }
    return html;
}

// Human phrase for what the agent is doing (daemon streams the phase).
// Line 1 only — description. The raw command/path is appended as line 2
// by recomputeLiveStatus when tool_detail differs from phase_detail.
QString ApiClient::phaseLine(const QVariantMap &s) const
{
    const QString agent = statusActor();
    QString phase = s.value("phase").toString();
    QString detail = shortDetail(s.value("phase_detail").toString(), 100);

    if (phase == "thinking")
        return tr("%1 is thinking...").arg(agent);
    if (phase == "writing")
        return tr("%1 is writing...").arg(agent);
    if (phase == "editing")
        return detail.isEmpty() ? tr("Editing files...") : tr("Editing · %1").arg(detail);
    if (phase == "reading")
        return detail.isEmpty() ? tr("Reading files...") : tr("Reading · %1").arg(detail);
    if (phase == "searching")
        return detail.isEmpty() ? tr("Searching the code...") : tr("Searching · %1").arg(detail);
    if (phase == "running")
        return detail.isEmpty() ? tr("Running a command...") : tr("Running · %1").arg(detail);
    if (phase == "browsing")
        return detail.isEmpty() ? tr("Browsing the web...") : tr("Browsing · %1").arg(detail);
    if (phase == "delegating")
        return tr("Delegating to a subagent...");
    if (phase == "asking")
        return detail.isEmpty() ? tr("Waiting for your answer...") : tr("Asking · %1").arg(detail);

    if (!phase.isEmpty() && !detail.isEmpty())
        return phase + QString::fromUtf8(" \xC2\xB7 ") + detail;
    if (!phase.isEmpty())
        return phase;

    // No phase: tool name only (command goes on line 2).
    QString tool = shortDetail(s.value("tool").toString(), 32);
    if (!tool.isEmpty())
        return QString::fromUtf8("\xE2\x9A\x99 ") + tool;
    return workingLine();
}

// Banner line for the open session, derived from the daemon's pushed
// status. Frames arrive ~1/s while anything runs, so this stays fresh
// without polling - including for jobs this client didn't start. Only the
// OPEN session's job is ever considered (parallel sessions never leak).
void ApiClient::recomputeLiveStatus()
{
    QString line;
    QString sig;
    for (int i = 0; i < m_activeStatuses.size(); ++i) {
        QVariantMap s = m_activeStatuses.at(i).toMap();
        bool matchesSession = !m_currentSessionId.isEmpty()
                && (s.value("session_id").toString() == m_currentSessionId
                    || s.value("new_session_id").toString() == m_currentSessionId);
        bool matchesJob = !m_jobId.isEmpty()
                && s.value("job_id").toString() == m_jobId;
        if (!matchesSession && !matchesJob)
            continue;

        if (s.value("pending_permission").toBool()) {
            line = tr("Waiting for permission...");
            sig = "permission";
        } else if (s.value("pending_question").toBool()) {
            line = tr("Waiting for your answer...");
            sig = "question";
        } else {
            line = phaseLine(s);
            // Not the line: that carries the elapsed seconds (changes every
            // frame) and the file being touched, so reading ten files in a
            // row would beep ten times. One beep per kind of work.
            sig = s.value("phase").toString() + "/" + s.value("tool").toString();
        }
        line += QString::fromUtf8("  \xC2\xB7  %1s")
                .arg(s.value("elapsed_s").toInt());
        // Line 2: raw command / path / [attached: …] when distinct from the
        // description (phase_detail). Label is multiline maxLineCount: 2.
        QString cmd = shortDetail(s.value("tool_detail").toString(), 160);
        QString desc = shortDetail(s.value("phase_detail").toString(), 100);
        if (!cmd.isEmpty() && cmd != desc)
            line += QLatin1Char('\n') + cmd;
        break;
    }
    notifyStatus(sig);
    if (line == m_liveStatusLine)
        return;
    m_liveStatusLine = line;
    emit liveStatusChanged();
}

// One blip per distinct status. An empty signature means the open session
// has nothing running: remember that so the next job's first phase beeps.
// Permission / question / plan-approval (surfaced as a question) use the
// rising C5-E5 attention cue instead of the quiet status blip.
void ApiClient::notifyStatus(const QString &signature)
{
    if (signature == m_lastStatusSig)
        return;
    m_lastStatusSig = signature;
    if (signature.isEmpty())
        return;
    if (signature == QLatin1String("permission")
            || signature == QLatin1String("question"))
        m_chime->play(Chime::CueAttention);
    else
        m_chime->play(Chime::CueStatus);
}

void ApiClient::updatePendingPermission(const QVariantMap &snap)
{
    QVariant pv = snap.value("pending_permission");
    if (pv.isNull() || !pv.isValid()) {
        clearPendingPermission();
        return;
    }
    QVariantMap p = pv.toMap();
    QString reqId = p.value("request_id").toString();
    if (reqId.isEmpty()) {
        clearPendingPermission();
        return;
    }
    if (reqId == m_permissionRequestId)
        return; // already showing this one
    m_permissionRequestId = reqId;
    m_permissionTool = p.value("tool_name").toString();
    // Collapse / cap so a multi-line Bash command can't fill the sheet.
    m_permissionDetail = shortDetail(p.value("detail").toString(), 120);
    emit permissionChanged();
}

void ApiClient::clearPendingPermission()
{
    if (m_permissionRequestId.isEmpty())
        return;
    m_permissionRequestId.clear();
    m_permissionTool.clear();
    m_permissionDetail.clear();
    emit permissionChanged();
}

void ApiClient::updatePendingQuestion(const QVariantMap &snap)
{
    QVariant qv = snap.value("pending_question");
    if (qv.isNull() || !qv.isValid()) {
        // Do not clear m_dismissedQuestionIds here — only clear the open panel.
        if (!m_questionRequestId.isEmpty()) {
            m_questionRequestId.clear();
            m_questions.clear();
            emit questionChanged();
        }
        return;
    }
    QVariantMap q = qv.toMap();
    QString reqId = q.value("request_id").toString();
    QVariantList questions = q.value("questions").toList();
    if (reqId.isEmpty() || questions.isEmpty()) {
        if (!m_questionRequestId.isEmpty()) {
            m_questionRequestId.clear();
            m_questions.clear();
            emit questionChanged();
        }
        return;
    }
    if (m_dismissedQuestionIds.contains(reqId))
        return; // user already answered/cancelled this id
    if (reqId == m_questionRequestId)
        return; // already showing this one
    m_questionRequestId = reqId;
    m_questions = questions;
    emit questionChanged();
}

void ApiClient::clearPendingQuestion()
{
    if (!m_questionRequestId.isEmpty())
        m_dismissedQuestionIds.insert(m_questionRequestId);
    if (m_questionRequestId.isEmpty() && m_questions.isEmpty())
        return;
    m_questionRequestId.clear();
    m_questions.clear();
    emit questionChanged();
}

void ApiClient::bumpMessages(bool scrollToEnd)
{
    m_scrollToEnd = scrollToEnd;
    m_messageRev++;
    emit messagesChanged();
}
