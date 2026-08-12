"""Provider adapters: everything agent-CLI-specific lives here.

A provider supplies two objects with duck-typed interfaces:

Store — read-only view over the agent's on-disk session transcripts:
    list_projects() -> [{id, cwd, name, session_count, last_active}]
    list_sessions(project_id=None, limit=25, user_only=True) -> [summary]
    search_sessions(query, project_id=None, limit=25, user_only=True)
        -> [summary + {snippet}]  full-text over title + message text
    iter_search_sessions(...)  (optional) yield hits as found for stream=1

`user_only` (the default, and what the app asks for) lists only sessions the
human started: no subagent transcripts the agent spawned for itself, and no
shells that opened and quit without a turn. Lookups by id (get_session /
get_messages) never filter, so a link to any session still resolves.
    get_session(session_id) -> summary | None
    get_messages(session_id, offset=None, limit=50)
        -> {session_id, total, offset, messages: [{uuid, role, ts, text,
            blocks}]} | None

Runner — how one turn is executed as a subprocess (used by jobs.JobManager):
    name                                  "claude" | "grok" | "codex"
    capabilities() -> dict                feature flags for /api/ping
    auth_health() -> dict                 optional CLI/login snapshot for /api/ping
    slash_commands() -> [str]             commands offered to the app
    title_for(text) -> str                (optional) name a session with THIS
                                          harness's own model; "" on failure
    prepare(job, mode) -> (cmd, env)      build the CLI invocation; may set
                                          job.cwd default; raises RunnerError
    handle_stream_line(job, line)         parse one stdout line into events
    tick(job)                             called ~4x/s while running (cheap)
    finalize(job, returncode, stderr_tail) -> bool | None
                                          post-exit fixups; True forces
                                          success, False failure, None means
                                          "returncode == 0 decides"
    cleanup(job)                          delete temp files

The HTTP layer and job manager never branch on the provider name — anything
provider-specific must stay behind these two objects.
"""

from collections import OrderedDict


class RunnerError(Exception):
    """A job could not be started (bad cwd, missing binary, ...)."""


class ProviderBundle:
    """One harness: store + runner + its own job manager."""

    __slots__ = ("name", "store", "runner", "jobs")

    def __init__(self, name, store, runner, jobs):
        self.name = name
        self.store = store
        self.runner = runner
        self.jobs = jobs


def build_one(config, name: str):
    """Return (store, runner) for a single named provider.

    The store is handed the runner's `title_for` so a session is named by the
    harness that owns it (see ../titles.py). Post-wired rather than passed in,
    because the store is built first and only the runner knows how to talk to
    its CLI.
    """
    name = str(name or "claude").lower()
    store = runner = None
    if name == "claude":
        from .claude import ClaudeRunner, ClaudeStore
        store, runner = ClaudeStore(config.projects_path, config), ClaudeRunner(config)
    elif name == "grok":
        from .grok import GrokRunner, GrokStore
        store, runner = GrokStore(config.grok_home_path, config), GrokRunner(config)
    elif name == "codex":
        from .codex import CodexRunner, CodexStore
        store, runner = CodexStore(config.codex_home_path, config), CodexRunner(config)
    if store is not None:
        titler = getattr(runner, "title_for", None)
        if callable(titler):
            store.titler = titler
        return store, runner
    raise ValueError(
        "unknown provider %r (expected 'claude', 'grok', or 'codex')" % name)


def build(config):
    """Back-compat: (store, runner) for config.provider / first of providers."""
    names = config.provider_names() if hasattr(config, "provider_names") else [
        str(getattr(config, "provider", "claude") or "claude").lower()]
    return build_one(config, names[0])


def build_all(config, job_manager_cls):
    """OrderedDict name → ProviderBundle for every configured harness."""
    bundles = OrderedDict()
    for name in config.provider_names():
        store, runner = build_one(config, name)
        jobs = job_manager_cls(config, runner)
        bundles[name] = ProviderBundle(name, store, runner, jobs)
    if not bundles:
        raise ValueError("no providers configured")
    return bundles
