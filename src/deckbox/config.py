"""Configuration resolution for Deckbox."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

CONFIG_DIR = Path(os.environ.get("DECKBOX_CONFIG_DIR", str(Path.home() / ".config" / "deckbox")))
CONFIG_PATH = CONFIG_DIR / "config.yaml"

DEFAULTS: dict = {
    "dir": None,
    "host": "0.0.0.0",
    "port": 8000,
    "log_level": "info",
    "allow_outside_root": False,
    "tls_hostnames": [],
    "tls_ips": [],
}

_ENV_KEYS = {
    "dir": "DECKBOX_DIR",
    "host": "DECKBOX_HOST",
    "port": "DECKBOX_PORT",
    "log_level": "DECKBOX_LOG_LEVEL",
    "allow_outside_root": "DECKBOX_ALLOW_OUTSIDE_ROOT",
}


@dataclass
class ResolvedConfig:
    """Fully resolved runtime configuration."""

    directory: Path
    host: str
    port: int
    log_level: str
    allow_outside_root: bool = False
    tls_hostnames: tuple[str, ...] = ()
    tls_ips: tuple[str, ...] = ()

    @property
    def dir_display(self) -> str:
        return str(self.directory)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _string_list(value: object, *, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


def parse_string_list(value: str, *, key: str) -> list[str]:
    """Parse a config CLI value without accepting empty list entries."""
    values = [item.strip() for item in value.split(",")]
    if not all(values):
        raise ValueError(f"{key} must be a comma-separated list of nonempty strings")
    return values


def load_config_file() -> dict:
    """Load known keys from the YAML config file. Missing/corrupt => {}."""
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (yaml.YAMLError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    known = {key: raw[key] for key in DEFAULTS if key in raw}
    for key in ("tls_hostnames", "tls_ips"):
        if key in known:
            known[key] = _string_list(known[key], key=key)
    return known


def save_config_file(data: dict) -> Path:
    """Persist known keys to the YAML config file (0600, dir 0700)."""
    merged = copy.deepcopy(DEFAULTS)
    for key in DEFAULTS:
        if key in data and data[key] is not None:
            merged[key] = data[key]
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".yaml.tmp")
    try:
        temporary.write_text(
            yaml.safe_dump(merged, sort_keys=True, default_flow_style=False), encoding="utf-8"
        )
        temporary.chmod(0o600)
        os.replace(temporary, CONFIG_PATH)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return CONFIG_PATH


def _env(key: str) -> str | None:
    value = os.environ.get(_ENV_KEYS[key])
    return value if value not in (None, "") else None


def _pick(key: str, flag_value: object) -> object:
    """Resolve one setting by precedence: flag > env > file > default."""
    if flag_value is not None:
        return flag_value
    environment_value = _env(key)
    if environment_value is not None:
        return environment_value
    file_config = load_config_file()
    if file_config.get(key) is not None:
        return file_config[key]
    return DEFAULTS[key]


def resolve(
    *,
    directory: str | os.PathLike | None = None,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
    allow_outside_root: bool | None = None,
    tls_hostnames: tuple[str, ...] = (),
    tls_ips: tuple[str, ...] = (),
) -> ResolvedConfig:
    """Resolve all settings. Explicit (non-None) args are CLI-flag overrides."""
    raw_directory = cast(str | os.PathLike[str] | None, _pick("dir", directory))
    resolved_directory = Path(raw_directory).expanduser().resolve() if raw_directory else Path.cwd()
    raw_port = cast(str | int, _pick("port", port))
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError):
        resolved_port = DEFAULTS["port"]
    file_config = load_config_file()
    return ResolvedConfig(
        directory=resolved_directory,
        host=str(_pick("host", host)),
        port=resolved_port,
        log_level=str(_pick("log_level", log_level)),
        allow_outside_root=_coerce_bool(_pick("allow_outside_root", allow_outside_root)),
        tls_hostnames=tuple(tls_hostnames or file_config.get("tls_hostnames", ())),
        tls_ips=tuple(tls_ips or file_config.get("tls_ips", ())),
    )
