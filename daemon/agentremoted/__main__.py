"""Entry point: python3 -m agentremoted [--provider claude|grok|codex|deepseek]"""

import argparse
import logging
import sys

from . import __version__
from . import providers
from .config import (load_config, load_or_create_token, CONFIG_DIR,
                     write_tmux_helper)
from .jobs import JobManager
from .server import make_server


def main():
    parser = argparse.ArgumentParser(
        prog="agentremoted",
        description="Serve AI agent CLI sessions (Claude Code, Grok Build, "
                    "Codex, DeepSeek Harness) to Agent Remote clients.",
    )
    parser.add_argument("--provider",
                        choices=("claude", "grok", "codex", "deepseek"),
                        help="force single-provider mode (overrides config)")
    parser.add_argument("--port", type=int, help="override listen port")
    parser.add_argument("--bind", help="override bind address")
    parser.add_argument("--print-token", action="store_true",
                        help="print the auth token and exit")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version",
                        version="agentremoted " + __version__)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Under launchd the soft open-files limit is 256, and everything we
    # spawn (the tmux server, hence every claude TUI pane) inherits it.
    # Claude Code easily exceeds 256 fds (MCP sockets, watchers, subagents)
    # and then crashes with EMFILE — the "works in my terminal, crashes from
    # the phone" mystery. Raise it before spawning anything.
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = 65536 if hard == resource.RLIM_INFINITY else min(65536, hard)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except (ImportError, ValueError, OSError):
        pass

    token = load_or_create_token()
    if args.print_token:
        print(token)
        return 0

    config = load_config()
    if args.provider:
        # CLI force: single-provider root mounts only.
        config._data["provider"] = args.provider
        config._data["providers"] = []
    if args.port:
        config._data["port"] = args.port
    if args.bind:
        config._data["bind"] = args.bind

    bundles = providers.build_all(config, JobManager)
    server = make_server(config, token, bundles)

    log = logging.getLogger("agentremoted")
    scheme = "https" if (config.tls_cert and config.tls_key) else "http"
    names = list(bundles.keys())
    multi = len(names) > 1
    log.info("agentremoted %s listening on %s://%s:%s  providers=%s",
             __version__, scheme, config.bind, config.port, ",".join(names))
    if multi:
        for name in names:
            log.info("  /%s  →  %s", name, bundles[name].runner.name)
    else:
        runner = bundles[names[0]].runner
        if runner.name == "claude":
            log.info("projects dir: %s", config.projects_path)
        elif runner.name == "grok":
            log.info("grok home: %s", config.grok_home_path)
        elif runner.name == "codex":
            log.info("codex home: %s (scaffold)", config.codex_home_path)
    log.info("auth token in %s (or run: python3 -m agentremoted --print-token)",
             CONFIG_DIR / "token")
    try:
        drop = config.drop_path
        drop.mkdir(parents=True, exist_ok=True)
        log.info("host→phone drop folder: %s", drop)
    except OSError as e:
        log.warning("could not create drop folder %s: %s", config.drop_path, e)
    helper = write_tmux_helper()
    if helper:
        log.info("interactive TUIs: %s ls  (they are on a private tmux "
                 "socket, so plain `tmux ls` will not show them)", helper)

    # Force interactive managers to adopt surviving tmux TUIs *before*
    # rehydrating mid-turn jobs so resume can rebind by session id.
    # DeepSeek: adopt a live `dsh web` or start one so resume can talk to it.
    for name, bundle in bundles.items():
        force = getattr(bundle.runner, "_interactive_mgr", None)
        if callable(force):
            try:
                force()
            except Exception as e:  # noqa: BLE001
                log.warning("interactive adopt for %s failed: %s", name, e)
        ensure = getattr(bundle.runner, "ensure_host", None)
        if callable(ensure):
            try:
                ok = ensure()
                host = getattr(bundle.runner, "host", None)
                if ok:
                    src = getattr(host, "source", "") or "ready"
                    log.info("dsh web %s at %s", src,
                             getattr(getattr(host, "client", None), "base", ""))
                else:
                    log.warning("dsh web not ready for %s: %s", name,
                                getattr(host, "last_error", "") or "unknown")
            except Exception as e:  # noqa: BLE001
                log.warning("dsh web ensure for %s failed: %s", name, e)
        try:
            bundle.jobs.resume_jobs()
        except Exception as e:  # noqa: BLE001
            log.warning("job resume for %s failed: %s", name, e)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
