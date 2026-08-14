# iOS app

Two pieces:

- **`AgentRemoteKit/`** — Swift package: daemon protocol types + `AgentRemoteClient` networking.
  Cross-platform, no SwiftUI. `swift build` works with Command Line Tools; full `swift test`
  needs Xcode (XCTest).
- **`AgentRemoteApp/`** — SwiftUI app (iPhone + iPad).
- **`project.yml`** — [XcodeGen](https://github.com/yonaskolb/XcodeGen) spec (generated
  `.xcodeproj` is gitignored).

## Generate and build

```bash
brew install xcodegen   # if needed
cd ios
xcodegen generate
open AgentRemote.xcodeproj
```

In Xcode: pick a signing team, then run on iPhone/iPad (or Simulator).  
`TARGETED_DEVICE_FAMILY` is `1,2` (iPhone + iPad).

Re-run `xcodegen generate` after `project.yml` changes.

## Architecture (aligned with Android / web)

Same agentremoted HTTP API as the other clients:

| Concept | iOS | Android |
|--------|-----|---------|
| Multi-host profiles | `ProfileStore` + Keychain tokens | `ProfileStore` + encrypted prefs |
| Unified session list | `AppModel.rows` (merged, activity-sorted) | `AgentRepository.sessions` |
| Open transcript | `ChatViewModel` + job poll | `TranscriptViewModel` |
| Caps / catalogues | `PingResponse` + `provider_details` | `Caps` / `ProviderDetailDto` |
| Live status | `/ws/status` per profile | SSE/WS per profile |

There is **no** persistent chat socket — only REST jobs + optional status stream.

### Navigation

- **iPad (regular width):** `NavigationSplitView` — **left** unified session list, **right** transcript.
- **iPhone (compact):** same split view collapses to a stack (list → detail).

Root entry is **Sessions**, not “pick a server first” (matches Android). Profiles / usage /
drop / settings live in the toolbar menu and sheets.

### Multi-harness

`/api/ping` multi fields (`multi`, `providers`, `provider_details`) drive:

- New-session harness picker (Claude / Grok / Codex)
- Per-session model list and effort picker (effort hidden for Claude)
- Provider accent colors on list rows and chat chrome

## What's implemented

- Multi-profile daemons; one merged session list with search + profile filter chips
- Focus mode (daemon ≥ 2.6): Focus chip, state pills, track/done, seen cursor
- Session rename + model-suggested titles (long-press a row)
- Resume session / new session (harness, cwd/projects, interactive toggle)
- Chat: markdown transcript, tools, stop, slash autocomplete (gated per harness)
- Attach to turns started elsewhere (desktop TUI, queued chain) via the status stream
- Live status banner (phase + command two-line, elapsed) from `/ws/status`
- Send while running: types into an interactive TUI, queues behind a headless job
- Queue sheet with per-prompt cancel
- `!command` host shell escape (echo + silent context turn, Android format)
- Composer attachments (upload → `[attached: …]` markers)
- Rewind to a message (confirmation spells out what it drops; conversation only)
- Permission Allow/Deny sheet (harness-named); AskUserQuestion sheet with markdown
  bodies — swipe-dismiss parks the gate behind an "Answer" banner, never denies
- Orphaned gates survive turn end (daemon 2.6.5 keeps them in the status feed)
- Model + effort pickers (session harness catalogues)
- Live TUI sheet (when `caps.live_tui`, or `interactive` implies one)
- Merged host drop inbox across daemons (folders download as `<name>.zip`, delete
  works on folders, dedup across profiles) + per-account merged usage
- Profile editor with test connection (ping + authenticated `/api/projects` call)
- Settings: theme, show-all-sessions, default execution/model/effort
- Provider-tinted UI (Claude / Grok / Codex)

Not ported from Android (platform-specific): background foreground-service watch +
push-style notifications, MediaStore Downloads (iOS uses the share sheet), sound/haptic cues.

## Protocol notes (daemon-side)

- Tool calls are not in durable history — resume is text-only.
- Model/effort/permission mode apply on the **next** message.
- No session-delete API.

## Testing

```bash
cd ios/AgentRemoteKit && swift build    # always
cd ios/AgentRemoteKit && swift test    # needs full Xcode
```

### UI smoke test (simulator, against a LIVE daemon)

`AgentRemoteUITests/SmokeUITests` drives the real app on a simulator: sessions list + Focus
toggle, drop inbox (folder rules), merged usage, transcript history, and one real headless
turn end-to-end. It needs a daemon at `http://127.0.0.1:8473` and a seeded profile:

```bash
UDID=<booted simulator udid>
PUUID=$(uuidgen); TOKEN=$(cat ~/.agentremoted/token)
JSON="[{\"id\":\"$PUUID\",\"name\":\"Mac\",\"serverURLString\":\"http://127.0.0.1:8473\"}]"
xcrun simctl spawn $UDID defaults write com.agentremote.app com.claudereremote.profiles \
    -data "$(printf '%s' "$JSON" | xxd -p | tr -d '\n')"
xcrun simctl spawn $UDID defaults write com.agentremote.app com.agentremote.simTokens \
    -dict "$PUUID.authToken" "$TOKEN"
mkdir -p /tmp/agent-remote-uitest   # scratch cwd for the live-turn test

xcodebuild -project AgentRemote.xcodeproj -scheme AgentRemote \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

`testLiveHeadlessTurn` runs one real (tiny) agent turn on the host — skip it with
`-skip-testing:AgentRemoteUITests/SmokeUITests/testLiveHeadlessTurn`.

Manual smoke: add server → sessions list fills → open Claude/Grok row → send message →
permission prompt if needed → New session with harness picker on multi daemon.
