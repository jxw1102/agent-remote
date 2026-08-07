# Agent notes — Agent Remote

How to **launch the daemon** (Mac launchd / Linux systemd / foreground):
[README.md](README.md#quick-start--run-the-daemon) and
[daemon/README.md](daemon/README.md#launch-the-daemon).

## Client parity (required)

Whenever you change a **client-side** feature, behaviour, or API contract, consider
**every** Agent Remote client — not only the one you are editing.

| Platform | Location |
|----------|----------|
| Web | `web/` |
| Android | `android/` (Compose) |
| BlackBerry 10 | `blackberry/` (Cascades / QML + C++) |
| iOS | `ios/` (SwiftUI) |
| LILYGO T-LoRa Pager / T-Deck | `esp32/` (ESP32-S3 firmware) |

Examples of work that almost always spans clients:

- New session flow (harness picker, cwd rules, models / effort)
- Profiles, ping / capabilities, multi-provider fields
- Transcript rendering, permissions, AskUserQuestion
- Jobs / queue / stop / live status (SSE or WebSocket)
- Themes, badges, provider accents, notifications / chimes
- Settings labels, empty states, error copy that mention the daemon

### How to apply this

1. **Ship the same capability** on every client that can reasonably support it,
   or document why a platform is deferred.
2. **Keep the HTTP API the source of truth.** Prefer daemon capability flags
   (`/api/ping`) over hard-coding per client.
3. **Match behaviour**, not pixel-identical UI. Each platform has its own
   toolkit (browser, Compose, Cascades) — same flows and data, platform-native
   presentation.
4. **Do not leave one client stranded** after an API or UX change (e.g. multi
   mode requiring `provider` on new session) without updating the others or
   noting the gap.

Daemon-only changes still need a version bump; if the API surface clients rely
on changes, update or verify all clients against it.
