"""Entry point: python3 -m agentremoted [--provider claude|grok|codex]"""

import argparse
import logging
import sys

from . import __version__
from . import providers
from .config import load_config, load_or_create_token, CONFIG_DIR
from .jobs import JobManager
from .server import make_server


def main():
    parser = argparse.ArgumentParser(
        prog="agentremoted",
        description="Serve AI agent CLI sessions (Claude Code, Grok Build, "
                    "Codex) to Agent Remote clients.",
    )
    parser.add_argument("--provider", choices=("claude", "grok", "codex"),
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

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
