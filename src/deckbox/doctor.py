"""Diagnostic checks for Deckbox."""

from __future__ import annotations

import shutil
import socket
import sys

from deckbox import __version__
from deckbox.config import CONFIG_PATH, ResolvedConfig

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_OK = f"{_GREEN}✓{_RESET}"
_WARN = f"{_YELLOW}‖{_RESET}"
_FAIL = f"{_RED}✗{_RESET}"


def _line(mark: str, label: str, detail: str = "") -> None:
    suffix = f"  {_DIM}{detail}{_RESET}" if detail else ""
    print(f"  {mark} {label}{suffix}")


def _port_in_use(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((probe_host, port)) == 0


def run_doctor(cfg: ResolvedConfig) -> int:
    """Print a diagnostic report. Returns process exit code (0 = healthy)."""
    problems = 0

    print(f"\n{_DIM}Deckbox {__version__} — doctor{_RESET}\n")

    # --- Runtime -----------------------------------------------------------
    _line(_OK, "Python", sys.version.split()[0])
    _line(_OK, "deckbox", __version__)

    # --- Served directory (hard requirement) -------------------------------
    directory = cfg.directory
    if not directory.exists():
        _line(_FAIL, "served directory", f"{directory} (does not exist)")
        problems += 1
    elif not directory.is_dir():
        _line(_FAIL, "served directory", f"{directory} (not a directory)")
        problems += 1
    else:
        _line(_OK, "served directory", str(directory))

    # --- Config file -------------------------------------------------------
    if CONFIG_PATH.exists():
        _line(_OK, "config file", str(CONFIG_PATH))
    else:
        _line(_DIM + "·" + _RESET, "config file", f"none ({CONFIG_PATH}) — using defaults")

    # --- GraphViz (DOT rendering) ------------------------------------------
    dot = shutil.which("dot")
    if dot:
        _line(_OK, "graphviz (dot)", dot)
    else:
        _line(_WARN, "graphviz (dot)", "not found — DOT files show source only")

    # --- PAM (remote auth) -------------------------------------------------
    try:
        import pam  # noqa: F401

        _line(_OK, "PAM (python-pam)", "available — remote auth enabled")
    except Exception as exc:  # noqa: BLE001
        _line(_WARN, "PAM (python-pam)", f"unavailable ({exc}) — remote access will fail auth")

    # --- systemd (service management) --------------------------------------
    if shutil.which("systemctl"):
        _line(_OK, "systemctl", "available — `deckbox service install` supported")
    else:
        _line(_WARN, "systemctl", "not found — service install unavailable")

    # --- Network -----------------------------------------------------------
    _line(_OK, "listen address", f"{cfg.host}:{cfg.port}")
    if _port_in_use(cfg.host, cfg.port):
        _line(_WARN, "port", f"{cfg.port} already in use (server may be running)")
    else:
        _line(_OK, "port", f"{cfg.port} free")

    print()
    if problems:
        print(f"{_RED}✗ {problems} problem(s) found.{_RESET}\n")
        return 1
    print(f"{_GREEN}✓ All essential checks passed.{_RESET}\n")
    return 0
