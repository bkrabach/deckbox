"""Deckbox command-line interface.

deckbox                     # run the server (default), serving cwd
deckbox run --dir PATH      # run, serving PATH
deckbox doctor              # diagnostics
deckbox status              # config + service + port status
deckbox service install     # install & start a systemd --user service
deckbox service {uninstall,start,stop,restart,status,logs}
deckbox config {show,path,set}
"""

from __future__ import annotations

import argparse
import sys

from deckbox import __version__
from deckbox.config import DEFAULTS, ResolvedConfig, load_config_file, resolve, save_config_file

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dir", default=None, metavar="PATH",
        help="Directory to serve (alternative to the positional PATH)",
    )
    parser.add_argument("--host", default=None, help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default 8000)")
    parser.add_argument("--log-level", default=None, help="uvicorn log level (default info)")
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable PAM auth even for non-localhost clients (trusted networks only)",
    )


def _add_path_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        metavar="PATH",
        help="Directory to serve (positional; overrides --dir if both given)",
    )


def _chosen_dir(args: argparse.Namespace) -> str | None:
    """Positional PATH wins over --dir; either is a CLI-level override."""
    return getattr(args, "path", None) or args.dir


def _auth_required(host: str, no_auth: bool) -> bool:
    if no_auth:
        return False
    return host not in _LOOPBACK


def run(args: argparse.Namespace) -> int:
    import uvicorn

    from deckbox.auth import launch_user, pam_available
    from deckbox.server import create_app

    cfg = resolve(
        directory=_chosen_dir(args), host=args.host, port=args.port, log_level=args.log_level
    )

    if not cfg.directory.exists():
        print(f"error: served directory does not exist: {cfg.directory}", file=sys.stderr)
        return 1

    auth_required = _auth_required(cfg.host, args.no_auth)
    app = create_app(cfg, auth_required=auth_required)

    _print_banner(cfg, auth_required=auth_required, launcher=launch_user())
    if auth_required and not pam_available():
        print(
            f"{_YELLOW}warning:{_RESET} PAM is unavailable — remote clients cannot "
            f"authenticate. Install python-pam or run with --host 127.0.0.1.",
            file=sys.stderr,
        )

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)
    return 0


def _print_banner(cfg: ResolvedConfig, *, auth_required: bool, launcher: str) -> None:
    shown_host = "localhost" if cfg.host in ("127.0.0.1", "::1") else cfg.host
    auth = (
        f"PAM (user {_BOLD}{launcher}{_RESET}) for non-localhost"
        if auth_required
        else "none (localhost-only or --no-auth)"
    )
    print(f"\n{_BOLD}Deckbox{_RESET} {_DIM}{__version__}{_RESET}")
    print(f"  serving : {cfg.directory}")
    print(f"  address : {_GREEN}http://{shown_host}:{cfg.port}{_RESET}")
    print(f"  auth    : {auth}\n")


def doctor(args: argparse.Namespace) -> int:
    from deckbox.doctor import run_doctor

    cfg = resolve(directory=_chosen_dir(args), host=args.host, port=args.port)
    return run_doctor(cfg)


def status(args: argparse.Namespace) -> int:
    import socket

    from deckbox import service

    cfg = resolve(directory=_chosen_dir(args), host=args.host, port=args.port)
    print(f"\n{_BOLD}Deckbox status{_RESET}")
    print(f"  serving directory : {cfg.directory}")
    print(f"  listen address    : {cfg.host}:{cfg.port}")

    installed = service.is_installed()
    active = service.is_active() if installed else False
    state = (
        f"{_GREEN}active{_RESET}"
        if active
        else ("installed (inactive)" if installed else "not installed")
    )
    print(f"  systemd service   : {state}")

    probe_host = "127.0.0.1" if cfg.host in ("0.0.0.0", "::", "") else cfg.host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        listening = sock.connect_ex((probe_host, cfg.port)) == 0
    print(f"  port {cfg.port:<12}: {'listening' if listening else 'not listening'}\n")
    return 0


def service_cmd(args: argparse.Namespace) -> int:
    from deckbox import service

    action = args.action
    try:
        if action == "install":
            cfg = resolve(
                directory=args.dir, host=args.host, port=args.port, log_level=args.log_level
            )
            if not cfg.directory.exists():
                print(f"error: directory does not exist: {cfg.directory}", file=sys.stderr)
                return 1
            path = service.install(cfg)
            print(f"{_GREEN}✓{_RESET} installed and started: {path}")
            print(f"  serving {cfg.directory} on {cfg.host}:{cfg.port}")
            print("  manage with: deckbox service status|logs|stop|restart")
            return 0
        if action == "uninstall":
            existed = service.uninstall()
            print("✓ uninstalled" if existed else "nothing to uninstall")
            return 0
        if action == "start":
            service.start()
            print(f"{_GREEN}✓{_RESET} started")
            return 0
        if action == "stop":
            service.stop()
            print("✓ stopped")
            return 0
        if action == "restart":
            service.restart()
            print(f"{_GREEN}✓{_RESET} restarted")
            return 0
        if action == "status":
            print(service.status_text())
            return 0
        if action == "logs":
            print(service.logs(lines=args.lines))
            return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("error: unknown service action", file=sys.stderr)
    return 2


def config_cmd(args: argparse.Namespace) -> int:
    from deckbox.config import CONFIG_PATH

    if args.action == "path":
        print(CONFIG_PATH)
        return 0
    if args.action == "show":
        cfg = resolve()
        print(f"\n{_BOLD}Resolved configuration{_RESET}")
        print(f"  directory : {cfg.directory}")
        print(f"  host      : {cfg.host}")
        print(f"  port      : {cfg.port}")
        print(f"  log_level : {cfg.log_level}")
        file_cfg = load_config_file()
        print(f"\n{_DIM}Config file ({CONFIG_PATH}):{_RESET}")
        if file_cfg:
            for key, value in file_cfg.items():
                print(f"  {key}: {value}")
        else:
            print(f"  {_DIM}(none — using defaults + env){_RESET}")
        print()
        return 0
    if args.action == "set":
        key, value = args.key, args.value
        if key not in DEFAULTS:
            print(f"error: unknown key '{key}'. Known: {', '.join(DEFAULTS)}", file=sys.stderr)
            return 1
        parsed: object = int(value) if key == "port" else value
        current = load_config_file()
        current[key] = parsed
        path = save_config_file(current)
        print(f"{_GREEN}✓{_RESET} set {key} = {parsed}  ({path})")
        return 0
    print("error: unknown config action", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deckbox",
        description="A pleasant, modern web viewer for a folder of files.",
    )
    parser.add_argument("--version", action="version", version=f"deckbox {__version__}")
    _add_run_flags(parser)  # allow bare `deckbox` to run with flags

    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run the web server (default)")
    _add_path_arg(run_p)
    _add_run_flags(run_p)

    doctor_p = sub.add_parser("doctor", help="Run diagnostics")
    _add_path_arg(doctor_p)
    _add_run_flags(doctor_p)

    status_p = sub.add_parser("status", help="Show config, service, and port status")
    _add_path_arg(status_p)
    _add_run_flags(status_p)

    service_p = sub.add_parser("service", help="Manage the systemd --user service")
    service_p.add_argument(
        "action",
        choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"],
    )
    _add_run_flags(service_p)
    service_p.add_argument("--lines", type=int, default=50, help="Lines for `logs`")

    config_p = sub.add_parser("config", help="Show or edit configuration")
    config_sub = config_p.add_subparsers(dest="action", required=True)
    config_sub.add_parser("show", help="Show resolved config")
    config_sub.add_parser("path", help="Print config file path")
    set_p = config_sub.add_parser("set", help="Set a config key")
    set_p.add_argument("key", help=f"One of: {', '.join(DEFAULTS)}")
    set_p.add_argument("value")

    return parser


_SUBCOMMANDS = frozenset({"run", "doctor", "status", "service", "config"})


def _route_argv(argv: list[str]) -> list[str]:
    """Default to the `run` subcommand so a bare PATH or bare flags work.

    `deckbox ~/notes`     -> `deckbox run ~/notes`
    `deckbox --port 9000` -> `deckbox run --port 9000`
    `deckbox doctor ~/x`  -> unchanged (explicit subcommand)
    `deckbox -h/--version`-> unchanged (handled by the root parser)
    """
    if not argv:
        return ["run"]
    first = argv[0]
    if first in ("-h", "--help", "--version"):
        return argv
    first_positional = next((a for a in argv if not a.startswith("-")), None)
    if first_positional is None or first_positional not in _SUBCOMMANDS:
        return ["run", *argv]
    return argv


def main() -> None:
    parser = build_parser()
    args = parser.parse_args(_route_argv(sys.argv[1:]))

    command = args.command
    if command == "doctor":
        sys.exit(doctor(args))
    if command == "status":
        sys.exit(status(args))
    if command == "service":
        sys.exit(service_cmd(args))
    if command == "config":
        sys.exit(config_cmd(args))
    # default (None) or explicit "run"
    sys.exit(run(args))


if __name__ == "__main__":
    main()
