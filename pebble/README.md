# Agent Remote — Pebble Time 2

Native `emery` watchapp. The watch never talks to the daemon: PebbleKit JS in
the official phone app holds the URL and token, calls `GET /api/ping`, and
pushes a short HELLO over AppMessage.

Time 2 only (`targetPlatforms: ["emery"]`).

## Build

Needs the [Pebble SDK](https://developer.repebble.com/sdk/).

```sh
pebble sdk install latest
pebble build
pebble install --emulator emery
```

Sideload on a phone: `pebble install --phone <phone-ip>`.

## Clay (URL + token)

The watchapp is **configurable**. Open settings with the gear in the Pebble
iOS/Android app, or:

```sh
pebble emu-app-config
```

Initial setup (install daemon, printed Base URL + token) is in
[github.com/jxw1102/agent-remote](https://github.com/jxw1102/agent-remote)
(README / Get started). Paste those values into Clay. The Clay page repeats
that URL so phone-app settings can be filled without this tree.

| Field | What |
| --- | --- |
| Daemon URL | LAN / Tailscale IP of the **host** running `agentremoted`, e.g. `http://192.168.1.20:8473`. **Not** `127.0.0.1` (that is the phone). Leave empty until you have a host — the watch shows `setup`. |
| Token | From `~/.agentremoted/token` (printed by the installer). |
| Poll while working / idle | Seconds. Used by later PRs; stored on the phone only. |

URL, token, and poll seconds live in PKJS `localStorage` (`ar.base` / `ar.token` /
`ar.pollIdle` / `ar.pollWork`). They are **never** sent to the watch.

HTTPS works if the phone trusts the cert. Self-signed certs fail in PKJS — use
`http` on LAN or a public CA.

## Connection labels

Status bar, right side:

| Label | Meaning |
| --- | --- |
| `setup` | Clay URL or token empty |
| `phone` | Bluetooth down (watch-local) |
| `daemon` | Phone up, host unreachable |
| `token?` | HTTP 401 |
| `AR` | `GET /api/ping` succeeded |

`CONN=2` is sent only after ping succeeds.

## Tests (no SDK)

```sh
node test/reshape_test.js
```
