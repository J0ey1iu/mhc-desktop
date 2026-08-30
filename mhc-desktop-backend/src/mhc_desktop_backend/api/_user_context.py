"""Shared request-user lookup for API modules.

``request.state.user`` is populated by :func:`install_auth
<mhc_desktop_backend.auth.middleware.install_auth>` on every
non-exempt request. Both the chat and metrics modules read it the
same way; this is the one copy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from mhc_desktop_backend.protocols import AuthUser

# Deploy-injectable per-request LLM header factory: takes the resolved
# principal (``None`` when no auth is wired, e.g. debug mode) and
# returns headers for the outbound LLM call.
ExtraHeadersFactory = Callable[[AuthUser | None], Awaitable[dict[str, str]]]


def current_user_id(request: Request) -> str:
    """The requesting user's username, or ``""`` for anonymous."""
    user = getattr(getattr(request, "state", None), "user", None)
    return getattr(user, "username", "") if user else ""


def llm_extra_headers(request: Request) -> Callable[[], Awaitable[dict[str, str]]] | None:
    """A request-scoped header provider for the LLM build, or ``None``
    when the deploy didn't wire ``llm_extra_headers_provider``.

    Closes over the resolved ``request.state.user`` so the deploy's
    factory gets the calling principal (username / tenant / upstream
    IdP token) on every chat and auto-title call.
    """
    factory: ExtraHeadersFactory | None = getattr(
        getattr(getattr(request, "app", None), "state", None),
        "llm_extra_headers_provider",
        None,
    )
    if factory is None:
        return None
    user: AuthUser | None = getattr(getattr(request, "state", None), "user", None)

    async def _provider() -> dict[str, str]:
        return await factory(user)

    return _provider