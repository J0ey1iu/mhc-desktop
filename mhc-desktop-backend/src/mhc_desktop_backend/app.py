"""FastAPI app factory for mhc-desktop-backend.

Wires the provider store, providers router, and SSE chat router.
The store lives on ``app.state.provider_store`` so routers can pull
it via ``Depends`` without leaking globals.

Default wiring lives in :mod:`mhc_desktop_deploy.impls.file_stores`;
the kernel whl imports nothing from there. With no kwargs at all,
``create_app`` builds a no-store app (every endpoint except
``/api/v1/health`` and ``/ready`` will 503). Callers in dev / packaged
installs wire the defaults via :func:`mhc_desktop_deploy.assemble.build_default_app`.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mhc_desktop_backend import __version__
from mhc_desktop_backend.api.auth import router as auth_router
from mhc_desktop_backend.api.chat import router as chat_router
from mhc_desktop_backend.api.mcp import router as mcp_router
from mhc_desktop_backend.api.meta import router as meta_router
from mhc_desktop_backend.api.metrics import router as metrics_router
from mhc_desktop_backend.api.onboarding import router as onboarding_router
from mhc_desktop_backend.api.prefs import router as prefs_router
from mhc_desktop_backend.api.providers import router as providers_router
from mhc_desktop_backend.api.sessions import router as sessions_router
from mhc_desktop_backend.api.skills import router as skills_router
from mhc_desktop_backend.api.tools import router as tools_router
from mhc_desktop_backend.auth.middleware import DEFAULT_EXEMPT_PATHS
from mhc_desktop_backend.config import Config, load_config
from mhc_desktop_backend.llm import PRESETS as _DEFAULT_PRESETS
from mhc_desktop_backend.llm.presets import Preset
from mhc_desktop_backend.onboarding import (
    DEFAULT_ONBOARDING_CARDS,
    OnboardingCard,
)
from mhc_desktop_backend.protocols import (
    AuthProviderProtocol,
    ChatPolicy,
    MCPManagerProtocol,
    MCPStoreProtocol,
    MetricsRepositoryProtocol,
    PrefsStoreProtocol,
    ProviderStoreProtocol,
    SessionStoreProtocol,
    SkillStoreProtocol,
    StreamRegistryProtocol,
    ToolExecutorRegistryProtocol,
    ToolStoreProtocol,
)

logger = logging.getLogger("mhc_desktop_backend")


def create_app(
    config: Config | None = None,
    *,
    sessions: SessionStoreProtocol | None = None,
    providers: ProviderStoreProtocol | None = None,
    skills: SkillStoreProtocol | None = None,
    mcp_store: MCPStoreProtocol | None = None,
    mcp_manager: MCPManagerProtocol | None = None,
    tools: ToolStoreProtocol | None = None,
    stream_registry: StreamRegistryProtocol | None = None,
    prefs: PrefsStoreProtocol | None = None,
    metrics: MetricsRepositoryProtocol | None = None,
    auth: AuthProviderProtocol | None = None,
    auth_exempt_paths: tuple[str, ...] = DEFAULT_EXEMPT_PATHS,
    auth_upstream_header_prefix: str = "x-mhc-upstream-",
    scope_required_for: Callable[[str], frozenset[str]] | None = None,
    provider_presets: list[Preset] = list(_DEFAULT_PRESETS),
    provider_types: frozenset[str] = frozenset({"openai", "anthropic"}),
    system_prompt_base: str | None = None,
    onboarding_cards: list[OnboardingCard] | None = None,
    chat_policy: ChatPolicy | None = None,
    tool_executor_registry: ToolExecutorRegistryProtocol | None = None,
    content_packs_root: Path | None = None,
    meta: dict[str, Any] | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Every deploy-tunable is a kwarg; nothing in the kernel reaches
    out to environment variables or hardcoded paths. The deploy
    package wires defaults via
    :func:`mhc_desktop_deploy.assemble.build_default_app`. Per-kwarg
    semantics are documented alongside each Protocol type; this
    docstring covers only the contract.

    **Fail-closed**: in non-debug mode (``config.debug is False``)
    ``auth`` is required. Boot fails loud with a descriptive error
    if a deploy forgets ``auth=`` rather than serving traffic
    unauthenticated. Debug mode keeps the historical "no auth wired"
    path so local iteration works without an IdP.
    """
    cfg = config or load_config()

    if auth is None and not cfg.debug:
        raise RuntimeError(
            "create_app: auth provider is required in non-debug mode "
            "(cfg.debug=False). Pass an AuthProviderProtocol via the "
            "``auth=`` kwarg. The deploy package's "
            "build_default_app() supplies MockAuthProvider for "
            "development; production deploys must replace it with "
            "their IdP adapter."
        )

    policy = chat_policy or ChatPolicy()
    cards = (
        list(onboarding_cards)
        if onboarding_cards is not None
        else list(DEFAULT_ONBOARDING_CARDS)
    )

    store: ProviderStoreProtocol | None = providers
    sessions_impl: SessionStoreProtocol | None = sessions
    skill_store: SkillStoreProtocol | None = skills
    mcp_store_impl: MCPStoreProtocol | None = mcp_store
    tools_impl: ToolStoreProtocol | None = tools
    prefs_impl: PrefsStoreProtocol | None = prefs
    metrics_repo: MetricsRepositoryProtocol | None = metrics
    registry_impl: StreamRegistryProtocol | None = stream_registry
    mcp_manager_impl: MCPManagerProtocol | None = mcp_manager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Boot phase: when ``content_packs_root`` is set, materialise
        # bundled content packs into the user data dir. The root is
        # an explicit kwarg — deploys decide where the packs live;
        # the kernel no longer reads ``MHC_RESOURCES_PATH`` itself.
        # ``MHC_RESOURCES_PATH`` is honoured as a back-compat fallback
        # only when ``content_packs_root`` is None.
        cp_root = app.state.content_packs_root
        if cp_root is None:
            env_rp = os.environ.get("MHC_RESOURCES_PATH", "").strip()
            if env_rp:
                cp_root = Path(env_rp) / "content-packs"
        if (
            cp_root is not None
            and cp_root.is_dir()
            and all(
                getattr(app.state, name, None) is not None
                for name in ("skill_store", "tool_store", "mcp_store")
            )
        ):
            from mhc_desktop_backend.content_packs import materialize_bundled

            await materialize_bundled(
                content_root=cp_root,
                skill_store=app.state.skill_store,
                tool_store=app.state.tool_store,
                mcp_store=app.state.mcp_store,
            )

        # Built-in system tools (load_skill) — registered after
        # content packs so a user-imported ``load_skill`` (rare,
        # but valid) gets overwritten by the kernel's authoritative
        # version. Always re-asserted at startup so the kernel
        # owns the canonical schema and the user can't permanently
        # disable it.
        from mhc_desktop_backend.tools.builtin import ensure_builtin_tools

        await ensure_builtin_tools(app)
        yield
        reg: StreamRegistryProtocol | None = app.state.stream_registry
        if reg is not None:
            active = reg.active()
            if active:
                logger.info(
                    "shutdown.cancel active=%s — waiting up to 3s",
                    ",".join(active),
                )
                await reg.cancel_all(timeout=3.0)
        for owned in (
            getattr(app.state, "session_store", None),
            getattr(app.state, "provider_store", None),
            getattr(app.state, "skill_store", None),
            getattr(app.state, "mcp_store", None),
            getattr(app.state, "tool_store", None),
            getattr(app.state, "metrics_repo", None),
        ):
            if owned is not None and hasattr(owned, "close"):
                try:
                    await owned.close()
                except Exception:  # pragma: no cover — best effort
                    logger.exception("shutdown.close failed")

    # Brand resolution: caller-supplied ``meta["brand"]["name"]``
    # wins, then ``cfg.app_name`` (env ``MHC_APP_NAME``), then
    # ``__app_name__``. We seed the resolved value back into
    # ``app.state.meta["brand"]["name"]`` so every late-boot
    # consumer (FastAPI title, MCP clientInfo, onboarding
    # placeholder) reads the same token.
    seed_meta = dict(meta) if meta else {}
    brand_name = (
        (seed_meta.get("brand") or {}).get("name") or cfg.app_name
    )
    seed_meta.setdefault("brand", {})["name"] = brand_name

    app = FastAPI(
        title=f"{brand_name} API",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.provider_store = store
    app.state.session_store = sessions_impl
    app.state.skill_store = skill_store
    app.state.mcp_store = mcp_store_impl
    app.state.mcp_manager = mcp_manager_impl
    app.state.tool_store = tools_impl
    app.state.stream_registry = registry_impl
    app.state.prefs_store = prefs_impl
    app.state.metrics_repo = metrics_repo
    app.state.config = cfg
    # Deploy-tunable state. The routers read these off ``app.state``
    # via their ``get_*`` helpers so a deploy can override any of
    # them at construction time without touching the kernel.
    app.state.provider_presets = list(provider_presets)
    app.state.provider_types = set(provider_types)
    app.state.system_prompt_base = system_prompt_base
    app.state.chat_policy = policy
    app.state.onboarding_cards = cards
    app.state.tool_executor_registry = tool_executor_registry
    app.state.content_packs_root = content_packs_root
    app.state.meta = seed_meta

    # Install auth middleware last so it sees the final app state.
    if auth is not None:
        from mhc_desktop_backend.auth.middleware import install_auth

        app.state.auth_provider = auth
        install_auth(
            app,
            auth,
            exempt_paths=auth_exempt_paths,
            upstream_header_prefix=auth_upstream_header_prefix,
            scope_required_for=scope_required_for,
        )

    # CORS — defaults to open in debug mode for the browser dev
    # workflow; production deploys pass an explicit allow-list via
    # ``cors_origins``. ``None`` + non-debug = no CORS middleware
    # installed (Electron renderer talks to localhost without CORS).
    cors = cors_origins if cors_origins is not None else (["*"] if cfg.debug else None)
    if cors is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/v1/health")
    async def health() -> dict:
        # ``data_dir`` is owned by the deploy package (file-backed
        # stores live there); if deploy isn't installed we surface
        # ``<unset>`` rather than crashing the kernel.
        try:
            from mhc_desktop_deploy.impls.file_stores import paths as _dp

            data_dir = str(_dp.DATA_DIR)
        except Exception:  # pragma: no cover — deploy absent
            data_dir = "<unset>"
        return {
            "status": "ok",
            "version": __version__,
            "debug": cfg.debug,
            "data_dir": data_dir,
        }

    @app.get("/ready")
    async def ready() -> dict:
        return {"status": "ready"}

    app.include_router(providers_router)
    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(skills_router)
    app.include_router(mcp_router)
    app.include_router(tools_router)
    app.include_router(prefs_router)
    app.include_router(metrics_router)
    app.include_router(onboarding_router)
    app.include_router(auth_router)
    app.include_router(meta_router)

    _mount_spa(app)

    logger.info("%s ready (debug=%s)", brand_name, cfg.debug)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Mount the built SPA from ``src/mhc_desktop_backend/static/`` and
    fall back to ``index.html`` for unknown GETs so the SPA router (hash
    routing) keeps working on reload.

    The Electron host loads the backend directly, so the renderer sees
    the SPA at the same origin as the API — no CORS, no separate static
    server, no ``file://`` protocol pitfalls (relative ``/api/v1/...``
    requests would otherwise break under ``file://``).
    """
    static_dir = Path(__file__).resolve().parent / "static"
    if not static_dir.is_dir():
        logger.info("spa.static.missing dir=%s", static_dir)
        return

    for sub in ("assets", "fonts"):
        p = static_dir / sub
        if p.is_dir():
            app.mount(
                f"/{sub}",
                StaticFiles(directory=str(p)),
                name=f"mhc_desktop_{sub}",
            )

    favicon = static_dir / "favicon.svg"
    if favicon.is_file():

        @app.get("/favicon.svg", include_in_schema=False)
        async def _favicon() -> FileResponse:
            return FileResponse(str(favicon))

    index = static_dir / "index.html"

    @app.middleware("http")
    async def _spa_fallback(request, call_next):
        response = await call_next(request)
        if (
            response.status_code == 404
            and request.method == "GET"
            and not request.url.path.startswith("/api")
            and not request.url.path.startswith("/docs")
            and not request.url.path.startswith("/openapi")
            and not request.url.path.startswith("/redoc")
            and not request.url.path.startswith("/assets")
            and not request.url.path.startswith("/fonts")
        ):
            if index.is_file():
                return FileResponse(str(index))
        return response

    # API responses must never be cached by the renderer's HTTP
    # cache. Without an explicit Cache-Control the browser may
    # heuristically re-serve a stale GET (e.g. the provider list
    # from before a DELETE) and the UI would resurrect deleted
    # rows on the next refresh. no-store on everything /api is
    # the cheap, total fix — these endpoints are cheap local
    # file reads, never a bottleneck.
    @app.middleware("http")
    async def _no_store_api(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api") or path in ("/ready", "/health"):
            response.headers["Cache-Control"] = "no-store"
        return response
