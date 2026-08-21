"""systemd --user service management for Deckbox (Linux)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from deckbox.config import ResolvedConfig, save_config_file

SERVICE_NAME = "deckbox.service"
USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_PATH = USER_UNIT_DIR / SERVICE_NAME


def systemctl_available() -> bool:
    return shutil.which("systemctl") is not None


def resolve_tool_bin() -> list[str]:
    """Command prefix to invoke deckbox. Prefer the installed script."""
    which = shutil.which("deckbox")
    if which:
        return [which]
    return [sys.executable, "-m", "deckbox"]


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def _unit_text(cfg: ResolvedConfig) -> str:
    exec_cmd = resolve_tool_bin() + [
        "run",
        "--dir",
        str(cfg.directory),
        "--host",
        cfg.host,
        "--port",
        str(cfg.port),
        "--log-level",
        cfg.log_level,
    ]
    exec_start = " ".join(_shlex_quote(part) for part in exec_cmd)
    path_env = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return f"""[Unit]
Description=Deckbox file viewer ({cfg.directory})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
WorkingDirectory={cfg.directory}
Environment=PATH={path_env}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def _shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def install(cfg: ResolvedConfig, *, enable_linger: bool = True) -> Path:
    """Write the unit, persist config, enable + start the service."""
    if not systemctl_available():
        raise RuntimeError("systemctl not found — systemd --user services need systemd.")
    # Persist chosen settings so `deckbox run`/`status` agree with the service.
    save_config_file(
        {
            "dir": str(cfg.directory),
            "host": cfg.host,
            "port": cfg.port,
            "log_level": cfg.log_level,
        }
    )
    USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(_unit_text(cfg))
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", SERVICE_NAME])
    if enable_linger:
        # Best-effort: lets the service run without an active login session.
        _run(["loginctl", "enable-linger", os.environ.get("USER", "")], check=False)
    return UNIT_PATH


def uninstall() -> bool:
    if not systemctl_available():
        raise RuntimeError("systemctl not found.")
    _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
    existed = UNIT_PATH.exists()
    UNIT_PATH.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    return existed


def start() -> None:
    _run(["systemctl", "--user", "start", SERVICE_NAME])


def stop() -> None:
    _run(["systemctl", "--user", "stop", SERVICE_NAME], check=False)


def restart() -> None:
    _run(["systemctl", "--user", "restart", SERVICE_NAME])


def is_installed() -> bool:
    return UNIT_PATH.exists()


def is_active() -> bool:
    if not systemctl_available():
        return False
    result = _run(["systemctl", "--user", "is-active", SERVICE_NAME], check=False)
    return result.stdout.strip() == "active"


def status_text() -> str:
    if not systemctl_available():
        return "systemctl not available"
    result = _run(["systemctl", "--user", "status", SERVICE_NAME, "--no-pager"], check=False)
    return (result.stdout or result.stderr).strip()


def logs(lines: int = 50) -> str:
    if not shutil.which("journalctl"):
        return "journalctl not available"
    result = _run(
        ["journalctl", "--user", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager"],
        check=False,
    )
    return (result.stdout or result.stderr).strip()
