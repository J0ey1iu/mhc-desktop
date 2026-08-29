"""Skill market proxy + personal-space sync engine.

The market service (``mhc-market-backend``) is a separate process with
no accounts: identity is forwarded from the kernel's authenticated
request via HMAC-signed headers (see the market service's ``auth.py``).
Everything the frontend needs goes through this router — the SPA never
talks to the market service directly (no CORS, one trust boundary).

Sync model (no versions, latest wins):

* every skill has a deterministic content sha (see
  ``SkillStoreProtocol.content_sha``)
* the kernel remembers ``base_sha`` (the cloud sha at the last
  successful sync) per skill in the skill store's state
* comparing ``local_sha`` / ``remote_sha`` / ``base_sha`` yields
  push / pull / conflict per skill; pushes use CAS (``base_sha``) so
  two devices syncing concurrently can't silently clobber each other
"""

from __future__ import annotations

import base64
import hmac
import io
import logging
import time
import zipfile
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from mhc_desktop_backend.api.usage import inc_download
from mhc_desktop_backend.skills import SkillError

logger = logging.getLogger("mhc_desktop_backend")


def _validate_market_zip(zip_bytes: bytes) -> None:
    """Fast-fail a broken market bundle (bad zip / no SKILL.md) with a
    clear error before touching the store; import_zip validates too, but
    a dedup hit skips the import so we validate up front."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            if not any(n.endswith("SKILL.md") for n in zf.namelist()):
                raise HTTPException(400, "zip does not contain SKILL.md")
    except zipfile.BadZipFile:
        raise HTTPException(400, "not a valid zip") from None



router = APIRouter(prefix="/api/v1/market", tags=["market"])

MARKET_TIMEOUT = httpx.Timeout(15.0)


def _sign(secret: str, user: str, ts: int) -> str:
    """Same HMAC scheme as the market service's auth.py — duplicated
    here (6 lines) so the kernel package doesn't depend on the market
    package."""
    import hashlib

    msg = f"{user}:{ts}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _cfg(request: Request) -> tuple[httpx.AsyncClient, str, str]:
    """(client, base_url, secret) or 503 when the market isn't wired."""
    base = getattr(request.app.state, "market_base_url", None)
    secret = getattr(request.app.state, "market_secret", None)
    if not base or not secret:
        raise HTTPException(status_code=503, detail="skill market not configured")
    client = httpx.AsyncClient(
        base_url=base,
        timeout=MARKET_TIMEOUT,
        transport=getattr(request.app.state, "market_transport", None),
        # trust_env=False — market is a local sidecar; don't route it
        # through the user's shell / macOS system proxy (see
        # llm/factory.py for the same guard on LLM clients).
        trust_env=False,
    )
    return client, base, secret


def _user(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        # Debug mode without auth wiring — market needs an identity.
        raise HTTPException(status_code=503, detail="authentication required")
    return user.username


def _headers(secret: str, user: str) -> dict[str, str]:
    ts = int(time.time())
    return {"X-MHC-User": user, "X-MHC-TS": str(ts), "X-MHC-Sig": _sign(secret, user, ts)}


async def _market_request(
    request: Request,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    client, _base, secret = _cfg(request)
    try:
        async with client as c:
            return await c.request(
                method,
                path,
                json=json_body,
                headers=_headers(secret, _user(request)),
            )
    except httpx.HTTPError as e:
        logger.warning("market.request failed path=%s: %s", path, e)
        raise HTTPException(status_code=502, detail=f"market unreachable: {e}") from None


def _map_market_status(r: httpx.Response) -> HTTPException:
    try:
        detail = r.json().get("detail", r.text)
    except Exception:
        detail = r.text
    return HTTPException(status_code=r.status_code, detail=detail)


# ── public registry passthrough ─────────────────────────────────────


@router.get("/skills")
async def list_market_skills(
    request: Request,
    q: str = "",
    category: str = "",
    sort: str = "downloads",
    featured: bool = False,
) -> Any:
    r = await _market_request(
        request,
        "GET",
        f"/api/v1/skills?q={quote(q)}&category={quote(category)}&sort={quote(sort)}&featured={str(featured).lower()}",
        json_body=None,
    )
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


@router.get("/skills/{slug}/files")
async def market_skill_files(slug: str, request: Request) -> Any:
    r = await _market_request(request, "GET", f"/api/v1/skills/{slug}/files")
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


@router.get("/skills/{slug}")
async def market_skill_detail(slug: str, request: Request) -> Any:
    r = await _market_request(request, "GET", f"/api/v1/skills/{slug}")
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


@router.get("/stories")
async def list_market_stories(request: Request) -> Any:
    r = await _market_request(request, "GET", "/api/v1/stories")
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


@router.get("/stories/{story_id}")
async def market_story_detail(story_id: str, request: Request) -> Any:
    r = await _market_request(request, "GET", f"/api/v1/stories/{story_id}")
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


@router.post("/stories")
async def create_market_story(
    request: Request, body: dict[str, Any] = Body(...)
) -> Any:
    r = await _market_request(
        request, "POST", "/api/v1/stories", json_body=body
    )
    if r.status_code not in (200, 201):
        raise _map_market_status(r)
    return r.json()


@router.post("/me/skills/{slug}/edit")
async def edit_cloud_copy(slug: str, request: Request, body: dict[str, Any] = Body(...)) -> Any:
    r = await _market_request(
        request, "POST", f"/api/v1/me/skills/{slug}/edit", json_body=body
    )
    if r.status_code not in (200, 201):
        raise _map_market_status(r)
    return r.json()


@router.get("/me/skills/{slug}/md")
async def get_cloud_copy_md(slug: str, request: Request) -> Any:
    r = await _market_request(request, "GET", f"/api/v1/me/skills/{slug}/md")
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


@router.delete("/me/skills/{slug}")
async def delete_cloud_copy(slug: str, request: Request) -> None:
    """Remove this user's cloud copy of a skill (mirror hygiene for
    local removals). Best-effort: 404 (no copy) counts as success."""
    r = await _market_request(request, "DELETE", f"/api/v1/me/skills/{slug}")
    if r.status_code not in (200, 204, 404):
        raise _map_market_status(r)


@router.get("/categories")
async def market_categories(request: Request) -> Any:
    r = await _market_request(request, "GET", "/api/v1/categories")
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


# ── add / publish ───────────────────────────────────────────────────


@router.post("/skills/{slug}/add")
async def add_from_market(
    slug: str,
    request: Request,
) -> dict[str, Any]:
    """Pull the latest market skill into the local store and back it
    up into the user's cloud space.

    Deliberately stateless: no "added / subscribed" binding is kept.
    The local slug is the market key (``skill_name-random6``) — unique
    per name+author, so same-named skills from different authors
    coexist. Re-adding the same entry updates that folder in place;
    if the exact content already exists locally it is skipped (dedup).
    """
    store = _skill_store(request)

    # Public download needs no identity headers, but signing anyway is
    # harmless and keeps one request path.
    r = await _market_request(request, "GET", f"/api/v1/skills/{slug}/download")
    if r.status_code != 200:
        raise _map_market_status(r)
    zip_bytes = r.content
    public_sha = r.headers.get("x-content-sha", "")

    # Validate the bundle (fast fail on a broken zip) and pick the
    # local slug = the market key passed in the URL path.
    _validate_market_zip(zip_bytes)
    local_slug = slug

    try:
        # 去重：本地任意技能已含此内容 → 跳过导入（不复制同内容第二份）。
        # 否则按市场 key 安装（同条目重复添加 = 原地覆盖更新）。
        dupe = None
        for s in await store.list():
            try:
                if await store.content_sha(s.slug) == public_sha:
                    dupe = s.slug
                    break
            except SkillError:
                continue
        if dupe is None:
            await store.import_zip(zip_bytes, origin="market", overwrite=True, slug=local_slug)
        else:
            # 同内容已存在于别的 slug 下（例如旧版添加的干净名字）→
            # 指向实际副本，返回/备份都针对它，避免返回 null。
            local_slug = dupe
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Backup into the personal space under the LOCAL slug (sync is keyed
    # by local skill identity, not the market key).
    # ponytail: base_sha=None overwrites any existing cloud copy; if a
    # second device edited it, the edit is lost. Acceptable: re-add from
    # market is an explicit "I want the published content" action.
    put = await _market_request(
        request,
        "PUT",
        f"/api/v1/me/skills/{local_slug}",
        json_body={
            "data": base64.b64encode(zip_bytes).decode(),
            "sha": public_sha,
            "base_sha": None,
        },
    )
    # Local download metric — a market skill was pulled into the store.
    await inc_download(request, slug)
    return {
        "skill": await store.get(local_slug),
        "cloud_backup": put.status_code in (200, 201),
    }


@router.post("/skills/{slug}/publish")
async def publish_to_market(
    slug: str,
    request: Request,
    body: dict[str, Any] = Body(default={}),
) -> Any:
    """Push a local skill to the public registry (overwrite = latest wins)."""
    store = _skill_store(request)
    skill = await store.get(slug)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill '{slug}' not found")
    try:
        zip_bytes = await store.export(slug)
        sha = await store.content_sha(slug)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    # Optional display icon: ``icon: <emoji>`` in SKILL.md frontmatter
    # (preserved verbatim in fm.extra by the parser).
    icon = ""
    try:
        _ctype, skill_md = await store.get_file(slug, "SKILL.md")
        from mhc_desktop_backend.skills.frontmatter import parse_skill_md

        fm, _ = parse_skill_md(skill_md.decode("utf-8", "replace"))
        icon = str(fm.extra.get("icon") or "")
    except Exception:  # noqa: BLE001 — icon is cosmetic, never block publish
        icon = ""
    payload = {
        "data": base64.b64encode(zip_bytes).decode(),
        "sha": sha,
        "display_name": skill.name,
        "description": skill.description,
        "category": str(body.get("category") or "other"),
        "icon": icon,
    }
    # 透传调用方扩展字段（如 source_type / source_ref）：内核只负责
    # 固定字段，其余 body 键原样转发给 market 持久化。
    payload.update({k: v for k, v in body.items() if k not in payload})
    r = await _market_request(
        request, "PUT", f"/api/v1/skills/{slug}", json_body=payload
    )
    if r.status_code != 200:
        raise _map_market_status(r)
    return r.json()


@router.delete("/skills/{slug}", status_code=204)
async def delist_market_skill(slug: str, request: Request) -> None:
    """Delist one of the user's own published skills from the market.

    ``slug`` is the market key of the user's own entry — the market
    service enforces the author guard (403 for non-authors). The local
    copy stays; the public entry disappears from the market.
    """
    r = await _market_request(request, "DELETE", f"/api/v1/skills/{slug}")
    if r.status_code not in (200, 204):
        raise _map_market_status(r)


# ── sync engine ─────────────────────────────────────────────────────


def _skill_store(request: Request):
    store = getattr(request.app.state, "skill_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="skill store not initialized")
    return store


async def _remote_manifest(request: Request) -> dict[str, dict[str, Any]]:
    """slug → cloud-copy meta for the user's personal space.

    The market joins cloud copies with public entries by content sha,
    so each copy carries the matched entry's ``author`` and
    ``market_slug`` (empty when there is no public match) and a
    ``delisted`` flag for copies of taken-down entries."""
    r = await _market_request(request, "GET", "/api/v1/me/skills")
    if r.status_code != 200:
        raise _map_market_status(r)
    out: dict[str, dict[str, Any]] = {}
    for i in r.json():
        out[i["slug"]] = {
            "sha": i.get("sha", ""),
            "author": str(i.get("author") or ""),
            "market_slug": str(i.get("market_slug") or ""),
            "delisted": bool(i.get("delisted")),
        }
    return out


async def _plan(request: Request) -> dict[str, dict[str, Any]]:
    """Per-skill sync decision. See module docstring for the sha rules."""
    store = _skill_store(request)
    remote = await _remote_manifest(request)
    actions: dict[str, dict[str, Any]] = {}
    for s in await store.list():
        if s.origin == "bundled":
            continue
        state = (await store.get_state(s.slug)).get("market") or {}
        base = state.get("base_sha")
        local_sha = await store.content_sha(s.slug)
        rmeta = remote.get(s.slug)
        rsha = rmeta["sha"] if rmeta is not None else None
        if rsha is None:
            # No cloud copy under this user's sync space — mirror
            # semantics: upload the local skill (a missing cloud copy
            # is never treated as a terminal "deleted" state; the
            # cloud is the backup of every local non-bundled skill).
            actions[s.slug] = {
                "action": "push",
                "local_sha": local_sha,
                "remote_sha": rsha,
                "base_sha": base,
            }
        elif local_sha == rsha:
            actions[s.slug] = {
                "action": "up-to-date",
                "local_sha": local_sha,
                "remote_sha": rsha,
                "base_sha": base,
            }
        elif base == rsha:
            # Cloud unchanged since last sync → local changed → push.
            actions[s.slug] = {
                "action": "push",
                "local_sha": local_sha,
                "remote_sha": rsha,
                "base_sha": base,
            }
        elif base is not None and base == local_sha:
            # Local untouched since last sync → cloud changed → pull.
            actions[s.slug] = {
                "action": "pull",
                "local_sha": local_sha,
                "remote_sha": rsha,
                "base_sha": base,
            }
        else:
            # Both moved (incl. never-synced with differing content).
            actions[s.slug] = {
                "action": "conflict",
                "local_sha": local_sha,
                "remote_sha": rsha,
                "base_sha": base,
            }
    for slug, rmeta in remote.items():
        rsha = rmeta["sha"]
        if slug not in actions:
            actions[slug] = {
                "action": "pull",
                "local_sha": None,
                "remote_sha": rsha,
                "base_sha": None,
            }
    return actions


async def _do_pull(request: Request, slug: str, rsha: str) -> None:
    store = _skill_store(request)
    r = await _market_request(request, "GET", f"/api/v1/me/skills/{slug}")
    if r.status_code != 200:
        raise _map_market_status(r)
    try:
        await store.import_zip(r.content, origin="market", overwrite=True)
        local_sha = await store.content_sha(slug)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    await store.patch_state(
        slug, {"market": {"local_sha": local_sha, "base_sha": rsha}}
    )
    # A sync pull that materialized a new local copy counts as a download.
    await inc_download(request, slug)


async def _do_push(request: Request, slug: str, expected_remote: str | None) -> str:
    store = _skill_store(request)
    zip_bytes = await store.export(slug)
    sha = await store.content_sha(slug)
    r = await _market_request(
        request,
        "PUT",
        f"/api/v1/me/skills/{slug}",
        json_body={
            "data": base64.b64encode(zip_bytes).decode(),
            "sha": sha,
            "base_sha": expected_remote,
        },
    )
    if r.status_code == 409:
        raise HTTPException(
            status_code=409, detail=f"cloud copy of '{slug}' changed concurrently"
        )
    if r.status_code not in (200, 201):
        raise _map_market_status(r)
    await store.patch_state(
        slug, {"market": {"local_sha": sha, "base_sha": sha}}
    )
    return sha


def _set_sha(pairs: list[tuple[str, str]]) -> str:
    """Collection-level fingerprint: sha256 over sorted ``slug:sha``
    pairs. Equal on both sides ⇔ the whole set is in sync (same slugs,
    same content) — one number that catches add/delete/change."""
    import hashlib

    h = hashlib.sha256()
    for slug, sha in sorted(pairs):
        h.update(f"{slug}:{sha}".encode())
        h.update(b"\0")
    return h.hexdigest()


@router.get("/sync")
async def sync_plan(request: Request) -> dict[str, Any]:
    """Dry run — what would a sync do right now."""
    actions = await _plan(request)
    store = _skill_store(request)
    local_pairs = [
        (s.slug, await store.content_sha(s.slug))
        for s in await store.list()
        if s.origin != "bundled"
    ]
    remote = await _remote_manifest(request)
    remote_pairs = [(k, v["sha"]) for k, v in remote.items()]
    local_set = _set_sha(local_pairs)
    remote_set = _set_sha(remote_pairs)
    return {
        "actions": actions,
        "conflicts": [s for s, a in actions.items() if a["action"] == "conflict"],
        "authors": {k: v["author"] for k, v in remote.items()},
        "market_slugs": {k: v["market_slug"] for k, v in remote.items()},
        "delisted": {k: v["delisted"] for k, v in remote.items()},
        "local_set_sha": local_set,
        "remote_set_sha": remote_set,
        "in_sync": local_set == remote_set,
    }


@router.post("/sync")
async def sync_execute(request: Request) -> dict[str, Any]:
    """Execute push/pull; leave conflicts for /sync/resolve."""
    actions = await _plan(request)
    result: dict[str, Any] = {"pushed": [], "pulled": [], "conflicts": [], "errors": []}
    for slug, a in actions.items():
        act = a["action"]
        try:
            if act == "push":
                await _do_push(request, slug, a.get("remote_sha"))
                result["pushed"].append(slug)
            elif act == "pull":
                await _do_pull(request, slug, a["remote_sha"])
                result["pulled"].append(slug)
            elif act == "conflict":
                result["conflicts"].append(slug)
        except HTTPException as e:
            if e.status_code == 409:
                result["conflicts"].append(slug)
            else:
                result["errors"].append({"slug": slug, "detail": e.detail})
    return result


@router.post("/sync/resolve")
async def sync_resolve(
    request: Request, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Resolve one conflict: ``{"slug": ..., "choice": "local"|"remote"}``."""
    slug = str(body.get("slug") or "")
    choice = str(body.get("choice") or "")
    if not slug or choice not in ("local", "remote"):
        raise HTTPException(status_code=400, detail="slug and choice are required")
    if choice == "local":
        sha = await _do_push(request, slug, None)  # CAS off: explicit user choice
        return {"slug": slug, "resolved": "local", "sha": sha}
    remote = await _remote_manifest(request)
    if slug not in remote:
        raise HTTPException(status_code=404, detail=f"no cloud copy of '{slug}'")
    await _do_pull(request, slug, remote[slug]["sha"])
    return {"slug": slug, "resolved": "remote"}
