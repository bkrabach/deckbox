"""Diagnostic checks for Deckbox."""

from __future__ import annotations

import http.client
import json
import shutil
import socket
import ssl
import sys
from dataclasses import dataclass

from deckbox import __version__
from deckbox import config
from deckbox.config import ResolvedConfig, required_tls_identities
from deckbox.tls import TLSStatus, inspect_tls as inspect_tls_material, tls_paths

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_OK = f"{_GREEN}✓{_RESET}"
_WARN = f"{_YELLOW}‖{_RESET}"
_FAIL = f"{_RED}✗{_RESET}"


@dataclass(frozen=True)
class Diagnostic:
    """One read-only Deckbox diagnostic result."""

    label: str
    detail: str
    ok: bool


@dataclass(frozen=True)
class TLSProbe:
    """The result of a CA- and hostname-verified local HTTPS health check."""

    ok: bool
    detail: str


def _line(mark: str, label: str, detail: str = "") -> None:
    suffix = f"  {_DIM}{detail}{_RESET}" if detail else ""
    print(f"  {mark} {label}{suffix}")


def _probe_endpoint(cfg: ResolvedConfig) -> tuple[str, str]:
    """Return the local connection address and certificate identity to verify."""
    if cfg.host in ("0.0.0.0", ""):
        hostname = next(iter(cfg.tls_hostnames), "localhost")
        return "127.0.0.1", hostname
    if cfg.host == "::":
        hostname = next(iter(cfg.tls_hostnames), "localhost")
        return "::1", hostname
    return cfg.host, cfg.host


def _port_in_use(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else "::1" if host == "::" else host
    try:
        addresses = socket.getaddrinfo(probe_host, port, type=socket.SOCK_STREAM)
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


def inspect_tls(cfg: ResolvedConfig) -> list[Diagnostic]:
    """Inspect fixed Deckbox TLS material without creating or changing it."""
    hostnames, ip_addresses = required_tls_identities(cfg)
    status = inspect_tls_material(tls_paths(), hostnames=hostnames, ip_addresses=ip_addresses)
    diagnostics = [
        Diagnostic(
            "TLS material",
            status.detail,
            status.ready,
        ),
        Diagnostic("TLS directory", str(status.paths.directory), status.ready),
        Diagnostic("TLS CA SHA-256", status.ca_fingerprint or "unavailable", status.ready),
        Diagnostic(
            "TLS leaf expiry",
            status.leaf_not_after.isoformat() if status.leaf_not_after else "unavailable",
            status.ready,
        ),
        Diagnostic("TLS DNS SANs", ", ".join(status.dns_names) or "none", status.ready),
        Diagnostic("TLS IP SANs", ", ".join(status.ip_addresses) or "none", status.ready),
    ]
    return diagnostics


def _verification_detail(error: ssl.SSLCertVerificationError) -> str:
    reason = error.verify_message.lower()
    if "hostname" in reason or "ip address mismatch" in reason:
        return f"HTTPS hostname verification failed: {error.verify_message}"
    return f"HTTPS CA verification failed: {error.verify_message}"


def probe_https_health(
    cfg: ResolvedConfig, tls_status: TLSStatus, *, timeout: float = 0.5
) -> TLSProbe:
    """Verify the live listener with only Deckbox's CA and hostname checking."""
    if not tls_status.ready:
        return TLSProbe(False, f"HTTPS health skipped: TLS material invalid ({tls_status.detail})")

    connect_host, server_hostname = _probe_endpoint(cfg)
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(tls_status.paths.ca_cert))
        with socket.create_connection((connect_host, cfg.port), timeout=timeout) as connection:
            with context.wrap_socket(connection, server_hostname=server_hostname) as secure_socket:
                request_host = f"[{server_hostname}]" if ":" in server_hostname else server_hostname
                secure_socket.sendall(
                    (
                        f"GET /health HTTP/1.1\r\nHost: {request_host}:{cfg.port}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii")
                )
                response = http.client.HTTPResponse(secure_socket)
                response.begin()
                body = response.read()
    except ssl.SSLCertVerificationError as error:
        return TLSProbe(False, _verification_detail(error))
    except ssl.SSLError as error:
        return TLSProbe(False, f"HTTPS TLS negotiation failed: {error}")
    except OSError as error:
        return TLSProbe(False, f"HTTPS connection failed: {error}")
    except http.client.HTTPException as error:
        return TLSProbe(False, f"HTTPS HTTP response failed: {error}")

    if response.status != 200:
        return TLSProbe(False, f"HTTPS HTTP status failed: expected 200, got {response.status}")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return TLSProbe(False, "HTTPS health JSON failed: response is not valid JSON")
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "ok"
        or not isinstance(payload.get("version"), str)
    ):
        return TLSProbe(False, "HTTPS health JSON failed: response is not a Deckbox health payload")
    return TLSProbe(True, "HTTPS health verified with the Deckbox CA.")


def run_doctor(cfg: ResolvedConfig) -> int:
    """Print a diagnostic report. Returns process exit code (0 = healthy)."""
    problems = 0

    print(f"\n{_DIM}Deckbox {__version__} — doctor{_RESET}\n")

    _line(_OK, "Python", sys.version.split()[0])
    _line(_OK, "deckbox", __version__)

    directory = cfg.directory
    if not directory.exists():
        _line(_FAIL, "served directory", f"{directory} (does not exist)")
        problems += 1
    elif not directory.is_dir():
        _line(_FAIL, "served directory", f"{directory} (not a directory)")
        problems += 1
    else:
        _line(_OK, "served directory", str(directory))

    if config.CONFIG_PATH.exists():
        _line(_OK, "config file", str(config.CONFIG_PATH))
    else:
        _line(_DIM + "·" + _RESET, "config file", f"none ({config.CONFIG_PATH}) — using defaults")

    dot = shutil.which("dot")
    if dot:
        _line(_OK, "graphviz (dot)", dot)
    else:
        _line(_WARN, "graphviz (dot)", "not found — DOT files show source only")

    try:
        import pam  # noqa: F401

        _line(_OK, "PAM (python-pam)", "available — remote auth enabled")
    except Exception as exc:  # noqa: BLE001
        _line(_WARN, "PAM (python-pam)", f"unavailable ({exc}) — remote access will fail auth")

    if shutil.which("systemctl"):
        _line(_OK, "systemctl", "available — `deckbox service install` supported")
    else:
        _line(_WARN, "systemctl", "not found — service install unavailable")

    for diagnostic in inspect_tls(cfg):
        _line(_OK if diagnostic.ok else _FAIL, diagnostic.label, diagnostic.detail)
        if not diagnostic.ok:
            problems += 1

    _line(_OK, "listen address", f"{cfg.host}:{cfg.port}")
    if _port_in_use(cfg.host, cfg.port):
        _line(_WARN, "port", f"{cfg.port} is listening")
        hostnames, ip_addresses = required_tls_identities(cfg)
        material = inspect_tls_material(tls_paths(), hostnames=hostnames, ip_addresses=ip_addresses)
        probe = probe_https_health(cfg, material)
        _line(_OK if probe.ok else _FAIL, "HTTPS health handshake", probe.detail)
        if not probe.ok:
            problems += 1
    else:
        _line(_OK, "port", f"{cfg.port} free")
        _line(_DIM + "·" + _RESET, "HTTPS health handshake", "skipped — listener not running")

    print()
    if problems:
        print(f"{_RED}✗ {problems} problem(s) found.{_RESET}\n")
        return 1
    print(f"{_GREEN}✓ All essential checks passed.{_RESET}\n")
    return 0
