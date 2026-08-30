"""Protocol value-objects — shared between the kernel and any deploy.

These dataclasses are the data shapes every Protocol returns. They
live in the kernel because they are part of the public contract;
deploy's concrete stores (and any enterprise replacement a customer
writes) both produce / consume them.

Moved out of the original ``storage/*_store.py`` modules when those
moved to ``mhc-desktop-deploy`` as concrete implementations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Session:
    """A chat session record.

    Persisted as a single JSON object per session in the file-backed
    reference impl. The schema is intentionally simple — the chat
    router reads ``messages`` back as a list of plain dicts and the
    UI only renders ``title`` / ``provider`` / ``model`` on the
    sidebar, with ``id`` and ``updated_at`` as the ordering keys.
    """

    id: str
    title: str = "New chat"
    messages: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Compact representation for the sidebar list."""
        return {
            "id": self.id,
            "title": self.title,
            "provider": self.provider,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Provider:
    """LLM provider configuration record.

    Schema is byte-for-byte compatible with mh-local's
    ``providers.json`` so a user can copy the file between the two
    apps. Field names follow the same shape as
    ``mh_gateway.llm.LLMProviderConfig`` even though we do not import
    that class (we deliberately stay independent of mh-gateway).
    """

    name: str
    provider_type: str = "openai"
    api_key: str = ""
    base_url: str = ""
    default_model: str = ""
    description: str = ""
    max_context_default: int = 0
    models: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    model_params: dict[str, Any] = field(default_factory=dict)
    # Static per-provider HTTP headers merged into every outbound LLM
    # request (e.g. a cost-center tag). Dynamic per-user headers come
    # from ``create_app(llm_extra_headers_provider=...)`` and win on
    # conflicts. Opaque to the kernel — forwarded verbatim.
    headers: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provider:
        d = dict(data)
        d.setdefault("provider_type", "openai")
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.__dataclass_fields__.values():
            out[f.name] = getattr(self, f.name)
        return out

    def public_dict(self) -> dict[str, Any]:
        """Same as :meth:`to_dict` but masks ``api_key`` to last 4 chars."""
        out = self.to_dict()
        if self.api_key:
            out["api_key"] = (
                "***" + self.api_key[-4:] if len(self.api_key) > 4 else "***"
            )
        return out


__all__ = ["Provider", "Session"]
