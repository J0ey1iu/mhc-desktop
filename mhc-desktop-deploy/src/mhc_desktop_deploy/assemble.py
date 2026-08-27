"""Default deployment wiring for mhc-desktop.

``build_default_app()`` builds the canonical ``create_app(...)`` call
with every default file-backed store plugged in. Enterprise forks
override specific kwargs (auth provider, scopes, system prompt
base, provider presets, chat policy, content-packs root, runtime
meta, cors origins, …) to integrate with their IdP / storage /
branding; everything else stays on the kernel's defaults.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI

from mhc_desktop_backend import create_app
from mhc_desktop_backend.config import Config, load_config

logger = logging.getLogger("mhc_desktop_deploy")


def _resolve_data_dir(override: object) -> tuple[Path, Path | None]:
    """Pick the file-backed stores' root directory.

    Returns ``(data_dir, factory_data_dir)``. ``data_dir`` is the
    canonical path used for ``ensure_dirs`` and the meta manifest.
    ``factory_data_dir`` is the value passed to the per-store
    factory functions; ``None`` means "let the factory read the
    module-level ``paths.*`` constants" — the test fixtures
    monkey-patch those constants to redirect to a tmp dir, and we
    only force a real path when the deploy explicitly set one.

    Priority: explicit ``data_dir=`` kwarg > ``MHC_DATA_DIR`` env
    var > ``~/.mhc-desktop`` default.
    """
    if override is not None:
        d = Path(str(override)).expanduser().resolve()
        return d, d
    env = os.environ.get("MHC_DATA_DIR", "").strip()
    if env:
        d = Path(env).expanduser().resolve()
        return d, d
    return Path.home() / ".mhc-desktop", None


def build_default_app(**overrides: object) -> FastAPI:
    """Wire every default file-backed store + ``MockAuthProvider``.

    Recognised convenience kwargs (consumed here, NOT forwarded to
    ``create_app``):

    * ``data_dir`` — root of the file-backed stores. ``None``
      honours ``MHC_DATA_DIR`` env var, then falls back to
      ``~/.mhc-desktop``.
    * ``config`` — :class:`Config` instance, useful for tests.

    Anything else in ``overrides`` is forwarded verbatim to
    ``create_app(...)`` so enterprise forks can drop in any of
    the kernel's Protocol types, presets, scope rules, etc., with
    no further plumbing here.
    """
    cfg: Config = (
        overrides.pop("config")
        if isinstance(overrides.get("config"), Config)
        else load_config()
    )
    data_dir, factory_data_dir = _resolve_data_dir(overrides.pop("data_dir", None))

    # Lazy imports: pulling every concrete store on every import
    # of this module would defeat the point of having a deploy
    # package that only includes what the customer actually uses.
    from mhc_desktop_deploy.impls.file_stores import ensure_dirs
    from mhc_desktop_deploy.impls.file_stores._defaults import (
        default_metrics_repo,
        default_mcp_manager,
        default_mcp_store,
        default_prefs_store,
        default_provider_store,
        default_session_store,
        default_skill_store,
        default_stream_registry,
        default_tool_store,
    )

    # ``ensure_dirs`` seeds the providers.json template on a fresh
    # install (only when missing / empty, so user edits survive a
    # restart) and creates the skills/mcp/tools/sessions subdirs.
    ensure_dirs()

    # Brand: explicit ``brand_name=`` kwarg > env (already loaded
    # into ``cfg.app_name``) > kernel default. See ``docs/BRANDING.md``.
    brand_name: str = str(overrides.pop("brand_name", None) or cfg.app_name)

    kwargs: dict[str, object] = {
        "config": cfg,
        "sessions": default_session_store(factory_data_dir),
        "providers": default_provider_store(factory_data_dir),
        "skills": default_skill_store(factory_data_dir),
        "mcp_store": default_mcp_store(factory_data_dir),
        "mcp_manager": default_mcp_manager(
            factory_data_dir,
            client_name=brand_name,
        ),
        "tools": default_tool_store(factory_data_dir),
        "stream_registry": default_stream_registry(),
        "prefs": default_prefs_store(factory_data_dir),
        "metrics": default_metrics_repo(),
    }
    # Auth is the only default we always wire — enterprise deployments
    # override ``auth=`` with their IdP adapter; the mock ships so
    # ``python -m mhc_desktop_deploy`` boots a working login flow.
    if "auth" not in overrides:
        from mhc_desktop_deploy.impls.auth.mock import MockAuthProvider

        kwargs["auth"] = MockAuthProvider()

    # Bundled content packs: honour an explicit override, fall back
    # to ``MHC_RESOURCES_PATH/content-packs`` (Electron-host
    # convention). The kernel just runs the materialization helper
    # when a root is supplied.
    if "content_packs_root" not in overrides:
        env_rp = os.environ.get("MHC_RESOURCES_PATH", "").strip()
        if env_rp:
            kwargs["content_packs_root"] = Path(env_rp) / "content-packs"

    # Runtime meta: deploy can override or merge; we seed a
    # minimal default (data_dir + bundled-content placeholders) so
    # the forked frontend can show useful info out of the box.
    user_meta = overrides.pop("meta", None)
    base_meta = {
        "version": create_app.__module__,
        "data_dir": str(data_dir),
        "debug": cfg.debug,
        # Brand token consumed by FastAPI title, MCP ``clientInfo``,
        # and the onboarding placeholder renderer. See
        # ``docs/BRANDING.md`` for the full rebrand recipe.
        "brand": {"name": brand_name},
        # Empty by default; deploys that stage content packs at boot
        # can populate these via the ``meta=`` override.
        "bundled": {"skills": [], "mcps": [], "tools": []},
    }
    if isinstance(user_meta, dict):
        merged = {**base_meta, **user_meta}
        # Preserve the empty-list defaults under ``bundled`` so a
        # deploy that only sets e.g. ``bundled.skills`` doesn't
        # accidentally drop ``bundled.mcps`` / ``bundled.tools``.
        merged["bundled"] = {**base_meta["bundled"], **(user_meta.get("bundled") or {})}
        kwargs["meta"] = merged
    elif user_meta is not None:
        kwargs["meta"] = user_meta
    else:
        kwargs["meta"] = base_meta

    kwargs.update(overrides)
    app = create_app(**kwargs)  # type: ignore[arg-type]
    logger.info(
        "mhc-desktop deploy wired (data_dir=%s, debug=%s)",
        data_dir,
        cfg.debug,
    )
    return app


__all__ = ["build_default_app"]
