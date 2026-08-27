"""First-run onboarding cards.

The renderer asks for ``GET /api/v1/onboarding`` on every cold start
and decides whether to show the overlay based on a localStorage
flag, so the backend stays stateless.

Card content is **deploy-provided**. The kernel ships a default
three-card catalogue in :mod:`mhc_desktop_backend.onboarding`; the
deploy either lets that ride or passes its own list to
``create_app(onboarding_cards=...)``. The card schema lives here in
the kernel; the content is the deploy's.

## Internationalisation

Each card stores ``title_i18n`` and ``body_i18n`` as
``{"en": ..., "zh": ...}`` dicts. The endpoint resolves the
locale from the ``Accept-Language`` header (``parse_locale``)
and returns:

* ``title`` / ``body`` — the value picked for the requester's
  locale, used by clients that don't ship their own i18n.
* ``title_i18n`` / ``body_i18n`` — the full dict, so a client
  that switches locales at runtime can re-render without a
  re-fetch (the frontend does this — it picks from
  ``title_i18n[currentLocale]`` reactively).

A missing locale falls back to the English entry; an empty
dict falls back to the raw title field.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from mhc_desktop_backend.api.locale import parse_locale

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


class OnboardingCard(BaseModel):
    # Stable id so the renderer can key transitions and so a future
    # backend-side analytics event ("saw-card-3") survives renumbering.
    id: str
    type: str

    # The locale-resolved strings. These match the requester's
    # Accept-Language at fetch time; clients that switch locale
    # at runtime should re-render from the *_i18n dicts below.
    title: str
    body: str

    # Full i18n dicts. Keys are ``"en"`` / ``"zh"`` for now.
    title_i18n: dict[str, str]
    body_i18n: dict[str, str]

    # Media. ``media_kind == "none"`` for ``centered`` cards;
    # ``media-color`` or ``media-image`` for the other two.
    media_kind: str = "none"
    media_color: Optional[str] = None
    media_label: Optional[str] = None
    media_image: Optional[str] = None


def _resolve(texts: dict[str, str], locale: str) -> str:
    """Pick the right locale or fall back to English.

    Fallback chain: requested locale → English → first entry →
    empty string. The endpoint never raises on a missing key
    because card copy is product copy and we should never ship
    a broken overlay due to a typo in a dict.
    """
    if locale in texts:
        return texts[locale]
    if "en" in texts:
        return texts["en"]
    if texts:
        return next(iter(texts.values()))
    return ""


def _build_cards(cards: list, locale: str, brand_name: str) -> list[OnboardingCard]:
    # ``title_i18n`` may contain ``{brand_name}``; ``str.replace`` is a
    # no-op on strings without the placeholder and silently leaves
    # typos like ``{BrandName}`` literal — no KeyError risk.
    out: list[OnboardingCard] = []
    sub = brand_name
    for raw in cards:
        out.append(
            OnboardingCard(
                id=raw.id,
                type=raw.type,
                title=_resolve(raw.title_i18n, locale).replace("{brand_name}", sub),
                body=_resolve(raw.body_i18n, locale).replace("{brand_name}", sub),
                title_i18n={
                    k: v.replace("{brand_name}", sub)
                    for k, v in raw.title_i18n.items()
                },
                body_i18n={
                    k: v.replace("{brand_name}", sub)
                    for k, v in raw.body_i18n.items()
                },
                media_kind=getattr(raw, "media_kind", "none") or "none",
                media_color=getattr(raw, "media_color", None),
                media_label=getattr(raw, "media_label", None),
                media_image=getattr(raw, "media_image", None),
            )
        )
    return out


@router.get("", response_model=list[OnboardingCard])
async def list_onboarding(
    request: Request,
    accept_language: str | None = Header(None, alias="Accept-Language"),
) -> list[OnboardingCard]:
    """Return the cards to show on first run.

    Card list comes from ``app.state.onboarding_cards`` (set by
    ``create_app(onboarding_cards=...)``; the kernel ships a default
    set in :mod:`mhc_desktop_backend.onboarding`). ``Accept-Language``
    drives the resolved ``title`` / ``body`` fields. The full
    ``*_i18n`` dicts are always returned so the client can
    re-render when the user flips the locale in Settings without a
    round trip.
    """
    cards = getattr(request.app.state, "onboarding_cards", None) or []
    locale = parse_locale(accept_language)
    brand_name = (
        (getattr(request.app.state, "meta", {}) or {}).get("brand", {}) or {}
    ).get("name") or "mhc-desktop-backend"
    return _build_cards(cards, locale, brand_name)
