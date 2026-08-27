"""Runtime configuration for mhc-desktop-backend.

Read once at startup; immutable. All fields are tunable through environment
variables (prefix ``MHC_``); see ``main.py`` for the entry point.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from mhc_desktop_backend import __app_name__


@dataclass(frozen=True)
class Config:
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    # Brand token used by the boot-time log banner. Late-boot surfaces
    # (FastAPI title, MCP ``clientInfo.name``, onboarding placeholder)
    # read from ``app.state.meta["brand"]["name"]`` — the deploy seeds
    # that via ``build_default_app(meta={"brand":{"name":...}})`` or by
    # setting ``MHC_APP_NAME`` before ``load_config()`` runs. See
    # ``docs/BRANDING.md``.
    app_name: str = __app_name__


def load_config() -> Config:
    """Build :class:`Config` from environment variables.

    Defaults match ``scripts/dev-mhc-desktop.sh`` so the dev loop works out
    of the box; everything is overridable per process.
    """
    return Config(
        debug=os.getenv("MHC_DEBUG", "1") == "1",
        host=os.getenv("MHC_HOST", "127.0.0.1"),
        port=int(os.getenv("MHC_PORT", "8765")),
        app_name=os.getenv("MHC_APP_NAME") or __app_name__,
    )
