# Billing and authentication

Agent Remote is a **control plane for CLIs you already run**. It does not sell
tokens and does not hold Anthropic/OpenAI/xAI API keys in its own config
(except when you put them in the environment the daemon inherits).

## Two different credentials

| Credential | Who issues it | Purpose |
|------------|---------------|---------|
| **Daemon token** (`~/.agentremoted/token`) | agentremoted on first start | Authenticates clients to the HTTP API |
| **Harness login / API key** | Claude, Codex, Grok, DeepSeek CLIs | Pays for model usage |

Clients only ever send the **daemon token**. LLM billing stays with the host
CLIs.

## Subscription vs API key (Claude example)

| Mode | How | Cost shape |
|------|-----|------------|
| **Subscription** | `claude` → login with claude.ai Pro/Max | Flat plan + rate limits; usually best for heavy coding |
| **API key** | `ANTHROPIC_API_KEY` in the environment | Pay per token; can exceed Max quickly |

If **both** exist, Claude Code prefers the **API key** and bills API rates.
Unset the key when you intend to use Max/Pro limits.

Codex is similar: ChatGPT login vs `OPENAI_API_KEY`. Grok uses whatever the
`grok` CLI is configured with on that host.

## Official Claude Remote Control vs Agent Remote

| | Claude Remote Control | Agent Remote |
|--|----------------------|--------------|
| Requires claude.ai subscription | Yes (API keys not supported for RC) | No — uses whatever the host CLI accepts |
| Multi-provider (Grok, Codex) | No | Yes |
| Multi-host profiles | No | Yes |
| Self-hosted clients | Claude apps / web | Web, Android, iOS, BB10, pager |

## Auth health on `/api/ping`

Daemon **≥ 2.5.3** includes an `auth` object (and per-harness
`provider_details.<name>.auth`) from local files only — no network:

```json
{
  "cli": "claude",
  "cli_on_path": true,
  "mode": "subscription",
  "status": "ok",
  "detail": "claude.ai subscription login looks valid"
}
```

| status | Meaning |
|--------|---------|
| `ok` | Looks usable |
| `warning` | Partial (e.g. key present, binary missing) |
| `expired` | Login present but expired / needs refresh |
| `missing` | No credentials found |
| `unknown` | Cannot classify (e.g. Grok without a decoded store) |

## Multi-host billing

Each machine has its own CLI logins:

- Mac: Claude Max subscription  
- VPS: Grok API or Codex  

Agent Remote merges sessions in the client; bills stay on each host’s vendors.

## Security

- Treat the daemon token like a password; rotate by replacing
  `~/.agentremoted/token` and restarting.
- Prefer HTTPS tunnels or private networks over raw public TCP.
- See [SECURITY.md](../SECURITY.md).
