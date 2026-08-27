"""Default onboarding card catalogue.

The first-run tour cards live in a kernel module (here) so the
deploy package can either ship the same defaults or replace the
list via ``create_app(onboarding_cards=...)``. The card *schema*
(:class:`OnboardingCard`) stays in the kernel; the *content* is
a deploy concern that defaults to the kernel's reference set so
out-of-the-box dev / packaged installs work.

See :mod:`mhc_desktop_backend.api.onboarding` for the router that
serves these.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class OnboardingCard(BaseModel):
    # Stable id so the renderer can key transitions and so a future
    # backend-side analytics event ("saw-card-3") survives renumbering.
    id: str
    type: str  # one of OnboardingCardType

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
    media_kind: str = "none"  # one of MediaKind
    media_color: Optional[str] = None
    media_label: Optional[str] = None
    media_image: Optional[str] = None


# Hard-coded product copy. Each entry owns a slug and the
# fallback English copy; the Chinese copy lives in the same
# record so we never drift across locales. ``{brand_name}`` in
# title strings is substituted at render time from
# ``app.state.meta["brand"]["name"]``; strings without the
# placeholder pass through untouched.
DEFAULT_ONBOARDING_CARDS: list[OnboardingCard] = [
    OnboardingCard(
        id="welcome",
        type="centered",
        # ``title`` / ``body`` are the locale-resolved strings the
        # endpoint renders when the client doesn't ship its own
        # i18n. We default them to the English entry here; the
        # router rebuilds them per request from ``title_i18n`` /
        # ``body_i18n`` so the wire payload always carries the
        # right locale. Pydantic requires both fields so we keep
        # a sensible default rather than an empty string.
        title="Welcome to {brand_name}",
        body=(
            "A focused agent workspace. Pick a model, drop in skills, "
            "connect tools — every message runs the same loop."
        ),
        title_i18n={
            "en": "Welcome to {brand_name}",
            "zh": "欢迎使用 {brand_name}",
        },
        body_i18n={
            "en": (
                "A focused agent workspace. Pick a model, drop in skills, "
                "connect tools — every message runs the same loop."
            ),
            "zh": (
                "一个专注的 Agent 工作台：选模型、加技能、接工具，"
                "每条消息都走同一套循环。"
            ),
        },
        media_kind="none",
    ),
    OnboardingCard(
        id="skills",
        type="media-text",
        title="Skills ride along",
        body=(
            "Drop a SKILL.md folder or a .skill.zip into the Skills "
            "page and toggle the ones you want. They attach to your "
            "next message and stay attached for the whole run."
        ),
        title_i18n={
            "en": "Skills ride along",
            "zh": "技能随消息绑定",
        },
        body_i18n={
            "en": (
                "Drop a SKILL.md folder or a .skill.zip into the Skills "
                "page and toggle the ones you want. They attach to your "
                "next message and stay attached for the whole run."
            ),
            "zh": (
                "把 SKILL.md 文件夹或 .skill.zip 导入到「技能」页面，"
                "勾选后即可随下一条消息一起发送，并在整个会话期间持续生效。"
            ),
        },
        media_kind="image",
        media_image="/onboarding/skills.svg",
        media_color="#5b8def",
        media_label="SKILL",
    ),
    OnboardingCard(
        id="mcp",
        type="media-top",
        title="Connect tools via MCP",
        body=(
            "Plug in a Model Context Protocol server and the model "
            "can call its tools mid-conversation. Configure the spawn "
            "vector (command + args) on the MCP page and refresh to "
            "discover what each server exposes."
        ),
        title_i18n={
            "en": "Connect tools via MCP",
            "zh": "用 MCP 连接工具",
        },
        body_i18n={
            "en": (
                "Plug in a Model Context Protocol server and the model "
                "can call its tools mid-conversation. Configure the spawn "
                "vector (command + args) on the MCP page and refresh to "
                "discover what each server exposes."
            ),
            "zh": (
                "接入一个 MCP（Model Context Protocol）服务器，"
                "模型就能在对话中调用它的工具。在「MCP」页面配置启动命令与参数，"
                "然后点击「刷新」即可看到每个服务器对外暴露的工具列表。"
            ),
        },
        media_kind="image",
        media_image="/onboarding/mcp.svg",
        media_color="#22c55e",
        media_label="MCP",
    ),
]


__all__ = [
    "DEFAULT_ONBOARDING_CARDS",
    "OnboardingCard",
]
