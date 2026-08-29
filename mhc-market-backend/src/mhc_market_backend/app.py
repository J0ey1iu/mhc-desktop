"""FastAPI app factory for the skill market service."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response

from .accounts import login as account_login
from .accounts import resolve_ops_user, resolve_user
from .logging_setup import setup_file_logging
from .store import CATEGORIES, MarketError, MarketStore, decode_b64_zip

logger = logging.getLogger("mhc_market")


class RequestLogMiddleware:
    """One line per request: method, path, status, duration, user.

    Goes to the file handler set up in ``create_app`` so the log
    directory alone is enough to reconstruct what happened.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            ms = (time.perf_counter() - start) * 1000
            path = scope.get("path", "")
            user = "-"
            for k, v in scope.get("headers", []):
                if k == b"x-mhc-user":
                    user = v.decode("latin-1")
                    break
            level = logging.INFO if status_holder["status"] < 500 else logging.ERROR
            logger.log(
                level,
                "request method=%s path=%s status=%s ms=%.1f user=%s",
                scope.get("method", ""),
                path,
                status_holder["status"],
                ms,
                user,
            )


def create_app(
    *,
    data_root: Path | None = None,
    secret: str | None = None,
    admin_token: str | None = None,
) -> FastAPI:
    """``data_root``/``secret`` default to env ``MHC_MARKET_DATA`` /
    ``MHC_MARKET_SECRET``; boot fails loud without a secret (fail-closed,
    same rule as the kernel's auth). ``admin_token`` (env
    ``MHC_MARKET_ADMIN_TOKEN``) gates the ``/api/v1/admin`` endpoints."""
    root = Path(data_root or os.environ.get("MHC_MARKET_DATA", "") or Path.home() / ".mhc-market")
    sec = secret or os.environ.get("MHC_MARKET_SECRET", "").strip()
    if not sec:
        raise RuntimeError(
            "market secret is required: set MHC_MARKET_SECRET or pass secret="
        )
    store = MarketStore(root)
    admin = (admin_token or os.environ.get("MHC_MARKET_ADMIN_TOKEN", "")).strip()

    # Durable logs: <data_root>/logs/market.log, daily rotation.
    # uvicorn.error propagates into ``uvicorn`` — attaching to both
    # would double-log; attach to the top of each chain instead.
    log_dir = setup_file_logging(root, ("mhc_market", "uvicorn", "uvicorn.access"))

    def auth_user(request: Request) -> str:
        try:
            user = resolve_user(request, sec)
            return user
        except HTTPException:
            logger.warning("auth.failed path=%s", request.url.path)
            raise

    def err(e: MarketError) -> HTTPException:
        return HTTPException(status_code=e.status, detail=e.detail)

    def require_admin(request: Request) -> None:
        if not admin:
            raise HTTPException(status_code=403, detail="admin not configured")
        if request.headers.get("x-mhc-admin") != admin:
            raise HTTPException(status_code=403, detail="invalid admin token")

    def _require_ops(request: Request) -> None:
        """Ops read-access: admin header (legacy), an admin-flagged bearer
        login token, or the kernel HMAC identity."""
        # Trust the server's own admin secret if configured.
        if admin and request.headers.get("x-mhc-admin") == admin:
            return
        # Otherwise require an admin-role login token (or HMAC).
        resolve_ops_user(request, sec)

    app = FastAPI(title="mhc-market", docs_url="/docs")
    app.state.store = store
    app.add_middleware(RequestLogMiddleware)

    @app.get("/api/v1/health")
    async def health() -> dict:
        return {"status": "ok", "service": "mhc-market", "log_dir": str(log_dir)}

    @app.post("/api/v1/auth/login")
    async def login(body: dict) -> dict:
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        token, is_admin = account_login(username, password)
        logger.info("auth.login user=%s admin=%s", username, is_admin)
        return {"token": token, "username": username, "is_admin": is_admin}

    @app.get("/api/v1/categories")
    async def categories() -> list[str]:
        return list(CATEGORIES)

    # ── public registry ─────────────────────────────────────────────

    @app.get("/api/v1/skills", response_model=list[dict])
    async def list_skills(
        q: str = "",
        category: str = "",
        sort: str = "downloads",
        featured: bool = False,
        limit: int | None = None,
        offset: int = 0,
        *,
        response: Response,
    ) -> list[dict]:
        items = store.list_public(
            q=q, category=category, sort=sort, featured=featured,
            limit=limit, offset=offset,
        )
        response.headers["X-Total-Count"] = str(
            store.count_public(q=q, category=category, featured=featured)
        )
        return items

    @app.get("/api/v1/skills/{slug}")
    async def get_skill(slug: str) -> dict:
        try:
            return store.get_public(slug)
        except MarketError as e:
            raise err(e) from None

    @app.get("/api/v1/skills/{slug}/files")
    async def skill_files(slug: str) -> list[dict]:
        try:
            return store.get_files(slug)
        except MarketError as e:
            raise err(e) from None

    @app.get("/api/v1/skills/{slug}/download")
    async def download(slug: str) -> Response:
        try:
            blob, meta = store.read_public_zip(slug)
        except MarketError as e:
            raise err(e) from None
        return Response(
            content=blob,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}.skill.zip"',
                "X-Content-Sha": meta["sha"],
            },
        )

    @app.put("/api/v1/skills/{slug}")
    async def publish(slug: str, body: dict, request: Request) -> dict:
        user = auth_user(request)
        data = body.get("data")
        if not isinstance(data, str) or not data:
            raise HTTPException(status_code=400, detail="zip data (base64) required")
        try:
            reserved = {"data", "sha", "display_name", "description", "category", "icon"}
            extras = {k: v for k, v in body.items() if k not in reserved}
            meta = store.publish(
                slug=slug,
                user=user,
                zip_bytes=decode_b64_zip(data),
                sha=str(body.get("sha") or ""),
                display_name=str(body.get("display_name") or slug),
                description=str(body.get("description") or ""),
                category=str(body.get("category") or "other"),
                icon=str(body.get("icon") or ""),
                meta=extras or None,
            )
            logger.info(
                "skill.published slug=%s author=%s sha=%s size=%s",
                slug, user, meta["sha"][:12], meta["size"],
            )
            return meta
        except MarketError as e:
            logger.warning("skill.publish_rejected slug=%s user=%s status=%s", slug, user, e.status)
            raise err(e) from None

    @app.delete("/api/v1/skills/{slug}", status_code=204)
    async def delist(slug: str, request: Request) -> None:
        user = auth_user(request)
        try:
            store.delete_public(slug, user)
        except MarketError as e:
            raise err(e) from None

    # ── ratings / reviews ───────────────────────────────────────────

    @app.get("/api/v1/skills/{slug}/rating")
    async def rating(slug: str) -> dict:
        try:
            return store.get_rating(slug)
        except MarketError as e:
            raise err(e) from None

    @app.get("/api/v1/skills/{slug}/reviews")
    async def reviews(slug: str) -> list[dict]:
        try:
            return store.list_reviews(slug)
        except MarketError as e:
            raise err(e) from None

    @app.post("/api/v1/skills/{slug}/reviews", status_code=201)
    async def add_review(slug: str, body: dict, request: Request) -> dict:
        user = auth_user(request)
        if "rating" not in body:
            raise HTTPException(status_code=400, detail="rating required")
        try:
            return store.add_review(
                slug=slug,
                user=user,
                rating=int(body["rating"]),
                comment=str(body.get("comment") or ""),
            )
        except MarketError as e:
            raise err(e) from None

    # ── admin / operator ────────────────────────────────────────────

    @app.get("/api/v1/admin/skills", response_model=list[dict])
    async def admin_skills(
        q: str = "",
        category: str = "",
        sort: str = "downloads",
        limit: int | None = None,
        offset: int = 0,
        *,
        request: Request,
        response: Response,
    ) -> list[dict]:
        require_admin(request)
        items = store.list_public(
            q=q, category=category, sort=sort, featured=False,
            limit=limit, offset=offset,
        )
        response.headers["X-Total-Count"] = str(
            store.count_public(q=q, category=category, featured=False)
        )
        return items

    @app.post("/api/v1/admin/skills/{slug}/featured")
    async def admin_featured(slug: str, body: dict, request: Request) -> dict:
        require_admin(request)
        feature = bool(body.get("featured", False))
        try:
            return store.set_featured(slug, feature)
        except MarketError as e:
            raise err(e) from None

    @app.delete("/api/v1/admin/skills/{slug}", status_code=204)
    async def admin_delist(slug: str, request: Request) -> None:
        require_admin(request)
        try:
            store.admin_delete_public(slug)
        except MarketError as e:
            raise err(e) from None

    # ── ops: monitoring / logs / backup ─────────────────────────────

    @app.get("/api/v1/admin/stats")
    async def admin_stats(request: Request) -> dict:
        require_admin(request)
        return store.stats()

    @app.get("/api/v1/admin/diagnostics")
    async def admin_diagnostics(request: Request) -> dict:
        require_admin(request)
        d = store.integrity()
        d["log_dir"] = str(log_dir)
        return d

    @app.get("/api/v1/admin/logs")
    async def admin_logs(
        *,
        lines: int = 200,
        request: Request,
    ) -> dict:
        require_admin(request)
        f = log_dir / "market.log"
        if not f.is_file():
            return {"lines": []}
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            return {"lines": fh.read().splitlines()[-max(1, lines):]}

    @app.post("/api/v1/admin/backup")
    async def admin_backup(request: Request) -> dict:
        require_admin(request)
        name = store.create_backup()
        return {"backup": name}

    @app.get("/api/v1/admin/backups")
    async def admin_backups(request: Request) -> list[dict]:
        require_admin(request)
        return store.list_backups()

    @app.post("/api/v1/admin/backups/{name}/restore")
    async def admin_restore(name: str, request: Request) -> dict:
        require_admin(request)
        try:
            store.restore_backup(name)
        except MarketError as e:
            raise err(e) from None
        return {"restored": name}

    # ── personal space (cloud backup + sync target) ─────────────────

    @app.get("/api/v1/me/skills")
    async def my_skills(request: Request) -> list[dict]:
        return store.list_user(auth_user(request))

    @app.get("/api/v1/me/skills/{slug}")
    async def get_my_skill(slug: str, request: Request) -> Response:
        user = auth_user(request)
        try:
            blob, meta = store.read_user_zip(user, slug)
        except MarketError as e:
            raise err(e) from None
        return Response(
            content=blob,
            media_type="application/zip",
            headers={"X-Content-Sha": meta["sha"]},
        )

    @app.put("/api/v1/me/skills/{slug}")
    async def put_my_skill(slug: str, body: dict, request: Request) -> dict:
        user = auth_user(request)
        data = body.get("data")
        if not isinstance(data, str) or not data:
            raise HTTPException(status_code=400, detail="zip data (base64) required")
        base_sha = body.get("base_sha")
        if base_sha is not None and not isinstance(base_sha, str):
            raise HTTPException(status_code=400, detail="base_sha must be a string")
        try:
            meta = store.put_user(
                user=user,
                slug=slug,
                zip_bytes=decode_b64_zip(data),
                sha=str(body.get("sha") or ""),
                base_sha=base_sha,
            )
            logger.info("sync.pushed user=%s slug=%s sha=%s", user, slug, meta["sha"][:12])
            return meta
        except MarketError as e:
            if e.status == 409:
                logger.warning("sync.cas_conflict user=%s slug=%s", user, slug)
            raise err(e) from None

    @app.get("/api/v1/me/skills/{slug}/md")
    async def get_my_skill_md(slug: str, request: Request) -> dict:
        user = auth_user(request)
        try:
            return store.get_user_md(user, slug)
        except MarketError as e:
            raise err(e) from None

    @app.post("/api/v1/me/skills/{slug}/edit")
    async def edit_my_skill(slug: str, body: dict, request: Request) -> dict:
        user = auth_user(request)
        try:
            return store.edit_user(
                user=user,
                slug=slug,
                description=body.get("description"),
                body=body.get("body"),
            )
        except MarketError as e:
            raise err(e) from None

    @app.delete("/api/v1/me/skills/{slug}", status_code=204)
    async def delete_my_skill(slug: str, request: Request) -> None:
        user = auth_user(request)
        try:
            store.delete_user(user, slug)
        except MarketError as e:
            raise err(e) from None

    # ── stories (user-submitted promo articles) ─────────────────

    @app.get("/api/v1/stories")
    async def list_stories() -> list[dict]:
        return store.list_stories()

    @app.get("/api/v1/stories/{story_id}")
    async def get_story(story_id: str) -> dict:
        try:
            return store.get_story(story_id)
        except MarketError as e:
            raise err(e) from None

    @app.post("/api/v1/stories", status_code=201)
    async def add_story(body: dict, request: Request) -> dict:
        user = auth_user(request)
        try:
            return store.add_story(
                user=user,
                title=str(body.get("title") or ""),
                skill_slug=str(body.get("skill_slug") or ""),
                content=str(body.get("content") or ""),
            )
        except MarketError as e:
            raise err(e) from None

    # ── usage ingest + ops dashboard (business metrics) ──────────────

    @app.post("/api/v1/usage")
    async def ingest_usage(body: dict, request: Request) -> dict:
        """Batch usage report from a desktop end.

        Body: ``{"end_id": "...", "day": "YYYY-MM-DD", "events": [
        {"kind": "skill_invoke", "slug": "...", "count": 1}, ...]}``.
        Idempotent per (day, end_id, kind, slug) — re-sends no-op.
        """
        # Any authenticated principal (kernel HMAC or bearer) may report.
        user = resolve_user(request, sec)
        end_id = str(body.get("end_id") or "").strip() or user
        day = str(body.get("day") or "").strip()
        events = body.get("events") or []
        if not day or not isinstance(events, list):
            raise HTTPException(status_code=400, detail="day and events are required")
        if not end_id:
            raise HTTPException(status_code=400, detail="end_id is required")
        for ev in events:
            if not isinstance(ev, dict):
                continue
            kind = str(ev.get("kind") or "")
            if kind not in ("skill_download", "skill_invoke", "skill_enable", "session", "message"):
                continue
            store.record_usage(
                end_id=end_id,
                day=day,
                kind=kind,
                slug=(str(ev.get("slug") or "").strip() or None),
                count=max(1, int(ev.get("count") or 1)),
            )
        logger.info("usage.ingest end=%s day=%s events=%s", end_id, day, len(events))
        return {"ok": True, "recorded": len(events)}

    def _ops_kind(kind: str) -> bool:
        return kind in ("skill_download", "skill_invoke", "skill_enable")

    @app.get("/api/v1/ops/overview")
    async def ops_overview(request: Request, days: int = 30) -> dict:
        _require_ops(request)
        return store.ops_overview(days=days)

    @app.get("/api/v1/ops/ranking")
    async def ops_ranking(request: Request, kind: str = "skill_invoke", days: int = 30, limit: int = 10):
        _require_ops(request)
        if not _ops_kind(kind):
            raise HTTPException(status_code=400, detail="invalid kind")
        return store.ops_ranking(kind=kind, days=days, limit=limit)

    @app.get("/api/v1/ops/trend")
    async def ops_trend(request: Request, days: int = 30) -> list[dict]:
        _require_ops(request)
        return store.ops_trend(days=days)

    @app.get("/api/v1/ops/growth")
    async def ops_growth(request: Request) -> dict:
        _require_ops(request)
        return store.ops_growth()

    # ── SPA hosting (the market web app, built into dist/) ─────────

    dist = Path(__file__).resolve().parent / "dist"
    if dist.is_dir():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/assets", StaticFiles(directory=str(dist / "assets")), name="assets"
        )

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(str(f))
            return FileResponse(str(dist / "index.html"))

    return app
