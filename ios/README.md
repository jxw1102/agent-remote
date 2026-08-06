# iOS app

Two pieces:

- **`AgentRemoteKit/`** — a Swift package with the daemon protocol types and the
  `AgentRemoteClient` networking layer. Cross-platform, has its own test suite (`swift test`), no
  SwiftUI/UIKit dependency — verified to build and pass on Linux as part of developing this (no
  Xcode available in that environment), including a live smoke test against a real daemon
  instance before the permanent fixture-based tests replaced it.
- **`AgentRemoteApp/`** — the SwiftUI app source.
- **`project.yml`** — an [XcodeGen](https://github.com/yonaskolb/XcodeGen) spec that generates the
  actual `.xcodeproj` (gitignored — regenerate it, don't hand-edit a checked-in project file).

## Generating and building the Xcode project

```bash
brew install xcodegen   # if you don't have it
cd ios
xcodegen generate       # writes AgentRemote.xcodeproj from project.yml
open AgentRemote.xcodeproj
```

In Xcode: pick your Apple ID as the signing team (free account is enough for personal
sideloading — re-sign every 7 days, or use a paid developer account for the usual 1 year), then
build & run on your iPhone/iPad over USB (or archive for TestFlight/ad-hoc).

Re-run `xcodegen generate` any time `project.yml` changes, or after pulling changes that touched
it — the generated `.xcodeproj` is gitignored on purpose.

## Architecture: talking to an agentremoted daemon

This app speaks the REST + job-polling protocol of
[agentremoted](https://github.com/jxw1102/agent-remote) ("bb10d" in some deployments — same
daemon, just a renamed package) — **not** a persistent WebSocket carrying the whole conversation.
The whole networking story:

- **Auth**: every request carries the daemon's shared token as an `X-Auth-Token` header. No SSH,
  no per-connection handshake — `AddConnectionView` just needs a base URL and that token.
- **Sessions are driven by jobs, not a socket**: `POST /api/sessions/new` (or `.../continue`)
  returns a `job_id` immediately; the client polls `GET /api/jobs/<id>?since=<seq>` (a plain
  0-based event-index cursor, not a real long-poll) until the job finishes. `ChatViewModel.pollJob`
  is the whole client-side state machine — see its doc comments and
  `backend`-adjacent `PROTOCOL_SPEC.md`-derived fixtures in `AgentRemoteKitTests` for the exact
  event shapes (`init`/`text`/`tool`/`result`/`permission`/`permission_resolved`/…).
- **`/ws/status`** is a best-effort, secondary feed (which jobs are running right now, across every
  session) — `DaemonClient` keeps it open for activity indicators, but nothing functional depends
  on it; a dropped status stream never blocks a chat.
- There's **no persistent connection to lose**. Every other call is an independent HTTP request,
  so unlike the old SSH-tunnel design, `SessionHub` doesn't need to "reestablish" anything after
  the app backgrounds/foregrounds — it just resumes the status stream.

### Known protocol limitations (daemon-side, not this client cutting corners)

- **No tool-call history on resume.** The daemon only persists user/assistant *text*; tool
  calls/results are explicitly "transient job state," never written to the transcript. Resuming a
  session shows text-only history — there's no way to recover a past turn's tool activity once its
  job is pruned, from this client or any other.
- **No mid-session model/permission-mode change call.** `model`/`permissionMode` are just fields on
  the *next* `new`/`continue` request — picking one in the UI doesn't take effect until you send
  the next message.
- **No session-delete endpoint.** Sessions are just Claude Code's own transcript files; the
  daemon's API has no route to remove one, so this app doesn't offer to either.
- **Live TUI needs a recent daemon build.** `caps.live_tui` is absent (not just `false`) on older
  deployments — this app hides the Live TUI button whenever that cap is missing, and treats a
  `GET .../tui` 404 as "not supported here" rather than a hard error either way.

## What's implemented

- Add/list servers (`ConnectionListView`, `AddConnectionView`) — just a name, base URL, and daemon
  token (Keychain via `KeychainStore`); non-secret metadata is in `UserDefaults` (`ProfileStore`).
- Connect flow (`ConnectingView`): verifies the token with `GET /api/ping`, surfaces the daemon's
  real error message on failure (401 vs a network error vs an invalid URL).
- Project/session picker (`ProjectView`): every resumable session, grouped by working directory;
  start a new one in an existing or brand-new folder.
- Chat (`ChatView`): assistant text (markdown), tool-call markers, a permission-request sheet with
  Allow/Deny, a model picker (raw ids from `/api/ping`), and slash-command autocomplete (bare
  names — this daemon doesn't provide per-command descriptions).
- **Live TUI** (`LiveTuiView`): polls the pane (plain text; ANSI/color deferred — see the protocol
  spec's own recommendation) and sends keys/text.
- **File drop** (`DropView`): list/download/delete the host's drop folder, upload an attachment.
- **Usage** (`UsageView`): renders `/api/usage`'s ready-to-format buckets.

## What's not implemented yet (deliberately out of scope for v1)

- Only one server connection at a time (matches "personal use" scope).
- AskUserQuestion isn't rendered in the chat timeline yet — the `question`/`question_resolved`
  event shape wasn't empirically verified (no live trigger during protocol research); driving it
  correctly needs that shape pinned down first.
- ANSI/color rendering in Live TUI (plain-text pane only).
- No editing of a saved server's token after creation — delete and re-add for now.

## Testing without a full round trip

`AgentRemoteKit`'s test suite (`cd ios/AgentRemoteKit && swift test`) decodes/encodes fixtures
copied verbatim from real captured daemon responses (ping, sessions, job events including a
pending permission, status push, usage, error bodies) using the same `.convertFromSnakeCase`
decoder `AgentRemoteClient` actually uses. The whole package also builds and its networking layer
(`URLSession`, `URLSessionWebSocketTask`) compiles on Linux — verified while developing this, no
Xcode available in that environment. What's *not* verified outside Xcode is the SwiftUI layer
itself — build it and walk through: add server → connect → open a session → send a message →
approve a permission prompt, before trusting it further.
