"""Build a streaming-capable LLM provider from a :class:`Provider`.

We intentionally bypass :mod:`mh_gateway.llm` (the project is
deliberately independent of mh-gateway). Instead we construct the
SDK's :class:`OpenAILLMProvider` / :class:`AnthropicLLMProvider`
directly with the right client.

For OpenAI-compatible vendors (DeepSeek, Moonshot, Zhipu, Ollama,
…) we reuse the OpenAI driver — they all speak the OpenAI REST API.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from anthropic import AsyncAnthropic
from httpx import AsyncClient
from minimal_harness.llm.anthropic import AnthropicLLMProvider
from minimal_harness.llm.openai import OpenAILLMProvider
from openai import AsyncOpenAI

from mhc_desktop_backend.protocol_models import Provider

logger = logging.getLogger("mhc_desktop_backend")


def _new_http_client() -> AsyncClient:
    # trust_env=False — don't pick up the user's shell HTTP proxy for
    # loopback vendors like Ollama.
    return AsyncClient(trust_env=False)


def build_provider(
    provider: Provider,
    *,
    model_override: str = "",
    model_params: dict[str, Any] | None = None,
    extra_headers_provider: Callable[[], Awaitable[dict[str, str]]] | None = None,
) -> Any:
    """Return a streaming-capable LLM instance for *provider*.

    ``model_override`` lets the caller pin a model different from the
    provider's ``default_model`` (e.g. via the chat UI).
    ``model_params`` are extra request-body fields merged into every
    call (shipped via OpenAI's ``extra_body`` so non-standard keys like
    ``reasoning_effort`` land in the request body unchanged).

    ``extra_headers_provider`` is an async callable returning HTTP
    headers merged into every outbound LLM call (the SDK invokes it
    per request). It is composed with the provider's static
    ``headers`` field: static headers first, then the callable's
    result overwrites on conflicts — so a request-scoped identity can
    override a provider-level default.
    """
    if not provider.api_key and provider.provider_type != "openai":
        # OpenAI driver still requires api_key (any non-empty string works
        # for vendors that don't auth, like Ollama — we set it as a guard).
        raise ValueError(f"provider '{provider.name}' has no api_key configured")

    model = model_override or provider.default_model
    if not model:
        raise ValueError(
            f"provider '{provider.name}' has no default_model and no override supplied"
        )

    llm_kwargs: dict[str, Any] = {}
    if model_params:
        llm_kwargs["extra_body"] = dict(model_params)

    static_headers = dict(provider.headers or {})
    if static_headers or extra_headers_provider is not None:

        async def _headers() -> dict[str, str]:
            out = dict(static_headers)
            if extra_headers_provider is not None:
                out.update(await extra_headers_provider())
            return out

        llm_kwargs["llm_extra_headers_provider"] = _headers

    ptype = provider.provider_type
    if ptype == "openai":
        kwargs: dict[str, Any] = {
            "api_key": provider.api_key or "sk-no-key",
            "http_client": _new_http_client(),
        }
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        client = AsyncOpenAI(**kwargs)
        logger.info(
            "llm.factory.openai provider=%s model=%s base_url=%s params=%s",
            provider.name,
            model,
            provider.base_url or "(default)",
            model_params or {},
        )
        return OpenAILLMProvider(
            client=client, model=model, llm_kwargs=llm_kwargs or None
        )

    if ptype == "anthropic":
        kwargs = {"api_key": provider.api_key, "http_client": _new_http_client()}
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        client = AsyncAnthropic(**kwargs)
        logger.info(
            "llm.factory.anthropic provider=%s model=%s base_url=%s",
            provider.name,
            model,
            provider.base_url or "(default)",
        )
        return AnthropicLLMProvider(client=client, model=model)

    raise ValueError(f"unsupported provider_type: {ptype}")
