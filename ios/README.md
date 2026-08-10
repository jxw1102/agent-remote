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
- Resume session / new session (harness, cwd/projects, interactive toggle)
- Chat: markdown transcript, tools, stop, slash autocomplete
- Permission Allow/Deny sheet
- AskUserQuestion sheet
- Model + effort pickers (session harness catalogues)
- Live TUI sheet (when `caps.live_tui`)
- Host drop files + usage sheets
- Settings: theme, show-all-sessions, default execution/model/effort
- Provider-tinted UI (Claude / Grok / Codex)

## Protocol notes (daemon-side)

- Tool calls are not in durable history — resume is text-only.
- Model/effort/permission mode apply on the **next** message.
- No session-delete API.

## Testing

```bash
cd ios/AgentRemoteKit && swift build    # always
cd ios/AgentRemoteKit && swift test    # needs full Xcode
```

Manual smoke: add server → sessions list fills → open Claude/Grok row → send message →
permission prompt if needed → New session with harness picker on multi daemon.
