"""Deckbox command-line interface."""

from __future__ import annotations

import argparse
import socket
import stat
import sys
from pathlib import Path

import uvicorn

from deckbox import __version__
from deckbox.auth import launch_user, pam_available
from deckbox.config import (
    DEFAULTS,
    ConfigValidationError,
    ResolvedConfig,
    load_config_file,
    parse_string_list,
    required_tls_identities,
    resolve,
    save_config_file,
)
from deckbox.server import create_app
from deckbox.tls import (
    TLSError,
    TLSIssue,
    TLSPaths,
    TLSStatus,
    inspect_tls,
    require_tls,
    setup_local_ca,
    tls_paths,
)

_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dir", default=None, metavar="PATH", help="Directory to serve")
    parser.add_argument("--host", default=None, help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default 8000)")
    parser.add_argument("--log-level", default=None, help="uvicorn log level (default info)")
    parser.add_argument(
        "--allow-outside-root",
        action="store_true",
        default=None,
        help="Let the 'Go to path' box browse outside the served directory.",
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
    return getattr(args, "path", None) or args.dir


def _runtime_config(args: argparse.Namespace) -> ResolvedConfig:
    return resolve(
        directory=_chosen_dir(args),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        log_level=getattr(args, "log_level", None),
        allow_outside_root=getattr(args, "allow_outside_root", None),
    )


def run(args: argparse.Namespace) -> int:
    cfg = _runtime_config(args)
    if not cfg.directory.exists():
        print(f"error: served directory does not exist: {cfg.directory}", file=sys.stderr)
        return 1

    try:
        hostnames, ip_addresses = required_tls_identities(cfg)
        tls_status = require_tls(tls_paths(), hostnames=hostnames, ip_addresses=ip_addresses)
    except TLSError:
        print(
            "error: TLS is not configured or invalid. Run 'deckbox setup-tls ...' first.",
            file=sys.stderr,
        )
        return 1

    app = create_app(cfg, auth_required=True)
    _print_banner(cfg)
    if not pam_available():
        print(
            f"{_YELLOW}warning:{_RESET} PAM is unavailable — no client can authenticate. "
            "Install python-pam.",
            file=sys.stderr,
        )
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        ssl_certfile=str(tls_status.paths.leaf_cert),
        ssl_keyfile=str(tls_status.paths.leaf_key),
    )
    return 0


def _print_banner(cfg: ResolvedConfig) -> None:
    print(f"\n{_BOLD}Deckbox{_RESET} {_DIM}{__version__}{_RESET}")
    print(f"  serving : {cfg.directory}")
    print(f"  address : {_GREEN}{_served_url(cfg)}{_RESET}")
    print(f"  auth    : PAM (user {_BOLD}{launch_user()}{_RESET}) — required for all clients")


def _tls_config(args: argparse.Namespace) -> tuple[ResolvedConfig, bool, bool]:
    stored = load_config_file()
    supplied_hostnames = getattr(args, "hostname", None)
    supplied_ips = getattr(args, "ip", None)
    hostnames = (
        tuple(supplied_hostnames)
        if supplied_hostnames is not None
        else tuple(stored.get("tls_hostnames", ()))
    )
    ips = tuple(supplied_ips) if supplied_ips is not None else tuple(stored.get("tls_ips", ()))
    return (
        resolve(tls_hostnames=hostnames, tls_ips=ips),
        supplied_hostnames is not None,
        supplied_ips is not None,
    )


def _print_tls_status(status: TLSStatus) -> None:
    print(f"  TLS readiness : {'ready' if status.ready else 'not ready'}")
    print(f"  TLS path  : {status.paths.directory}")
    print(f"  CA SHA-256: {status.ca_fingerprint or 'unavailable'}")
    print(f"  leaf expiry: {status.leaf_not_after or 'unavailable'}")
    print(f"  DNS SANs  : {', '.join(status.dns_names) or 'none'}")
    print(f"  IP SANs   : {', '.join(status.ip_addresses) or 'none'}")
    print(f"  detail    : {status.detail}")


def _snapshot_tls(
    paths: TLSPaths,
) -> tuple[bool, int | None, dict[Path, tuple[bytes, int]]]:
    """Capture fixed TLS state so an unsuccessful config save can be undone."""
    directory_existed = paths.directory.exists()
    directory_mode = stat.S_IMODE(paths.directory.stat().st_mode) if directory_existed else None
    files = {
        path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
        if path.is_file()
    }
    return directory_existed, directory_mode, files


def _restore_tls(
    paths: TLSPaths,
    snapshot: tuple[bool, int | None, dict[Path, tuple[bytes, int]]],
) -> None:
    """Restore a fixed TLS snapshot after config persistence fails."""
    directory_existed, directory_mode, files = snapshot
    for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key):
        if path in files:
            content, mode = files[path]
            paths.directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(mode)
        elif path.is_file():
            path.unlink()
    if directory_existed:
        if directory_mode is not None:
            paths.directory.chmod(directory_mode)
    elif paths.directory.exists():
        paths.directory.rmdir()


def setup_tls(args: argparse.Namespace) -> int:
    try:
        cfg, hostnames_supplied, ips_supplied = _tls_config(args)
        paths = tls_paths()
        hostnames, ip_addresses = required_tls_identities(cfg)
        inspected = inspect_tls(paths, hostnames=hostnames, ip_addresses=ip_addresses)
    except (TLSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.status:
        print(f"\n{_BOLD}Deckbox TLS status{_RESET}")
        _print_tls_status(inspected)
        if not inspected.ready:
            print("Run 'deckbox setup-tls ...' first.")
            return 1
        return 0

    stored = load_config_file()
    stored_hostnames = tuple(stored.get("tls_hostnames", ()))
    stored_ips = tuple(stored.get("tls_ips", ()))
    configuration_changed = (hostnames_supplied and cfg.tls_hostnames != stored_hostnames) or (
        ips_supplied and cfg.tls_ips != stored_ips
    )
    material_exists = any(
        path.exists() for path in (paths.ca_cert, paths.ca_key, paths.leaf_cert, paths.leaf_key)
    )
    if material_exists and configuration_changed and not args.renew:
        print(
            "error: requested TLS names differ from persisted configuration; rerun with --renew.",
            file=sys.stderr,
        )
        return 1

    if inspected.reason is TLSIssue.MISSING_SAN_COVERAGE and not args.renew:
        print(
            "error: requested TLS names differ from the current leaf; rerun with --renew.",
            file=sys.stderr,
        )
        return 1

    try:
        snapshot = _snapshot_tls(paths)
        issued = setup_local_ca(
            paths,
            hostnames=hostnames,
            ip_addresses=ip_addresses,
            renew=args.renew,
        )
    except (TLSError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if hostnames_supplied:
        stored["tls_hostnames"] = list(cfg.tls_hostnames)
    if ips_supplied:
        stored["tls_ips"] = list(cfg.tls_ips)
    try:
        save_config_file(stored)
    except OSError as exc:
        _restore_tls(paths, snapshot)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{_GREEN}✓{_RESET} Deckbox TLS material is ready.")
    print(f"  CA location : {issued.paths.ca_cert}")
    print(f"  CA SHA-256 : {issued.ca_fingerprint}")
    print(f"  setup page : {_served_url(cfg)}/setup")
    print("  Restart Deckbox to use new TLS material: deckbox service restart")
    return 0


def doctor(args: argparse.Namespace) -> int:
    from deckbox.doctor import run_doctor

    try:
        cfg = _runtime_config(args)
    except ConfigValidationError as exc:
        print(
            f"error: invalid TLS configuration: {exc}; run 'deckbox setup-tls ...'.",
            file=sys.stderr,
        )
        return 1
    return run_doctor(cfg)


def status(args: argparse.Namespace) -> int:
    from deckbox.doctor import probe_https_health
    from deckbox import service

    try:
        cfg = _runtime_config(args)
    except ConfigValidationError as exc:
        print(
            f"error: invalid TLS configuration: {exc}; run 'deckbox setup-tls ...'.",
            file=sys.stderr,
        )
        return 1
    print(f"\n{_BOLD}Deckbox status{_RESET}")
    print(f"  serving directory : {cfg.directory}")
    print(f"  listen address    : {_served_url(cfg)}")
    installed = service.is_installed()
    active = service.is_active() if installed else False
    state = (
        f"{_GREEN}active{_RESET}"
        if active
        else ("installed (inactive)" if installed else "not installed")
    )
    print(f"  systemd service   : {state}")
    hostnames, ip_addresses = required_tls_identities(cfg)
    tls_status = inspect_tls(tls_paths(), hostnames=hostnames, ip_addresses=ip_addresses)
    _print_tls_status(tls_status)
    listening = _is_listening(
        "127.0.0.1" if cfg.host in ("0.0.0.0", "") else "::1" if cfg.host == "::" else cfg.host,
        cfg.port,
    )
    print(f"  port {cfg.port:<12}: {'listening' if listening else 'not listening'}")
    if not tls_status.ready:
        print("  HTTPS health      : skipped — TLS material is not ready\n")
        return 1
    if not listening:
        print("  HTTPS health      : skipped — listener not running\n")
        return 0
    probe = probe_https_health(cfg, tls_status)
    print(f"  HTTPS health      : {probe.detail}\n")
    return 0 if probe.ok else 1


def _is_listening(host: str, port: int) -> bool:
    """Return whether a TCP listener is reachable without assuming an address family."""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for family, socktype, protocol, _, address in addresses:
        try:
            with socket.socket(family, socktype, protocol) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex(address) == 0:
                    return True
        except OSError:
            continue
    return False


def service_cmd(args: argparse.Namespace) -> int:
    from deckbox import service

    action = args.action
    try:
        if action == "install":
            cfg = _runtime_config(args)
            if not cfg.directory.exists():
                print(f"error: directory does not exist: {cfg.directory}", file=sys.stderr)
                return 1
            path = service.install(cfg)
            print(f"{_GREEN}✓{_RESET} installed and started: {path}")
            print(f"  serving {cfg.directory} at {_served_url(cfg)}")
            return 0
        if action == "uninstall":
            existed = service.uninstall()
            print("✓ uninstalled" if existed else "nothing to uninstall")
            return 0
        if action == "start":
            service.start(_runtime_config(args))
            print(f"{_GREEN}✓{_RESET} started")
            return 0
        if action == "stop":
            service.stop()
            print("✓ stopped")
            return 0
        if action == "restart":
            service.restart(_runtime_config(args))
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
        for key in ("directory", "host", "port", "log_level", "tls_hostnames", "tls_ips"):
            print(f"  {key} : {getattr(cfg, key)}")
        return 0
    if args.action == "set":
        key, value = args.key, args.value
        if key not in DEFAULTS:
            print(f"error: unknown key '{key}'. Known: {', '.join(DEFAULTS)}", file=sys.stderr)
            return 1
        try:
            parsed: object
            if key == "port":
                parsed = int(value)
            elif key in ("tls_hostnames", "tls_ips"):
                parsed = parse_string_list(value, key=key)
            else:
                parsed = value
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        current = load_config_file()
        current[key] = parsed
        path = save_config_file(current)
        print(f"{_GREEN}✓{_RESET} set {key} = {parsed}  ({path})")
        return 0
    if args.action == "unset":
        key = args.key
        if key not in DEFAULTS:
            print(f"error: unknown key '{key}'. Known: {', '.join(DEFAULTS)}", file=sys.stderr)
            return 1
        current = load_config_file()
        if key not in current:
            print(f"{_DIM}·{_RESET} {key} was not set (already using default)")
            return 0
        del current[key]
        path = save_config_file(current)
        print(f"{_GREEN}✓{_RESET} unset {key} — reverted to default  ({path})")
        return 0
    print("error: unknown config action", file=sys.stderr)
    return 2


def _served_url(cfg: ResolvedConfig) -> str:
    host = cfg.host
    if host in ("0.0.0.0", "::", ""):
        host = "localhost"
    elif ":" in host:
        host = f"[{host}]"
    return f"https://{host}:{cfg.port}"


def open_cmd(args: argparse.Namespace) -> int:
    import webbrowser

    cfg = _runtime_config(args)
    url = _served_url(cfg)
    print(f"{_GREEN}✓{_RESET} opening {url}")
    if not webbrowser.open(url):
        print(f"{_DIM}(could not launch a browser; open {url} manually){_RESET}")
    return 0


def update(args: argparse.Namespace) -> int:
    import shutil
    import subprocess

    source = "git+https://github.com/bkrabach/deckbox"
    uv = shutil.which("uv")
    if not uv:
        print("error: 'uv' is not on PATH.", file=sys.stderr)
        return 1
    cmd = (
        [uv, "tool", "install", "--force", "--reinstall", source]
        if args.reinstall
        else [uv, "tool", "upgrade", "deckbox"]
    )
    print(f"{_DIM}$ {' '.join(cmd)}{_RESET}")
    try:
        result = subprocess.run(cmd, check=False)
    except OSError as exc:
        print(f"error: could not run uv: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        return result.returncode
    print(f"{_GREEN}✓{_RESET} deckbox is up to date.")
    from deckbox import service

    if getattr(args, "no_restart", False) or not service.is_installed() or not service.is_active():
        return 0
    try:
        service.restart(resolve())
        print(f"{_GREEN}✓{_RESET} restarted the deckbox service (now on the latest code).")
    except RuntimeError as exc:
        print(f"{_YELLOW}warning:{_RESET} update applied but service restart failed: {exc}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deckbox", description="A modern web viewer for a folder of files."
    )
    parser.add_argument("--version", action="version", version=f"deckbox {__version__}")
    _add_run_flags(parser)
    sub = parser.add_subparsers(dest="command")
    run_p = sub.add_parser("run", help="Run the HTTPS web server (default)")
    _add_path_arg(run_p)
    _add_run_flags(run_p)
    setup_p = sub.add_parser("setup-tls", help="Create, renew, or inspect Deckbox TLS material")
    mode = setup_p.add_mutually_exclusive_group()
    mode.add_argument("--renew", action="store_true", help="Replace the leaf certificate only")
    mode.add_argument(
        "--status", action="store_true", help="Inspect TLS material without changing it"
    )
    setup_p.add_argument("--hostname", action="append", default=None, metavar="NAME")
    setup_p.add_argument("--ip", action="append", default=None, metavar="ADDRESS")
    for name, help_text in (
        ("doctor", "Run diagnostics"),
        ("status", "Show config, service, and port status"),
        ("open", "Open the served URL in a web browser"),
    ):
        command = sub.add_parser(name, help=help_text)
        _add_path_arg(command)
        _add_run_flags(command)
    service_p = sub.add_parser("service", help="Manage the systemd --user service")
    service_p.add_argument(
        "action", choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"]
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
    unset_p = config_sub.add_parser("unset", help="Revert a config key to its default")
    unset_p.add_argument("key", help=f"One of: {', '.join(DEFAULTS)}")
    update_p = sub.add_parser("update", help="Update deckbox to the latest version (via uv)")
    update_p.add_argument("--reinstall", action="store_true")
    update_p.add_argument("--no-restart", action="store_true")
    return parser


_SUBCOMMANDS = frozenset(
    {"run", "setup-tls", "doctor", "status", "service", "config", "open", "update"}
)


def _route_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["run"]
    if argv[0] in ("-h", "--help", "--version"):
        return argv
    first_positional = next((arg for arg in argv if not arg.startswith("-")), None)
    if first_positional is None or first_positional not in _SUBCOMMANDS:
        return ["run", *argv]
    return argv


def main() -> None:
    args = build_parser().parse_args(_route_argv(sys.argv[1:]))
    if args.command == "setup-tls":
        sys.exit(setup_tls(args))
    if args.command == "doctor":
        sys.exit(doctor(args))
    if args.command == "status":
        sys.exit(status(args))
    if args.command == "service":
        sys.exit(service_cmd(args))
    if args.command == "config":
        sys.exit(config_cmd(args))
    if args.command == "open":
        sys.exit(open_cmd(args))
    if args.command == "update":
        sys.exit(update(args))
    sys.exit(run(args))


if __name__ == "__main__":
    main()
