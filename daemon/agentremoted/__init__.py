"""agentremoted — serve AI agent CLI sessions to Agent Remote clients.

Fronts one or more harnesses (Claude Code, Grok Build, Codex, DeepSeek,
Cursor Agent, GitHub Copilot, CodeBuddy Code) over a shared
token-authenticated HTTP API. Multi-provider mode mounts each harness under
``/{name}/…`` so clients keep one profile per harness against a single process.
"""

__version__ = "2.18.7"

