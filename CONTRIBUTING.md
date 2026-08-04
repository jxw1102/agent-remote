# Contributing to Agent Remote

## Layout

| Piece | Path |
|-------|------|
| Daemon | `daemon/agentremoted/` |
| Web client | `web/` |
| BlackBerry 10 app | `blackberry/` |
| Android app | `android/` |
| Android app | sibling [`../android-remote/`](../android-remote/) |

## Client parity

If you change a **client-facing** feature or HTTP contract, consider **every**
client (web, Android, BB10), or document why a platform is deferred. Details:
[AGENTS.md](AGENTS.md).

Prefer capability flags from `GET /api/ping` over hard-coding per client.

## Daemon changes

1. Bump `daemon/agentremoted/__init__.py` → `__version__` in the same change.
2. Run tests:

```bash
cd daemon
python3 tests/smoke_test.py
python3 tests/render_test.py
```

## Web

```bash
cd web
python3 build.py    # → dist/agent-remote.html
./serve.sh          # local static server
```

No npm; keep the client a single built file.

## BlackBerry bar

```bash
cd blackberry
./build-bar-docker.sh    # → dist/AgentRemote.bar
```

## Pull requests

- Prefer focused diffs; match existing code style.
- Do not commit secrets, tokens, real hostnames, or built `.bar` files
  (`dist/` artifacts are gitignored).
