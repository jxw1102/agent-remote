#ifndef BRAND_HPP
#define BRAND_HPP

/*!
 * Compile-time branding - the ONLY place the two artifacts differ in code.
 *
 * Build with `qmake VARIANT=grok` (defines VARIANT_GROK) or
 * `qmake VARIANT=claude` (defines VARIANT_CLAUDE, the default). Everything
 * else - pages, transport, rendering - is shared; provider-specific
 * *features* are gated at runtime by the daemon's /api/ping capabilities,
 * not by these constants.
 *
 * QSettings scopes and crash-file names deliberately match what each
 * pre-merge app used, so upgrading an existing install keeps its server
 * URL, token and crash-log continuity.
 */

#if defined(VARIANT_GROK)

#define BRAND_APP_NAME        "Grok Remote"
#define BRAND_AGENT_NAME      "Grok"
#define BRAND_VERSION         "2.0.0"
#define BRAND_SETTINGS_ORG    "Local"
#define BRAND_SETTINGS_APP    "GrokRemote"
#define BRAND_DEFAULT_URL     ""
#define BRAND_DEFAULT_TOKEN   ""
// Grok/X visual language: near-black UI, but the accent/highlight uses the
// app-icon cyan (silver #9a9a9a was too low-contrast to see).
#define BRAND_ACCENT_COLOR    "#00d4ff"
#define BRAND_BANNER_BG       "#0d1a1f"
#define BRAND_BANNER_TEXT     "#00d4ff"
// User-prompt well - a neutral charcoal box, clearly lighter than the
// near-black page (matches the desktop TUI's boxed prompt; no border),
// with the silver chevron marking it as your message.
#define BRAND_USER_WELL       "#2a2a2a"
#define BRAND_LIVE_WELL       "#17171c"
// Transcript rich-text accents. The daemon bakes exactly this palette onto
// the wire, so the Grok build displays it verbatim (remap is a no-op):
// cyan inline code + links, purple headings, muted-purple thought lines.
#define BRAND_RICH_INLINE     "#67e8f9"
#define BRAND_RICH_HEADING    "#c678dd"
#define BRAND_META_THOUGHT    "#9a8fb0"
// Status-cue LED flash (Chime). Cyan matches the Grok accent; done/error
// stay green/red.
#define BRAND_LED_STATUS      bb::device::LedColor::Cyan
#define BRAND_CRASH_FILE      "/grokremote-crash.txt"
#define BRAND_CRASH_PREV_FILE "/grokremote-crash-prev.txt"
#define BRAND_UI_ERROR_FILE   "/grokremote-ui-error.txt"
// Until the first ping answers, assume the daemon has no interactive
// permission prompting (grok's model is up-front --deny/--yolo flags).
#define BRAND_DEFAULT_CAP_PERMISSIONS false
#define BRAND_DEFAULT_CAP_REQUIRES_CWD false
// Default to the host TUI: headless `grok -p` hangs mid-turn too often.
// Switching Interactive off falls back to auto-approving every tool, since
// grok has no interactive permission callback to answer from the phone.
#define BRAND_DEFAULT_PERMISSION_MODE "interactive"
// Level 2: grok gained that default one release after claude, so installs
// sitting at level 1 (headless) get the one-time reset. Bumping only the
// brand whose default changed leaves a deliberate choice on claude intact.
#define BRAND_EXEC_MIGRATION  2
// Grok has no local /api/usage - the menu opens the web usage page instead.
#define BRAND_USAGE_URL       "https://grok.com/?_s=usage"

#elif defined(VARIANT_UNIFIED)

/*
 * Agent Remote - the merged client. One install talks to every
 * agentremoted host; the provider (and therefore the accent, agent name and
 * rich-text palette) is a property of the ACTIVE PROFILE at runtime, not of
 * the build. The constants below are the neutral fallbacks used before the
 * first ping answers / for daemons of an unknown provider; the per-provider
 * palettes live in apiclient.cpp (palForProvider).
 */

#define BRAND_APP_NAME        "Agent Remote"
#define BRAND_AGENT_NAME      "Agent"
#define BRAND_VERSION         "3.0.0"
// Fresh settings scope: this is a NEW app id, installed alongside the
// single-provider ones. (QSettings are per-app sandboxes on BB10, so the
// old apps' profiles cannot be imported - they are re-entered once.)
#define BRAND_SETTINGS_ORG    "bb10d"
#define BRAND_SETTINGS_APP    "AgentRemote"
#define BRAND_DEFAULT_URL     ""
#define BRAND_DEFAULT_TOKEN   ""
// Cascades default primary blue until a provider is known (session
// harness recolors via applyProviderTheme: claude orange / grok cyan /
// codex green). Gray #9aa4b2 looked washed-out as app chrome.
#define BRAND_ACCENT_COLOR    "#00A8DF"
#define BRAND_BANNER_BG       "#0d1a22"
#define BRAND_BANNER_TEXT     "#00A8DF"
#define BRAND_USER_WELL       "#2a2a2a"
#define BRAND_LIVE_WELL       "#14161a"
// Wire palette as-is (identity remap) for unknown providers.
#define BRAND_RICH_INLINE     "#67e8f9"
#define BRAND_RICH_HEADING    "#c678dd"
#define BRAND_META_THOUGHT    "#9a8fb0"
// Neutral status LED; done/error stay green/red (chime.cpp).
#define BRAND_LED_STATUS      bb::device::LedColor::White
#define BRAND_CRASH_FILE      "/agentremote-crash.txt"
#define BRAND_CRASH_PREV_FILE "/agentremote-crash-prev.txt"
#define BRAND_UI_ERROR_FILE   "/agentremote-ui-error.txt"
// Conservative defaults until each daemon's ping is cached per profile.
#define BRAND_DEFAULT_CAP_PERMISSIONS false
#define BRAND_DEFAULT_CAP_REQUIRES_CWD true
#define BRAND_DEFAULT_PERMISSION_MODE "interactive"
#define BRAND_EXEC_MIGRATION  1
// Empty = in-app Usage sheet via each daemon's /api/usage.
#define BRAND_USAGE_URL       ""

#else /* VARIANT_CLAUDE (default) */

#define BRAND_APP_NAME        "Claude Remote"
#define BRAND_AGENT_NAME      "Claude"
#define BRAND_VERSION         "1.0.0"
#define BRAND_SETTINGS_ORG    "bb10d"
#define BRAND_SETTINGS_APP    "ClaudeSessions"
#define BRAND_DEFAULT_URL     ""
#define BRAND_DEFAULT_TOKEN   ""
// Anthropic visual language: warm orange accent family.
#define BRAND_ACCENT_COLOR    "#d97757"
#define BRAND_BANNER_BG       "#2a1f1a"
#define BRAND_BANNER_TEXT     "#e8a088"
// User-prompt well - a neutral charcoal box, clearly lighter than the
// near-black page (matches the desktop TUI's boxed prompt; no border),
// with the orange chevron marking it as your message.
#define BRAND_USER_WELL       "#2a2a2a"
#define BRAND_LIVE_WELL      "#20180f"
// Transcript rich-text accents - the app remaps the daemon's wire palette
// (Grok's cyan/purple) to these: indigo inline code + links, warm-orange
// headings echoing the Claude accent, muted-indigo thought lines.
#define BRAND_RICH_INLINE     "#5a69ee"
#define BRAND_RICH_HEADING    "#e08a5c"
#define BRAND_META_THOUGHT    "#8a92e0"
// Status-cue LED flash (Chime). BB10's LED has no orange; yellow is the
// closest to the Claude accent. Done/error stay green/red.
#define BRAND_LED_STATUS      bb::device::LedColor::Yellow
#define BRAND_CRASH_FILE      "/clauderemote-crash.txt"
#define BRAND_CRASH_PREV_FILE "/clauderemote-crash-prev.txt"
#define BRAND_UI_ERROR_FILE   "/clauderemote-ui-error.txt"
#define BRAND_DEFAULT_CAP_PERMISSIONS true
#define BRAND_DEFAULT_CAP_REQUIRES_CWD true
// Claude defaults to the interactive host TUI: claude.ai connectors only
// work there, and every tool auto-runs. Headless is the opt-out.
#define BRAND_DEFAULT_PERMISSION_MODE "interactive"
// Level 1: shipped with the interactive/headless toggle (bar 1.1.3).
#define BRAND_EXEC_MIGRATION  1
// Empty = in-app Usage sheet via daemon /api/usage (claude subscription).
#define BRAND_USAGE_URL       ""

#endif

// The daemon always bakes Grok's TUI palette into rich text on the wire
// (render_blocks.py: COLOR_INLINE_CODE / COLOR_HEADING / COLOR_META_THOUGHT).
// ApiClient::remapAccent() rewrites these to the BRAND_RICH_* above - a
// no-op on the Grok build, where the two palettes are identical. Keep in
// sync with the daemon constants.
#define WIRE_RICH_INLINE      "#67e8f9"
#define WIRE_RICH_HEADING     "#c678dd"
#define WIRE_META_THOUGHT     "#9a8fb0"

#endif // BRAND_HPP
