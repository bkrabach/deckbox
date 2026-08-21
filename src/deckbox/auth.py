"""Authentication for Deckbox.

Policy:
  * Requests from localhost (127.0.0.1 / ::1) are never challenged.
  * Any other client must authenticate with HTTP Basic auth, and the
    username MUST be the OS user that launched the server, verified via PAM.

The client IP is taken from the socket (``request.client.host``), which is
unforgeable — unlike the Host or X-Forwarded-For headers.
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

_LOCALHOST_ADDRS = {"127.0.0.1", "::1"}
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
    """Challenge non-localhost requests with PAM-backed Basic auth."""

    async def dispatch(self, request: Request, call_next):
        if not getattr(request.app.state, "auth_required", False):
            return await call_next(request)

        client_host = request.client.host if request.client else ""
        if client_host in _LOCALHOST_ADDRS:
            return await call_next(request)

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
