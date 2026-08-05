#ifndef APICLIENT_HPP
#define APICLIENT_HPP

#include <QHash>
#include <QMap>
#include <QNetworkAccessManager>
#include <QObject>
#include <QSet>
#include <QString>
#include <QStringList>
#include <QTimer>
#include <QVariant>
#include <QVariantList>
#include <QVariantMap>

// Status stream transport:
//   - WebSocket (StatusSocket): raw QTcpSocket, plain HTTP only.
//   - SSE (StatusSse): QNetworkAccessManager, works over http and https.
// Unified Agent Remote profiles are often https:// (Cloudflare etc.), so the
// primary stream must be SSE — otherwise workingCount stays 0 and the home
// "N sessions working" strip never appears. Single-provider variants can
// still force SSE with -DUSE_SSE_STATUS if needed.
#if defined(VARIANT_UNIFIED)
#ifndef USE_SSE_STATUS
#define USE_SSE_STATUS
#endif
#endif
//#define USE_SSE_STATUS

// StatusSse is always compiled (secondary per-profile streams + optional
// primary). WebSocket is only pulled in when the primary is not SSE.
#include "statussse.hpp"
#ifndef USE_SSE_STATUS
#include "statussocket.hpp"
#endif

class Chime;
class QNetworkReply;
class RichPaint;

/*!
 * All communication with agentremoted (claude / grok / codex harnesses —
 * the same HTTP API; /api/ping reports which ones and their capabilities).
 *
 * State is exposed as Q_PROPERTYs that QML pages bind to, with plain-int
 * rev counters where a page must rebuild a ListView model imperatively.
 * Dynamically created pages must NOT use Connections{} or JS
 * _api.<signal>.connect(): both are unreliable on BB10 Cascades once the
 * page is destroyed (lesson inherited from the on-device-proven GrokRemote
 * app - see its ChatPage.qml).
 *
 * Sessions run in parallel on the daemon. The client keeps a per-session
 * job map (m_sessionJobs) and only ever polls/banners the job of the OPEN
 * session - switching pages can never show session A's status on session
 * B's transcript. Jobs started elsewhere (another device, an app restart
 * ago) are adopted from the /ws/status stream when their session is opened.
 *
 * The transcript model: ApiClient owns the message list of the currently
 * open session, including live items appended while a job runs; QML
 * re-fills its ArrayDataModel whenever messageRev bumps. Rich blocks are
 * rasterized to PNGs by RichPaint (stb_truetype) and shown as ImageViews -
 * the paint pipeline GrokRemote proved on device; any block that fails to
 * paint falls back to a Cascades Html Label row.
 */
class ApiClient : public QObject
{
    Q_OBJECT
    // Branding exposed to QML. On the single-provider variants these are
    // compile-time constants; on the unified variant the agent name and the
    // whole accent family follow the ACTIVE PROFILE's provider, so they
    // notify on capsChanged (fires on ping replies and profile switches).
    Q_PROPERTY(QString brandName READ brandName CONSTANT)
    Q_PROPERTY(QString agentName READ agentName NOTIFY capsChanged)
    Q_PROPERTY(QString brandVersion READ brandVersion CONSTANT)
    Q_PROPERTY(QString accentColor READ accentColor NOTIFY capsChanged)
    Q_PROPERTY(QString themeBannerBg READ themeBannerBg NOTIFY capsChanged)
    Q_PROPERTY(QString themeBannerText READ themeBannerText NOTIFY capsChanged)
    Q_PROPERTY(QString themeUserWell READ themeUserWell CONSTANT)
    Q_PROPERTY(QString themeLiveWell READ themeLiveWell NOTIFY capsChanged)
    Q_PROPERTY(QString themeHeading READ themeHeading NOTIFY capsChanged)
    Q_PROPERTY(QString themeMetaThought READ themeMetaThought NOTIFY capsChanged)
    // True on the AgentRemote build: QML gates the profile chips / "runs on"
    // labels on this instead of sniffing the brand name.
    Q_PROPERTY(bool unified READ isUnified CONSTANT)
    // Device pixel width (Classic/Q20 = 720, Passport = 1440). List rows need
    // an explicit preferredWidth (Fill is ignored on item roots); paint PNGs
    // use screen-derived widths so Passport isn't stuck at half-screen.
    Q_PROPERTY(int screenWidth READ screenWidth CONSTANT)
    // True on wider devices (Passport); QML bumps a few FontSizes slightly.
    Q_PROPERTY(bool largeDisplay READ largeDisplay CONSTANT)
    // Connection profiles: [{name, baseUrl, token}], one active at a time.
    Q_PROPERTY(QVariantList profiles READ profiles NOTIFY profilesChanged)
    Q_PROPERTY(int activeProfileIndex READ activeProfileIndex NOTIFY profilesChanged)
    // Active profile's connection (kept for existing bindings).
    Q_PROPERTY(QString baseUrl READ baseUrl NOTIFY settingsChanged)
    Q_PROPERTY(QString token READ token NOTIFY settingsChanged)
    Q_PROPERTY(bool configured READ configured NOTIFY settingsChanged)
    // Daemon capabilities (defaults from brand.hpp until /api/ping answers).
    Q_PROPERTY(bool capPermissions READ capPermissions NOTIFY capsChanged)
    Q_PROPERTY(bool capRequiresCwd READ capRequiresCwd NOTIFY capsChanged)
    Q_PROPERTY(bool capSetModel READ capSetModel NOTIFY capsChanged)
    // Reasoning-effort is settable (grok --effort; false for claude -p).
    Q_PROPERTY(bool capSetEffort READ capSetEffort NOTIFY capsChanged)
    // Subscription usage view is available (claude only; the "Usage" menu
    // item is gated on this).
    Q_PROPERTY(bool capShowUsage READ capShowUsage NOTIFY capsChanged)
    // The daemon can run turns in a host tmux TUI ("interactive" mode).
    Q_PROPERTY(bool capInteractive READ capInteractive NOTIFY capsChanged)
    // Live TUI pane capture/keys (daemon ≥ 2.4; falls back to interactive).
    Q_PROPERTY(bool capLiveTui READ capLiveTui NOTIFY capsChanged)
    // "/rewind N" works in that TUI (claude only; grok's takes a prompt).
    Q_PROPERTY(bool capRewind READ capRewind NOTIFY capsChanged)
    // Same flag resolved for the open session's harness — what the long-press
    // action binds to, so the entry is gone on a codex session.
    Q_PROPERTY(bool canRewindHere READ sessionCanRewind NOTIFY currentSessionChanged)
    // GrokRemote: Usage opens the browser to BRAND_USAGE_URL instead of the
    // in-app sheet. Always true on the Grok build; false on Claude.
    Q_PROPERTY(bool usageOpensBrowser READ usageOpensBrowser CONSTANT)
    // Slash commands the daemon offers (authed ping); "/" panel lists them.
    Q_PROPERTY(QStringList slashCommands READ slashCommands NOTIFY capsChanged)
    // Model / effort names the daemon offers for the pickers (authed ping).
    Q_PROPERTY(QStringList models READ models NOTIFY capsChanged)
    Q_PROPERTY(QStringList efforts READ efforts NOTIFY capsChanged)
    // Model the OPEN session last ran on (from its summary; "" if unknown).
    Q_PROPERTY(QString sessionModel READ sessionModel NOTIFY currentSessionChanged)
    // Sent with the next prompt: "" / "default" = daemon default. Persisted;
    // the drag-down "Session" menu writes them.
    Q_PROPERTY(QString modelOverride READ modelOverride WRITE setModelOverride NOTIFY settingsChanged)
    Q_PROPERTY(QString effortOverride READ effortOverride WRITE setEffortOverride NOTIFY settingsChanged)
    // Progress cues (Chime): beep on media volume / LED flash. Persisted,
    // toggled from the Session sheet.
    Q_PROPERTY(bool soundCues READ soundCues WRITE setSoundCues NOTIFY settingsChanged)
    Q_PROPERTY(bool ledCues READ ledCues WRITE setLedCues NOTIFY settingsChanged)
    // Connection test (Settings sheet). 0 idle, 1 testing, 2 ok, 3 failed.
    Q_PROPERTY(int pingState READ pingState NOTIFY pingChanged)
    Q_PROPERTY(QString pingInfo READ pingInfo NOTIFY pingChanged)
    // Subscription usage buckets for the Usage sheet. Each entry:
    // {title, percent, resets_text, severity}. usageRev bumps on each fetch
    // so the sheet rebuilds; usageStatus carries "Loading..." / an error.
    Q_PROPERTY(int usageRev READ usageRev NOTIFY usageChanged)
    Q_PROPERTY(QVariantList usageBuckets READ usageBuckets NOTIFY usageChanged)
    Q_PROPERTY(QString usageStatus READ usageStatus NOTIFY usageChanged)
    // Projects page
    Q_PROPERTY(int projectsRev READ projectsRev NOTIFY projectsChanged)
    Q_PROPERTY(QVariantList projects READ projects NOTIFY projectsChanged)
    Q_PROPERTY(QString projectsStatus READ projectsStatus NOTIFY projectsChanged)
    // Sessions page (project filter lives here so pages stay stateless)
    Q_PROPERTY(int sessionsRev READ sessionsRev NOTIFY sessionsChanged)
    Q_PROPERTY(QVariantList sessions READ sessions NOTIFY sessionsChanged)
    Q_PROPERTY(QString sessionsStatus READ sessionsStatus NOTIFY sessionsChanged)
    // Non-empty while a full-text search is active (results replace the list).
    Q_PROPERTY(QString searchQuery READ searchQuery NOTIFY sessionsChanged)
    Q_PROPERTY(QString projectFilter READ projectFilter NOTIFY filterChanged)
    Q_PROPERTY(QString projectFilterName READ projectFilterName NOTIFY filterChanged)
    // How many sessions the daemon is working on right now (all sessions,
    // not just the open one) - the sessions list shows the count.
    Q_PROPERTY(int workingCount READ workingCount NOTIFY workingChanged)
    // Transcript of the open session + live job feed
    Q_PROPERTY(int messageRev READ messageRev NOTIFY messagesChanged)
    Q_PROPERTY(QVariantList messages READ messages NOTIFY messagesChanged)
    Q_PROPERTY(bool canLoadOlder READ canLoadOlder NOTIFY messagesChanged)
    Q_PROPERTY(bool loadingOlder READ loadingOlder NOTIFY loadingOlderChanged)
    Q_PROPERTY(bool scrollToEndHint READ scrollToEndHint NOTIFY messagesChanged)
    Q_PROPERTY(QString transcriptStatus READ transcriptStatus NOTIFY transcriptStatusChanged)
    Q_PROPERTY(QString currentSessionId READ currentSessionId NOTIFY currentSessionChanged)
    // Job of the OPEN session only.
    Q_PROPERTY(bool jobRunning READ jobRunning NOTIFY jobRunningChanged)
    // Live "<Agent> is working... 12s" line while a job runs ("" otherwise).
    Q_PROPERTY(QString jobTicker READ jobTicker NOTIFY jobTickerChanged)
    // Daemon-pushed status for the open session (WebSocket /ws/status):
    // covers jobs this client didn't start too, e.g. after an app restart.
    Q_PROPERTY(QString liveStatusLine READ liveStatusLine NOTIFY liveStatusChanged)
    // Prompts queued while a job runs. The queue lives on the daemon
    // (attached to the running job); this mirrors the latest snapshot so
    // queued prompts survive the app dying or losing Wi-Fi.
    Q_PROPERTY(int queuedCount READ queuedCount NOTIFY queueChanged)
    // Interactive turns run in a host TUI that queues its own input, so the
    // daemon queue (and its menu entry) does not apply.
    Q_PROPERTY(bool queueSupported READ queueSupported NOTIFY settingsChanged)
    // Drag-down menu: Live TUI when interactive + open session; Queue when headless.
    Q_PROPERTY(bool liveTuiMenu READ liveTuiMenu NOTIFY settingsChanged)
    Q_PROPERTY(bool liveTuiEnabled READ liveTuiEnabled NOTIFY currentSessionChanged)
    // Live TUI sheet state (poll ~400 ms while open).
    Q_PROPERTY(int tuiRev READ tuiRev NOTIFY tuiChanged)
    Q_PROPERTY(QString tuiText READ tuiText NOTIFY tuiChanged)
    Q_PROPERTY(QString tuiStatus READ tuiStatus NOTIFY tuiChanged)
    Q_PROPERTY(bool tuiAttached READ tuiAttached NOTIFY tuiChanged)
    Q_PROPERTY(bool tuiLive READ tuiLive NOTIFY tuiChanged)
    // [{id, prompt}] - the QueueSheet lists these with a cancel button each.
    Q_PROPERTY(QVariantList queuedPrompts READ queuedPrompts NOTIFY queueChanged)
    // Execution mode: "interactive" (host TUI) or "headless" (CLI). Both
    // always bypass tool permissions. Persisted; wired as interactive /
    // bypassPermissions on the HTTP API.
    Q_PROPERTY(QString permissionMode READ permissionMode WRITE setPermissionMode NOTIFY settingsChanged)
    // A tool use is waiting for the user to Allow/Deny (non-auto modes).
    Q_PROPERTY(bool permissionPending READ permissionPending NOTIFY permissionChanged)
    Q_PROPERTY(QString permissionTool READ permissionTool NOTIFY permissionChanged)
    Q_PROPERTY(QString permissionDetail READ permissionDetail NOTIFY permissionChanged)
    // AskUserQuestion (interactive mode): the agent opened its selection
    // panel in the host TUI and is blocked until we pick. questions is
    // [{question, header, multi_select, options:[{label, description}]}] -
    // the QuestionSheet renders it and posts one label list per question.
    Q_PROPERTY(bool questionPending READ questionPending NOTIFY questionChanged)
    Q_PROPERTY(QVariantList questions READ questions NOTIFY questionChanged)
    // Root page pins this to open the New Session sheet for a project.
    Q_PROPERTY(int newSessionRequestRev READ newSessionRequestRev NOTIFY newSessionRequestChanged)
    Q_PROPERTY(QString newSessionCwd READ newSessionCwd NOTIFY newSessionRequestChanged)
    Q_PROPERTY(QString newSessionProjectName READ newSessionProjectName NOTIFY newSessionRequestChanged)
    // Attachment upload (the "+" button): rev bumps when a file landed on
    // the daemon; the page appends [attached: path] to the prompt field.
    Q_PROPERTY(int attachRev READ attachRev NOTIFY attachChanged)
    Q_PROPERTY(QString lastAttachmentPath READ lastAttachmentPath NOTIFY attachChanged)
    // Host->phone drop folder (agent copies files to drop_path; phone lists
    // and downloads them into shared/downloads). dropRev rebuilds the sheet.
    Q_PROPERTY(int dropRev READ dropRev NOTIFY dropChanged)
    Q_PROPERTY(QVariantList dropFiles READ dropFiles NOTIFY dropChanged)
    Q_PROPERTY(QString dropStatus READ dropStatus NOTIFY dropChanged)
    Q_PROPERTY(QString dropPath READ dropPath NOTIFY dropChanged)
    Q_PROPERTY(QString dropLocalDir READ dropLocalDir NOTIFY dropChanged)
    // Rolling UI error / crash log shown in Settings (GrokRemote feature).
    Q_PROPERTY(QString errorLog READ errorLog NOTIFY errorLogChanged)

public:
    explicit ApiClient(QObject *parent = 0);

    QString brandName() const;
    QString agentName() const;
    QString statusActor() const;   // model name for the status banner
    QString brandVersion() const;
    QString accentColor() const;
    QString themeBannerBg() const;
    QString themeBannerText() const;
    QString themeUserWell() const;
    QString themeLiveWell() const;
    QString themeHeading() const;
    QString themeMetaThought() const;
    bool isUnified() const;

    int screenWidth() const { return m_screenWidth; }
    bool largeDisplay() const { return m_largeDisplay; }

    QVariantList profiles() const { return m_profiles; }
    int activeProfileIndex() const { return m_activeProfile; }
    QString baseUrl() const { return m_baseUrl; }
    QString token() const { return m_token; }
    bool configured() const { return !m_baseUrl.isEmpty() && !m_token.isEmpty(); }
    bool capPermissions() const { return m_capPermissions; }
    bool capRequiresCwd() const { return m_capRequiresCwd; }
    bool capSetModel() const { return m_capSetModel; }
    bool capSetEffort() const { return m_capSetEffort; }
    bool capShowUsage() const { return m_capShowUsage; }
    bool capInteractive() const { return m_capInteractive; }
    bool capLiveTui() const { return m_capLiveTui; }
    bool capRewind() const { return m_capRewind; }
    bool usageOpensBrowser() const;
    QStringList slashCommands() const { return m_slashCommands; }
    QStringList models() const { return m_models; }
    QStringList efforts() const { return m_efforts; }
    QString sessionModel() const { return m_sessionModel; }
    QString modelOverride() const { return m_modelOverride; }
    QString effortOverride() const { return m_effortOverride; }
    bool soundCues() const { return m_soundCues; }
    bool ledCues() const { return m_ledCues; }

    int pingState() const { return m_pingState; }
    QString pingInfo() const { return m_pingInfo; }

    int usageRev() const { return m_usageRev; }
    QVariantList usageBuckets() const { return m_usageBuckets; }
    QString usageStatus() const { return m_usageStatus; }

    int projectsRev() const { return m_projectsRev; }
    QVariantList projects() const { return m_projects; }
    QString projectsStatus() const { return m_projectsStatus; }

    int sessionsRev() const { return m_sessionsRev; }
    QVariantList sessions() const { return m_sessions; }
    QString sessionsStatus() const { return m_sessionsStatus; }
    QString searchQuery() const { return m_searchQuery; }
    QString projectFilter() const { return m_projectFilter; }
    QString projectFilterName() const { return m_projectFilterName; }
    int workingCount() const { return m_workingCount; }

    int messageRev() const { return m_messageRev; }
    QVariantList messages() const { return m_messages; }
    bool canLoadOlder() const { return m_earliestOffset > 0; }
    bool loadingOlder() const { return m_loadingOlder; }
    bool scrollToEndHint() const { return m_scrollToEnd; }
    QString transcriptStatus() const { return m_transcriptStatus; }
    QString currentSessionId() const { return m_currentSessionId; }

    bool jobRunning() const { return m_awaitingJob || !m_jobId.isEmpty(); }
    QString jobTicker() const { return m_jobTicker; }
    QString liveStatusLine() const { return m_liveStatusLine; }
    int queuedCount() const { return m_queued.size() + pendingQueueSize(); }
    QVariantList queuedPrompts() const { return m_queued; }
    bool interactiveMode() const
    { return m_permissionMode == QLatin1String("interactive"); }
    bool queueSupported() const { return !interactiveMode(); }
    // Menu slot shows Live TUI instead of Queue when the user chose Interactive.
    bool liveTuiMenu() const { return interactiveMode(); }
    // Live TUI needs an open session and a daemon that can host a TUI.
    bool liveTuiEnabled() const
    {
        return interactiveMode()
                && !m_currentSessionId.isEmpty()
                && (m_capLiveTui || m_capInteractive);
    }
    int tuiRev() const { return m_tuiRev; }
    QString tuiText() const { return m_tuiText; }
    QString tuiStatus() const { return m_tuiStatus; }
    bool tuiAttached() const { return m_tuiAttached; }
    bool tuiLive() const { return m_tuiLive; }

    QString permissionMode() const { return m_permissionMode; }
    bool permissionPending() const { return !m_permissionRequestId.isEmpty(); }
    QString permissionTool() const { return m_permissionTool; }
    QString permissionDetail() const { return m_permissionDetail; }

    bool questionPending() const { return !m_questionRequestId.isEmpty(); }
    QVariantList questions() const { return m_questions; }

    int newSessionRequestRev() const { return m_newSessionRequestRev; }
    QString newSessionCwd() const { return m_newSessionCwd; }
    QString newSessionProjectName() const { return m_newSessionProjectName; }

    int attachRev() const { return m_attachRev; }
    QString lastAttachmentPath() const { return m_lastAttachmentPath; }

    int dropRev() const { return m_dropRev; }
    QVariantList dropFiles() const { return m_dropFiles; }
    QString dropStatus() const { return m_dropStatus; }
    QString dropPath() const { return m_dropPath; }
    QString dropLocalDir() const { return m_dropLocalDir; }

    QString errorLog() const { return m_errorLog; }

    // Page factories (QmlDocument). ComponentDefinition.createObject() is
    // unreliable on BB10 Cascades - GrokRemote abandoned it on device.
    Q_INVOKABLE QObject *createTranscriptPage();
    Q_INVOKABLE QObject *createProjectsPage();
    // Surface a QML-side error where the user can see it (sessions status).
    Q_INVOKABLE void reportUiError(const QString &message);
    // Copy plain text to the system clipboard (transcript row menu).
    Q_INVOKABLE void copyToClipboard(const QString &text);
    Q_INVOKABLE void clearErrorLog();

    // Profiles: index -1 in saveProfile() appends a new one. Saving or
    // activating a profile makes it the active connection.
    Q_INVOKABLE void saveProfile(int index, const QString &name,
                                 const QString &baseUrl, const QString &token);
    Q_INVOKABLE void deleteProfile(int index);
    // Switch a daemon off without deleting it: it is then never contacted
    // (no socket, ping, sessions, drop or usage). Absent flag = enabled.
    Q_INVOKABLE void setProfileEnabled(int index, bool on);
    Q_INVOKABLE bool profileEnabled(int profileIndex) const;
    Q_INVOKABLE void activateProfile(int index);
    // Unified: switch the active daemon WITHOUT discarding the merged
    // session list (activateProfile resets everything). Job/transcript
    // state is still daemon-scoped and is cleared. Falls back to
    // activateProfile on the single-provider variants.
    Q_INVOKABLE void switchProfile(int index);
    // Open a merged-list row: switches to the row's profile first when it
    // is not the active one. profileIndex -1 = plain openTranscript.
    Q_INVOKABLE void openSessionRow(int profileIndex, const QString &sessionId);
    // Accent hex for a provider name ("claude"/"grok"/"codex"/other) - the
    // QML profile chips color themselves with this.
    Q_INVOKABLE QString providerAccent(const QString &provider) const;
    // Active profile's harness list (multi daemon → claude/grok/codex).
    Q_INVOKABLE QStringList profileHarnesses() const;
    // requires_cwd for a specific harness on a multi host (else profile caps).
    Q_INVOKABLE bool harnessRequiresCwd(const QString &harness) const;
    bool harnessCap(const QString &harness, const QString &cap,
                    bool fallback) const;
    // Rewind is per harness (claude and grok can, codex cannot), so the UI
    // gates on the OPEN session's harness, not the daemon-level union that
    // /api/ping reports at its root.
    Q_INVOKABLE bool sessionCanRewind() const;
    // Slash commands of the OPEN session's harness (multi daemons list them
    // per provider); the "/" panel and the send gate both use this.
    Q_INVOKABLE QStringList sessionSlashCommands() const;

    Q_INVOKABLE void setPermissionMode(const QString &mode);
    Q_INVOKABLE void setModelOverride(const QString &model);
    Q_INVOKABLE void setEffortOverride(const QString &effort);
    Q_INVOKABLE void setSoundCues(bool on);
    Q_INVOKABLE void setLedCues(bool on);
    Q_INVOKABLE void resolvePermission(bool allow);
    // answers: one entry per question, each a list of chosen option labels
    // (single-select questions carry exactly one). notes: optional free text
    // per question, for options that take one (grok's "Request changes").
    // Cancel Escapes the panel.
    Q_INVOKABLE void resolveQuestion(const QVariantList &answers,
                                     const QVariantList &notes = QVariantList());
    // Rasterize daemon "blocks" (the question body) into transcript-style
    // display rows so a sheet renders markdown natively, not as raw text.
    Q_INVOKABLE QVariantList renderBlocks(const QVariantList &blocks);
    Q_INVOKABLE void cancelQuestion();
    Q_INVOKABLE void ping();
    Q_INVOKABLE void fetchUsage();
    // Open BRAND_USAGE_URL in the system browser (GrokRemote Usage menu).
    Q_INVOKABLE void openUsageInBrowser();
    Q_INVOKABLE void fetchProjects();
    Q_INVOKABLE void setProjectFilter(const QString &projectId, const QString &name);
    // Full-text search over titles + message bodies. Empty query clears and
    // reloads the normal sessions list. Results include title_html / snippet_html
    // with the keyword wrapped in brand-accent <font> tags.
    // scheduleSearchSessions() is what the TextField calls: debounces 50ms so
    // each keystroke doesn't fire a request; empty text clears immediately.
    Q_INVOKABLE void scheduleSearchSessions(const QString &query);
    Q_INVOKABLE void searchSessions(const QString &query);
    Q_INVOKABLE void requestNewSession(const QString &cwd, const QString &projectName);
    Q_INVOKABLE void openTranscript(const QString &sessionId);
    Q_INVOKABLE void refreshTranscript();
    Q_INVOKABLE void loadOlder();
    Q_INVOKABLE void sendPrompt(const QString &prompt);
    // "Rewind to here" on a user row -> /rewind N (interactive mode only).
    Q_INVOKABLE void rewindToRow(int rowId);
    Q_INVOKABLE void cancelQueued(const QString &queueId);
    // provider: multi-harness root requires it (claude|grok|codex); empty =
    // single-provider / path-profile daemons.
    Q_INVOKABLE void startNewSession(const QString &cwd, const QString &prompt,
                                     const QString &provider = QString());
    Q_INVOKABLE void stopJob();
    // Upload a picked file to the daemon; attachRev bumps with the path.
    Q_INVOKABLE void uploadAttachment(const QString &fileUrl);
    // Live TUI (Interactive): poll host tmux pane + inject keys/text.
    Q_INVOKABLE void startLiveTui();
    Q_INVOKABLE void stopLiveTui();
    Q_INVOKABLE void sendTuiKey(const QString &key);
    Q_INVOKABLE void sendTuiLine(const QString &text);
    // Host->phone drop: list / download / delete files the agent staged.
    Q_INVOKABLE void fetchDropFiles();
    Q_INVOKABLE void downloadDropFile(const QString &name);
    Q_INVOKABLE void deleteDropFile(const QString &name);
    // Unified inbox rows carry which daemon holds the file; -1 = the active
    // profile (what the plain calls above do).
    Q_INVOKABLE void downloadDropFrom(int profileIndex, const QString &name);
    Q_INVOKABLE void deleteDropFrom(int profileIndex, const QString &name);

public Q_SLOTS:
    // Slot so applicationui can wire Application::fullscreen() to it.
    void refreshSessions();

Q_SIGNALS:
    void settingsChanged();
    void profilesChanged();
    void capsChanged();
    void usageChanged();
    void jobTickerChanged();
    void liveStatusChanged();
    void workingChanged();
    void pingChanged();
    void projectsChanged();
    void sessionsChanged();
    void filterChanged();
    void messagesChanged();
    void loadingOlderChanged();
    void transcriptStatusChanged();
    void currentSessionChanged();
    void jobRunningChanged();
    void permissionChanged();
    void questionChanged();
    void queueChanged();
    void newSessionRequestChanged();
    void attachChanged();
    void dropChanged();
    void tuiChanged();
    void errorLogChanged();

private Q_SLOTS:
    void onFinished(QNetworkReply *reply);
    void pollJob();
    void pollTui();
    void onStatusFrame(const QByteArray &payload);
    // Frames from the unified variant's secondary per-profile streams;
    // sender()->property("profileIndex") says which daemon spoke.
    void onExtraStatusFrame(const QByteArray &payload);
    void runPendingSearch();

private:
    QObject *createPageFromAsset(const QString &asset);
    QNetworkReply *get(const QString &pathAndQuery, const QString &kind);
    QNetworkReply *post(const QString &path, const QVariantMap &body, const QString &kind);
    QNetworkRequest makeRequest(const QString &pathAndQuery) const;
    QVariant parseBody(const QByteArray &body, bool *ok) const;
    QString httpErrorText(int httpStatus, const QVariant &data, bool parseOk,
                          const QString &networkError) const;

    void fetchMessages(int offset, int limit, bool older);
    void handleMessages(QNetworkReply *reply, const QVariant &data);
    void setLoadingOlder(bool v);
    void reportLoadTiming(const QVariantMap &t, qint64 netMs, qint64 buildMs,
                          int count);
    void handleJobPoll(int httpStatus, const QVariant &data, bool parseOk,
                       const QString &networkError);
    void handleJobEnd(const QString &status, const QString &newSessionId,
                      const QString &error, int droppedQueued);
    void postPrompt(const QString &prompt);
    void postQueuePrompt(const QString &jobId, const QString &prompt);
    void postDirectInput(const QString &jobId, const QString &prompt);
    void attachToJob(const QString &jobId);
    void detachJob();
    void updateQueueFromServer(const QVariantList &queued);
    void removeQueuedEcho(const QString &prompt);
    QString dropQueueNote(int droppedQueued);
    void appendLiveItem(const QVariantMap &item);
    void setTranscriptStatus(const QString &text);
    void setJobTicker(const QString &text);
    void updatePendingPermission(const QVariantMap &snap);
    void clearPendingPermission();
    void updatePendingQuestion(const QVariantMap &snap);
    void clearPendingQuestion();
    void bumpMessages(bool scrollToEnd);
    void recomputeLiveStatus();
    // Beep + blue LED when the agent's phase/tool actually changes (status
    // frames arrive ~1/s; only a new signature is a "new status").
    void notifyStatus(const QString &signature);
    void updateWorkingSet();
    void annotateWorkingSessions(bool bumpRev);
    void decorateSessionRow(QVariantMap &s) const;
    // Escape + wrap case-insensitive matches of query in <font color=hi>.
    static QString highlightHtml(const QString &text, const QString &query,
                                 const QString &baseColor, const QString &hiColor);
    static QString escapeHtml(const QString &text);
    void updateCaps(const QVariantMap &caps);
    QString workingLine() const;
    QString phaseLine(const QVariantMap &s) const;
    int pendingQueueSize() const;

    // Profiles persistence + activation.
    void loadProfiles();
    void persistProfiles();
    void applyActiveProfile(bool resetState);

    // ---- Unified (AgentRemote) plumbing. Compiled everywhere, active
    // behavior gated on VARIANT_UNIFIED inside apiclient.cpp. ----
    // GET against an arbitrary profile's daemon (makeRequest always talks
    // to the ACTIVE profile).
    QNetworkReply *getFrom(const QString &baseUrl, const QString &token,
                           const QString &pathAndQuery, const QString &kind);
    QNetworkReply *postTo(const QString &baseUrl, const QString &token,
                          const QString &path, const QVariantMap &body,
                          const QString &kind);
    // True + fills base/token when the profile row is usable.
    bool profileEndpoint(int profileIndex, QString *baseUrl,
                         QString *token) const;
    // Fan out /api/sessions (query empty) or /api/sessions/search across
    // every configured profile; merge + sort when the last reply lands.
    void startUnifiedFetch(const QString &query);
    void finishUnifiedFetch();
    // Learn provider/caps of the NON-active profiles (cheap /api/ping each).
    void pingProfiles();
    // One StatusSse per non-active profile so working markers cover every
    // daemon, not just the one the open transcript talks to.
    void rebuildExtraStreams();
    // Caps + provider from the profile's cached ping (instant, correct
    // gating on switch; the live ping refreshes it right after).
    void applyCachedCaps(int index);
    void setProvider(const QString &provider);
    // Open-session harness if any, else active profile's provider.
    QString themeProvider() const;
    // Recolor the OS chrome (title separator, caret) to the provider accent.
    void applyProviderTheme();
    // A late provider discovery re-badges that profile's merged-list rows.
    void annotateProviderRows(int profileIndex, const QString &provider);

    // Paint pipeline (RichPaint / stb_truetype -> cached PNG -> ImageView).
    void appendMessageItemsFor(QVariantList &out, const QVariantMap &m);
    void appendRenderedBlocks(QVariantList &out, const QVariantList &blocks, bool live);
    QVariantMap renderRichBlock(const QString &rich, int widthPx, bool code, bool heading);
    // Rewrite the daemon's wire palette (Grok's) to this variant's accents.
    QString remapAccent(const QString &rich) const;
    QVariantMap paintedItem(const QString &rich, const QString &plainText,
                            bool code, bool heading, const QString &lang, bool live);

    // Rolling error log + crash-dump adoption (GrokRemote feature).
    void appendLog(const QString &line);
    void persistErrorLog();
    void loadPersistedError();
    void adoptCrashDump();
    static QString logFilePath();
    static QString crashFilePath();
    static QString crashPrevFilePath();

    QNetworkAccessManager m_nam;
    QTimer m_pollTimer;
    // Live TUI pane poll while the sheet is open (~400 ms).
    QTimer m_tuiTimer;
    bool m_tuiOpen;
    bool m_tuiInFlight;
    qint64 m_tuiSeq;
    int m_tuiRev;
    QString m_tuiText;
    QString m_tuiStatus;
    bool m_tuiAttached;
    bool m_tuiLive;
    // Coalesce session-search keystrokes (50ms after last change).
    QTimer m_searchDebounce;
    QString m_pendingSearchQuery;
#ifdef USE_SSE_STATUS
    StatusSse m_statusSocket;
#else
    StatusSocket m_statusSocket;
#endif
    // Last /ws/status payload's "active" list + the banner line derived
    // from it for the currently open session.
    QVariantList m_activeStatuses;
    QString m_liveStatusLine;
    // Phase/tool signature the last status cue was played for.
    QString m_lastStatusSig;
    // All session ids the daemon is working on (session_id + new_session_id
    // of every active job) - drives workingCount and the list markers.
    QSet<QString> m_workingSet;
    int m_workingCount;

    QVariantList m_profiles;   // [{name, baseUrl, token, provider?, caps?}]
    int m_activeProfile;
    QString m_baseUrl;
    QString m_token;
    // Active profile's provider ("claude"/"grok"/""), cached from ping.
    QString m_provider;

    // Unified merge state: generation counter guards against a refresh (or
    // profile switch) racing an in-flight fan-out; rows accumulate until
    // every profile answered.
    int m_unifiedGen;
    int m_unifiedPending;
    QVariantList m_unifiedRows;
    QStringList m_unifiedErrors;
    QList<StatusSse *> m_extraStreams;         // one per non-active profile
    QHash<int, QVariantList> m_extraStatuses;  // profileIndex -> active jobs

    // Unified inbox merge (same generation-guarded shape as the sessions).
    int m_dropGen;
    int m_dropPending;
    QVariantList m_dropMergedRows;
    QStringList m_dropErrors;
    QStringList m_dropHostPaths;  // "Mac Claude: /Users/…/Public" per daemon

    // Unified usage merge. Progressive on purpose: claude's buckets answer
    // in <1s, grok's daemon resumes a tmux TUI (tens of seconds) - each
    // reply re-renders instead of the fast one waiting for the slow one.
    int m_usageGen;
    int m_usagePending;
    QMap<int, QVariantList> m_usageByProfile;  // ordered by profile index
    QStringList m_usageErrors;
    bool m_capPermissions;
    bool m_capRequiresCwd;
    bool m_capSetModel;
    bool m_capSetEffort;
    bool m_capShowUsage;
    bool m_capInteractive;
    bool m_capLiveTui;
    bool m_capRewind;
    QStringList m_slashCommands;
    QStringList m_models;
    QStringList m_efforts;
    QString m_sessionModel;
    QString m_sessionProvider;  // provider of the OPEN session's daemon
    QString m_modelOverride;
    QString m_effortOverride;
    bool m_soundCues;
    bool m_ledCues;

    int m_pingState;
    QString m_pingInfo;

    // Subscription usage buckets (Usage sheet).
    int m_usageRev;
    QVariantList m_usageBuckets;
    QString m_usageStatus;

    int m_projectsRev;
    QVariantList m_projects;
    QString m_projectsStatus;

    int m_sessionsRev;
    QVariantList m_sessions;
    QString m_sessionsStatus;
    // Active full-text search query ("" = browsing the normal sessions list).
    // Matches the last issued request so stale search replies can be dropped.
    QString m_searchQuery;
    QString m_projectFilter;
    QString m_projectFilterName;

    int m_messageRev;
    QVariantList m_messages;
    int m_earliestOffset;
    bool m_loadingOlder;
    bool m_scrollToEnd;
    QString m_transcriptStatus;
    QString m_jobEndStatus;
    QString m_currentSessionId;

    // Working directory of the open session (from the sessions list / new
    // session form). Used as cwd for "!command" shell escapes.
    QString m_sessionCwd;
    // Shell context: output from "!cmd" to prepend to the next real prompt.
    QString m_shellContext;

    // Job of the open session (only this one is polled/bannered).
    QString m_jobId;
    int m_since;
    int m_pollFailures;
    bool m_pollInFlight;
    bool m_awaitingJob;
    qint64 m_jobStartMs;
    // Status-stream doorbell for the tracked job. The daemon's ~1 Hz status
    // frames carry next_seq; pollJob() skips the HTTP GET while a fresh
    // frame proves the daemon holds nothing past m_since. -1 = unknown
    // (stream down / old daemon / job not yet seen) -> plain polling.
    int m_wsNextSeq;
    qint64 m_wsFrameMs;      // when the tracked job was last seen in a frame
    bool m_wsJobSeen;        // tracked job appeared in at least one frame
    bool m_wsDoorbell;       // frame demands an immediate fetch
    int m_wsQueuedCount;     // last frame's queue/permission view; a change
    bool m_wsPendingPerm;    // rings the doorbell (may not add events)
    void resetWsJobState();
    QString m_jobTicker;
    // Latest "⚙ Tool  detail" line; shown in the ticker while the job runs.
    QString m_jobToolLine;
    // session id -> job id for every job this client started or adopted;
    // switching transcripts re-attaches through this map.
    QMap<QString, QString> m_sessionJobs;
    // Server-side queue mirror: [{id, prompt}] from the last job snapshot.
    QVariantList m_queued;
    // Prompts typed in the short window before a session's job id arrives;
    // flushed to the daemon's queue when the "continue" response lands.
    QMap<QString, QStringList> m_pendingQueue;

    QString m_permissionMode;
    QString m_permissionRequestId;
    QString m_permissionTool;
    QString m_permissionDetail;

    QString m_questionRequestId;
    QVariantList m_questions;

    int m_newSessionRequestRev;
    QString m_newSessionCwd;
    QString m_newSessionProjectName;

    int m_attachRev;
    QString m_lastAttachmentPath;

    // Display metrics (Classic 720 / Passport 1440).
    int m_screenWidth;
    bool m_largeDisplay;
    int m_paintWidthBody;
    int m_paintWidthCode;
    int m_fontBodyPx;
    int m_fontCodePx;
    int m_fontHeadingPx;

    int m_dropRev;
    QVariantList m_dropFiles;
    QString m_dropStatus;
    QString m_dropPath;
    QString m_dropLocalDir;

    RichPaint *m_richPaint;
    Chime *m_chime;
    bool m_richPaintWarned;
    QString m_errorLog;
};

#endif // APICLIENT_HPP
