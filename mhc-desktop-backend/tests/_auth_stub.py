"""Kernel-side :class:`AuthProviderProtocol` stub for tests.

Why this exists
---------------

Kernel tests used to import ``MockAuthProvider`` from
``mhc_desktop_deploy.impls.auth.mock``. That crossed the
kernel/deploy boundary in the test layer: removing or replacing
the deploy's reference auth provider would break collection of
``mhc-desktop-backend/tests/test_auth.py`` and friends, even
though the kernel never depended on that specific class.

This module provides a structurally-typed replacement that
satisfies :class:`mhc_desktop_backend.protocols.AuthProviderProtocol`
without touching the deploy package. Seeded accounts mirror
``MockAuthProvider`` so HTTP test payloads stay identical:

* ``alice`` / ``wonderland`` — Alice Liddell (``u-alice``)
* ``bob``   / ``builder``    — Bob the Builder (``u-bob``)
* ``demo``  / ``demo``       — Demo User (``u-demo``)

If a future kernel test needs a different account set, declare
the test's own seeded accounts — don't extend this stub.
"""

from __future__ import annotations

import secrets
from typing import NamedTuple

from mhc_desktop_backend.protocols import AuthUser


class _SeededAccount(NamedTuple):
    password: str
    user: AuthUser


_SEED: dict[str, _SeededAccount] = {
    "alice": _SeededAccount(
        "wonderland",
        AuthUser(
            id="u-alice", username="alice",
            display_name="Alice Liddell", avatar_url=None,
        ),
    ),
    "bob": _SeededAccount(
        "builder",
        AuthUser(
            id="u-bob", username="bob",
            display_name="Bob the Builder", avatar_url=None,
        ),
    ),
    "demo": _SeededAccount(
        "demo",
        AuthUser(
            id="u-demo", username="demo",
            display_name="Demo User", avatar_url=None,
        ),
    ),
}


class StubAuthProvider:
    """In-memory ``AuthProviderProtocol`` implementation.

    Process-local token registry, same shape as the deploy's
    reference impl but with no deploy-package import. Tokens are
    24-byte URL-safe random strings; ``logout`` removes them.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, AuthUser] = {}

    async def login(
        self, username: str, password: str
    ) -> tuple[str, AuthUser] | None:
        acct = _SEED.get(username.strip().lower())
        if acct is None or acct.password != password:
            return None
        token = secrets.token_urlsafe(24)
        self._tokens[token] = acct.user
        return token, acct.user

    async def resolve(self, token: str) -> AuthUser | None:
        if not token:
            return None
        return self._tokens.get(token)

    async def logout(self, token: str) -> None:
        self._tokens.pop(token, None)


__all__ = ["StubAuthProvider"]