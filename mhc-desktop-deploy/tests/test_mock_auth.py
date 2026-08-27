"""Unit tests for ``mhc_desktop_deploy.impls.auth.mock.MockAuthProvider``.

The reference impl for the kernel's ``AuthProviderProtocol``. Lives
in deploy/tests/ (not kernel/tests/) so the kernel can swap or drop
this class without breaking test collection on the kernel side.
"""

from __future__ import annotations

import pytest
from mhc_desktop_deploy.impls.auth.mock import MockAuthProvider


@pytest.mark.asyncio
async def test_mock_login_success_returns_token_and_user() -> None:
    p = MockAuthProvider()
    out = await p.login("alice", "wonderland")
    assert out is not None
    token, user = out
    assert user.username == "alice"
    assert user.display_name == "Alice Liddell"
    assert len(token) > 16


@pytest.mark.asyncio
async def test_mock_login_bad_password_returns_none() -> None:
    p = MockAuthProvider()
    assert await p.login("alice", "wrong") is None


@pytest.mark.asyncio
async def test_mock_login_unknown_user_returns_none() -> None:
    p = MockAuthProvider()
    assert await p.login("nobody", "x") is None


@pytest.mark.asyncio
async def test_mock_resolve_then_logout() -> None:
    p = MockAuthProvider()
    token, user = await p.login("bob", "builder")  # type: ignore[misc]
    assert (await p.resolve(token)) == user
    await p.logout(token)
    assert await p.resolve(token) is None