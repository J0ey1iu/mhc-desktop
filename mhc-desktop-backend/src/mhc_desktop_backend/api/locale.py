"""Locale parsing for mhc-desktop-backend.

Mirrors the helper in mh-gateway but defaults to ``en`` to match
the frontend's behaviour: ``i18n.ts`` also defaults to English
unless ``navigator.language`` starts with ``zh``, so a Chinese
user without the locale saved still gets English unless their
browser asks for it.

The function only needs to recognise the two locales the
renderer ships with — anything else falls back to the
default. Adding a third locale means adding it to the
``Locale`` Literal in both this module and
``packages/mhc-desktop-frontend/src/i18n.ts``.
"""

from __future__ import annotations


def parse_locale(accept_language: str | None = None) -> str:
    """Map an ``Accept-Language`` header (or ``None``) to ``"zh"`` or ``"en"``.

    Header parsing follows RFC 7231 loosely: we take the first
    comma-separated tag, strip any ``;q=...`` quality value,
    lower-case it, and check the prefix. ``zh-CN``, ``zh-TW``,
    ``zh-Hans`` all collapse to ``"zh"``; everything else that's
    not explicitly ``"en"`` falls back to the default.
    """
    if accept_language:
        first = accept_language.split(",")[0].split(";")[0].strip().lower()
        if first.startswith("zh"):
            return "zh"
        if first.startswith("en"):
            return "en"
    return "en"
