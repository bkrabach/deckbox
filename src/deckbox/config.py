"""Configuration resolution for Deckbox.

Precedence (first non-None wins), applied per setting:

    1. CLI flag (passed explicitly)
    2. Environment variable (DECKBOX_*)
    3. Config file (~/.config/deckbox/config.yaml)
    4. Hardcoded default

For the served directory specifically, the final fallback is the current
working directory (so running without any configuration serves the cwd).
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("DECKBOX_CONFIG_DIR", str(Path.home() / ".config" / "deckbox")))
CONFIG_PATH = CONFIG_DIR / "config.yaml"

# Only these keys are recognised in the config file. Unknown keys are ignored
# so that a file written by a newer/older version never corrupts resolution.
DEFAULTS: dict = {
    "dir": None,  # None => fall back to current working directory
    "host": "0.0.0.0",
    "port": 8000,
    "log_level": "info",
}

_ENV_KEYS = {
    "dir": "DECKBOX_DIR",
    "host": "DECKBOX_HOST",
    "port": "DECKBOX_PORT",
    "log_level": "DECKBOX_LOG_LEVEL",
}


@dataclass
class ResolvedConfig:
    """Fully resolved runtime configuration."""

    directory: Path
    host: str
    port: int
    log_level: str

    @property
    def dir_display(self) -> str:
        return str(self.directory)


def load_config_file() -> dict:
    """Load known keys from the YAML config file. Missing/corrupt => {}."""
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (yaml.YAMLError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in DEFAULTS if k in raw}


def save_config_file(data: dict) -> Path:
    """Persist known keys to the YAML config file (0600, dir 0700)."""
    merged = copy.deepcopy(DEFAULTS)
    for key in DEFAULTS:
        if key in data and data[key] is not None:
            merged[key] = data[key]
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(merged, sort_keys=True, default_flow_style=False))
    tmp.chmod(0o600)
    os.replace(tmp, CONFIG_PATH)
    return CONFIG_PATH


def _env(key: str) -> str | None:
    env_name = _ENV_KEYS[key]
    val = os.environ.get(env_name)
    return val if val not in (None, "") else None


def _pick(key: str, flag_value):
    """Resolve one setting by precedence: flag > env > file > default."""
    if flag_value is not None:
        return flag_value
    env_val = _env(key)
    if env_val is not None:
        return env_val
    file_cfg = load_config_file()
    if file_cfg.get(key) is not None:
        return file_cfg[key]
    return DEFAULTS[key]


def resolve(
    *,
    directory: str | os.PathLike | None = None,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
) -> ResolvedConfig:
    """Resolve all settings. Explicit (non-None) args are CLI-flag overrides."""
    raw_dir = _pick("dir", directory)
    resolved_dir = Path(raw_dir).expanduser().resolve() if raw_dir else Path.cwd()

    raw_port = _pick("port", port)
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError):
        resolved_port = DEFAULTS["port"]

    return ResolvedConfig(
        directory=resolved_dir,
        host=str(_pick("host", host)),
        port=resolved_port,
        log_level=str(_pick("log_level", log_level)),
    )
