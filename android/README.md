# Agent Remote (Android)

One Android app for every [`agentremoted`](../bb10-remote/daemon) host you run —
Claude on your Mac, Grok on a VPS, multi-harness, or several profiles at once.
It is the Android counterpart to [Agent Remote](../bb10-remote/) on BlackBerry
and web: the HTTP API is identical for every provider, and `/api/ping` says
which harness is answering and what it can do.

**Daemon install (Mac launchd / Linux systemd):** see
[bb10-remote/README.md](../bb10-remote/README.md#quick-start--run-the-daemon)
and [bb10-remote/daemon/README.md](../bb10-remote/daemon/README.md).

```
Profiles                      Unified session list           Transcript
┌──────────────┐              ┌──────────────────┐           ┌──────────────┐
│ Mac · Claude │──┐           │ ● Mac Claude  ↻  │           │ Claude theme │
│ VPS · Grok   │──┼──ping──▶  │ ● VPS Grok       │──open──▶  │ or Grok theme│
└──────────────┘  │           │ ● Mac Claude     │           └──────────────┘
                  └──SSE────▶ │ 2 working…       │
                              └──────────────────┘
```

## What it does

- **Many daemons, one list.** Every enabled profile is fanned out in parallel
  and merged into a single list sorted by last activity, with a coloured
  provider badge per row. A dead daemon shows an error banner instead of
  silently shrinking the list.
- **New sessions ask which daemon first.** The project list, whether a working
  folder is even required, and the model / effort / execution-mode pickers all
  re-read from the daemon you picked.
- **Live turns.** Each daemon's `/sse/status` stream drives a phase banner
  ("writing · READY · 7 s"), the working markers in the list, and an
  event-driven poll: `GET /api/jobs/<id>?since=N` only fires when the stream's
  `next_seq` says there is something new.
- **Answers the agent's questions.** Permission prompts (Allow / Deny) and
  `AskUserQuestion` panels — single- and multi-select, with the free-text note
  some options take — are mirrored to the phone and drive the real host TUI.
- **Everything else the BB10 apps had:** the daemon-side prompt queue,
  stop, `/command` gating, `!shell` escapes fed back as context, attachment
  upload, host→phone drop downloads, subscription usage, and rewind.
- **Notifications.** A foreground service keeps the status streams alive while
  turns run, and alerts when one finishes or blocks on you — including turns
  you started from the desktop TUI or another phone.

## Build

Requires JDK 17+ (Android Studio's bundled JBR works) and an Android SDK with
platform 36.

```bash
./gradlew assembleDebug      # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease    # minified + shrunk, signed with keystore/agentremote.jks
```

`local.properties` needs `sdk.dir=…`; the release keystore in `keystore/` is a
self-signed sideload key, not a Play upload key.

## Setting it up

1. On each host, note the daemon base URL and the token file
   (`~/.agentremoted/token`, or `/root/.agentremoted/token` on a typical VPS).
2. In the app: **⋮ → Profiles → +**, fill in name / address / token, then
   **Test connection**. It pings *and* makes one authenticated call, so a
   wrong token fails here rather than showing an empty list later.
3. Save. The provider badge, model list and feature toggles come from that
   daemon's `/api/ping` — nothing is hard-coded per provider.

Addresses accept `host`, `host:port`, or a full `http(s)://` URL. Cleartext
HTTP is permitted (`network_security_config.xml`) because that is how
agentremoted is normally deployed on a LAN or behind Cloudflare.

## How it is put together

```
data/     wire DTOs, profile store (AndroidKeyStore-wrapped tokens), settings,
          the repository that fans out across profiles, JobWatcher
net/      DaemonClient (OkHttp, per-call-kind timeouts), StatusStream (SSE)
ui/       Compose screens + a small markdown parser/renderer
service/  foreground job watch + notifications
```

Two decisions worth knowing:

- **The daemon's pre-rendered `blocks` are ignored.** They carry BB10
  Cascades-flavoured HTML and that engine's palette. Android parses each
  message's raw `text` instead (`ui/markdown/`), so code blocks scroll instead
  of wrapping, tables stay tables, and links are tappable.
- **Tokens are encrypted at rest** with a non-exportable AndroidKeyStore AES
  key, and backup/transfer of the DataStore is excluded — a copied data
  directory decrypts to nothing.

## Tested against

`agentremoted` multi (Claude / Grok / Codex as available) — macOS launchd and
Linux systemd, simultaneously, on Android 13 and Android 16.
