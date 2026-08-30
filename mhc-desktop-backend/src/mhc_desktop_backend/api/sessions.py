"""Session CRUD router."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from mhc_desktop_backend.api._user_context import llm_extra_headers
from mhc_desktop_backend.llm import build_provider
from mhc_desktop_backend.protocols import (
    ProviderStoreProtocol,
    SessionStoreProtocol,
)

logger = logging.getLogger("mhc_desktop_backend")

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def get_store(request: Request) -> SessionStoreProtocol:
    store: SessionStoreProtocol | None = getattr(
        request.app.state, "session_store", None
    )
    if store is None:
        raise HTTPException(status_code=503, detail="session store not initialized")
    return store


def get_provider_store(request: Request) -> ProviderStoreProtocol:
    store: ProviderStoreProtocol | None = getattr(
        request.app.state, "provider_store", None
    )
    if store is None:
        raise HTTPException(status_code=503, detail="provider store not initialized")
    return store


@router.get("")
async def list_sessions(
    store: SessionStoreProtocol = Depends(get_store),
) -> list[dict[str, Any]]:
    sessions = await store.list()
    return [s.summary() for s in sessions]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: dict[str, Any] | None = None,
    store: SessionStoreProtocol = Depends(get_store),
) -> dict[str, Any]:
    sess = await store.create(body or {})
    return sess.to_dict()


# Specific sub-routes MUST be declared before the catch-all
# ``/{sid}`` matcher below, otherwise FastAPI will route
# ``/sessions/delete-many`` to the dynamic handler.
@router.post("/delete-many", status_code=status.HTTP_200_OK)
async def delete_many_sessions(
    body: dict[str, Any], store: SessionStoreProtocol = Depends(get_store)
) -> dict[str, Any]:
    sids = body.get("ids") or []
    if not isinstance(sids, list) or not all(isinstance(s, str) for s in sids):
        raise HTTPException(status_code=400, detail="ids must be a list of strings")
    removed = await store.delete_many(sids)
    return {"removed": removed}


@router.post("/clear", status_code=status.HTTP_200_OK)
async def clear_sessions(
    store: SessionStoreProtocol = Depends(get_store),
) -> dict[str, Any]:
    removed = await store.clear_all()
    return {"removed": removed}


@router.post("/{sid}/auto-title", status_code=status.HTTP_200_OK)
async def auto_title(
    sid: str,
    body: dict[str, Any],
    request: Request,
    store: SessionStoreProtocol = Depends(get_store),
    provider_store: ProviderStoreProtocol = Depends(get_provider_store),
) -> dict[str, Any]:
    """Generate a Chinese title (≤10 chars) for *sid* from the user's
    first message, then persist it. The frontend fires this once per
    new session, after the first user message lands. We update the
    session title in place — never overwrite a user-renamed title
    (only the default "New chat" placeholder is fair game).

    The LLM call is non-streaming: we just iterate the provider's
    Stream to completion and read ``.response.content``. Failure
    falls back to truncating the user message (mirrors what the
    frontend used to do locally, so the user still sees a usable
    sidebar entry even when the model is offline).

    Returns ``{"title": ..., "source": "llm"|"fallback"}`` so the
    frontend can decide whether to surface a "rename?" affordance
    later.
    """
    user_message = (body.get("user_message") or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="user_message is required")

    existing = await store.get(sid)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"session '{sid}' not found")

    # Don't overwrite a title the user already set by hand. Only the
    # default placeholder is fair game.
    if existing.title and existing.title != "New chat":
        return {"title": existing.title, "source": "kept"}

    generated = await _generate_title(
        user_message=user_message,
        provider_name=(body.get("provider") or "").strip(),
        model=(body.get("model") or "").strip(),
        provider_store=provider_store,
        extra_headers_provider=llm_extra_headers(request),
    )

    title = generated or _fallback_title(user_message)
    src = "llm" if generated else "fallback"

    updated = await store.update(sid, {"title": title})
    logger.info("session.auto_title sid=%s title=%r source=%s", sid[:8], title, src)
    return {"title": updated.title, "source": src}


async def _generate_title(
    *,
    user_message: str,
    provider_name: str,
    model: str,
    provider_store: ProviderStoreProtocol,
    extra_headers_provider: Callable[[], Awaitable[dict[str, str]]] | None = None,
) -> str | None:
    """Run a tiny non-streaming LLM call to summarize *user_message*
    into a Chinese title ≤10 chars. Returns ``None`` on any failure
    so the caller can fall back to a hard truncate.
    """
    if not provider_name:
        return None
    try:
        provider = await provider_store.get(provider_name)
    except Exception:  # pragma: no cover — defensive
        logger.exception("auto_title.provider.lookup failed")
        return None
    if provider is None:
        return None
    try:
        llm = build_provider(
            provider,
            model_override=model,
            model_params=dict(getattr(provider, "model_params", None) or {}),
            extra_headers_provider=extra_headers_provider,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("auto_title.provider.build failed")
        return None

    system = (
        "你是一个会话标题生成器。任务：根据用户输入的内容，"
        "用中文生成一个不超过10个字的简洁标题，"
        "概括用户本次提问的核心意图。"
        "要求：直接输出标题文字本身，不要加任何标点符号、"
        "引号、序号、表情或前后缀说明；"
        "如果是英文或代码相关输入，仍然用中文概括。"
    )
    # Trim the user message we send so we never blow the budget on a
    # pathological 50 KiB paste — the gist is in the first ~500 chars.
    excerpt = user_message[:500]
    try:
        stream = await llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": excerpt},
            ],
            tools=[],
        )
        async for _ in stream:
            pass  # drain — we only care about the final response
        resp = stream.response
        raw = (resp.content or "").strip()
    except Exception:  # pragma: no cover — defensive
        logger.exception("auto_title.llm.call failed")
        return None

    return _clean_title(raw)


def _clean_title(raw: str) -> str | None:
    """Strip quotes / punctuation / newlines the model sometimes
    adds, then truncate to 10 visible characters. Returns ``None``
    if nothing meaningful is left."""
    if not raw:
        return None
    # Drop any thinking-style block the model might leak past the
    # stream boundary.
    cleaned = raw.split("</think>", 1)[-1]
    # Strip quotes (full-width and ASCII), trailing period / colon,
    # leading list markers, newlines, tabs.
    cleaned = re.sub(
        r"[\"'\u201c\u201d\u2018\u2019:：。.,、!!?！？\n\r\t]", "", cleaned
    )
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    # 10 *characters* (chars, not bytes — CJK each char is 1 visual
    # unit, ASCII each char is 1 too; emoji and combining marks are
    # # rare here so a plain slice is fine).
    if len(cleaned) > 10:
        cleaned = cleaned[:10]
    return cleaned


def _fallback_title(user_message: str) -> str:
    """Last-resort title: strip newlines and truncate to 10 chars.
    Used when the LLM call fails so the sidebar still shows
    something readable."""
    flat = re.sub(r"\s+", " ", user_message).strip()
    return flat[:10] or "New chat"


@router.get("/{sid}")
async def get_session(
    sid: str, store: SessionStoreProtocol = Depends(get_store)
) -> dict[str, Any]:
    sess = await store.get(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session '{sid}' not found")
    return sess.to_dict()


@router.put("/{sid}")
async def update_session(
    sid: str,
    body: dict[str, Any],
    store: SessionStoreProtocol = Depends(get_store),
) -> dict[str, Any]:
    try:
        sess = await store.update(sid, body)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return sess.to_dict()


@router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    sid: str, store: SessionStoreProtocol = Depends(get_store)
) -> None:
    await store.delete(sid)
