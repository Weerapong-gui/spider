"""Single shared token, checked in constant time.

There is one user, so there is one secret and no user table. The server sits
behind Tailscale, which handles network-level identity; this is the second
layer that keeps other machines on the same LAN out.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Request

from spider.core.errors import ErrorCode, SpiderError

SESSION_COOKIE = "spider_session"


def token_matches(expected: str, given: str | None) -> bool:
    if not given:
        return False
    # compare_digest, never ==, so the time taken does not reveal how much of
    # the token was guessed correctly.
    return secrets.compare_digest(expected, given)


def bearer_from_header(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def make_auth_dependency(token: str) -> Callable[[Request], None]:
    def require_auth(request: Request) -> None:
        given = bearer_from_header(request.headers.get("authorization"))
        if given is None:
            given = request.cookies.get(SESSION_COOKIE)
        if not token_matches(token, given):
            raise SpiderError(ErrorCode.unauthorized, "Missing or invalid token.")

    return require_auth
