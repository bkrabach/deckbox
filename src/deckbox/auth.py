"""Authentication for Deckbox.

Policy:
  * EVERY request must authenticate with HTTP Basic auth (except /health),
    and the username MUST be the OS user that launched the server, verified
    via PAM. There is NO localhost bypass.

Why no localhost bypass: a socket peer of 127.0.0.1 does NOT prove the caller
is genuinely local. Any userspace proxy — `ssh -L`, socat, a container port
forward — re-originates the connection so the peer address is 127.0.0.1 for a
truly *remote* caller too. muxplex shipped a security advisory
(GHSA-7c6r-fvrh-9qp4) over exactly this trust-the-loopback-peer mistake; we
don't repeat it. Use --no-auth to opt out for genuinely trusted local use.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import pwd

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_REALM = "Deckbox"
# Paths reachable without auth even from remote clients.
_PUBLIC_PATHS = frozenset({"/health"})


def launch_user() -> str:
    """Return the OS username that launched this process."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, AttributeError):
        return os.environ.get("USER", "") or os.environ.get("LOGNAME", "")


def pam_available() -> bool:
    try:
        import pam  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means PAM is unusable
        return False
    return True


def _pam_authenticate(username: str, password: str) -> bool:
    """Blocking PAM check. Runs in a threadpool from the middleware."""
    try:
        import pam
    except Exception:  # noqa: BLE001 - PAM unavailable => auth fails closed
        return False
    try:
        authenticator = pam.pam()
        return bool(authenticator.authenticate(username, password, service="login"))
    except Exception:  # noqa: BLE001 - any PAM error must fail closed, never crash
        return False


class PamAuthMiddleware(BaseHTTPMiddleware):
    """Challenge every request with PAM-backed Basic auth (no localhost bypass)."""

    async def dispatch(self, request: Request, call_next):
        if not getattr(request.app.state, "auth_required", False):
            return await call_next(request)

        # No localhost bypass — a 127.0.0.1 socket peer does not prove the
        # caller is local (ssh -L / socat / container forwards re-originate).
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            username, password = _decode_basic(header)
            expected = request.app.state.launch_user
            # Constant-time username comparison, then PAM verifies the password.
            if (
                username
                and hmac.compare_digest(username, expected)
                and await run_in_threadpool(_pam_authenticate, username, password)
            ):
                return await call_next(request)

        return _challenge()

    # (BaseHTTPMiddleware requires dispatch; helpers kept at module scope below.)


def _decode_basic(header: str) -> tuple[str, str]:
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return "", ""
    username, sep, password = decoded.partition(":")
    if not sep:
        return "", ""
    return username, password


def _challenge() -> Response:
    return JSONResponse(
        {"detail": "Authentication required"},
        status_code=401,
        headers={"WWW-Authenticate": f'Basic realm="{_REALM}", charset="UTF-8"'},
    )
