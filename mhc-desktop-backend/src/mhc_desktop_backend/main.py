"""Entry point for the mhc-desktop-backend HTTP server.

Usage:
    uv run python -m mhc_desktop_backend

Env:
    MHC_HOST, MHC_PORT, MHC_DEBUG, MHC_RELOAD, MH_LOG_LEVEL
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import uvicorn

from mhc_desktop_backend import __version__
from mhc_desktop_backend.config import load_config


def _proactor_loop_factory(*args: Any, **kwargs: Any) -> asyncio.AbstractEventLoop:
    """Always use ProactorEventLoop on Windows.

    uvicorn's default ``asyncio_loop_factory`` picks SelectorEventLoop
    on Windows whenever ``reload`` or ``workers>1`` is set (its
    ``use_subprocess`` flag), and SelectorEventLoop on Windows has NO
    subprocess support — ``asyncio.create_subprocess_exec`` raises
    NotImplementedError. Our PowerShell tool spawns a child process,
    so dev mode (reload on) would break every tool call. Proactor is
    the only Windows loop that can spawn subprocesses.

    Note: for a custom ``loop=`` string uvicorn passes this factory
    straight to ``asyncio.run(loop_factory=...)``, which calls it with
    NO arguments and expects an event-loop *instance* back (built-in
    factories return a class instead — we must not). ``*args/**kwargs``
    keep us compatible with both call shapes.
    """
    return asyncio.ProactorEventLoop()


# Asia/Shanghai never observes DST — a fixed +08:00 offset is exact and
# can't drift with the build machine's or the user machine's local
# timezone. That means we don't need tzdata / a zoneinfo database.
_SHANGHAI_OFFSET = 8 * 3600  # seconds


def make_log_formatter(fmt: str) -> logging.Formatter:
    """Build a Formatter whose ``%(asctime)s`` is fixed to Asia/Shanghai.

    ``logging.Formatter`` calls ``self.converter(record.created)``
    (default: ``time.localtime``) then ``time.strftime``. Swap the
    converter for an arithmetic UTC+8 shift so every backend log line
    (our logger + uvicorn's error/access loggers) carries Shanghai
    time on any machine.
    """
    formatter = logging.Formatter(fmt)
    formatter.converter = lambda t: time.gmtime(t + _SHANGHAI_OFFSET)  # type: ignore[assignment]
    return formatter


# Fed to uvicorn.run(log_config=...): uvicorn removes its own handlers
# and routes its log lines through this config, so ALL lines (backend,
# uvicorn.error, uvicorn.access) share the Shanghai-time formatter.
LOG_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "shanghai": {
            "()": make_log_formatter,
            "fmt": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "shanghai",
        },
    },
    "root": {
        "handlers": ["stderr"],
        "level": os.getenv("MH_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "uvicorn": {"handlers": ["stderr"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["stderr"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["stderr"], "level": "INFO", "propagate": False},
    },
}


def run() -> None:
    cfg = load_config()

    root = logging.getLogger()
    root.setLevel(os.getenv("MH_LOG_LEVEL", "INFO"))
    handler = logging.StreamHandler()
    handler.setFormatter(
        make_log_formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(handler)
    logger = logging.getLogger("mhc_desktop_backend")

    logger.info(
        "Starting %s %s on %s:%s", cfg.app_name, __version__, cfg.host, cfg.port
    )
    uvicorn.run(
        "mhc_desktop_backend.app:create_app",
        host=cfg.host,
        port=cfg.port,
        reload=os.getenv("MHC_RELOAD", "1") == "1",
        factory=True,
        log_level="info",
        log_config=LOG_CONFIG,
        loop="mhc_desktop_backend.main:_proactor_loop_factory",
    )


if __name__ == "__main__":
    run()
