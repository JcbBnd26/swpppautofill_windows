"""Shared middleware for the Tools platform.

Each factory function returns a configured middleware callable that can
be registered on any FastAPI app via @app.middleware("http").
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response

log = logging.getLogger(__name__)

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_csrf_middleware(
    *,
    expected_origin: str,
    dev_mode: bool = False,
) -> Callable:
    """Return an async middleware function that rejects unsafe requests
    whose Origin header does not match *expected_origin*.

    In dev mode the check is skipped entirely.  When the Origin header
    is absent the request is allowed through (browsers always send it
    on same-origin unsafe fetches).
    """
    _expected = expected_origin.rstrip("/")

    async def _csrf_origin_check(request: Request, call_next: Callable) -> Response:
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and not dev_mode:
                if origin.rstrip("/") != _expected:
                    log.warning(
                        "CSRF origin mismatch: expected=%s got=%s path=%s",
                        _expected,
                        origin,
                        request.url.path,
                    )
                    return Response(
                        content='{"detail":"Origin mismatch"}',
                        status_code=403,
                        media_type="application/json",
                    )
        return await call_next(request)

    return _csrf_origin_check
