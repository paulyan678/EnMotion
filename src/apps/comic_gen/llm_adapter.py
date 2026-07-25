"""New API-only chat adapter with strict model-specific credentials."""

from __future__ import annotations

import os
import logging
import uuid
from typing import Any, Dict, List, Optional

from ...utils.newapi_models import (
    CHAT,
    get_selected_model,
    resolve_model_api_key,
)

logger = logging.getLogger(__name__)


class LLMAdapter:
    """OpenAI-compatible New API chat interface.

    The selected model and its dedicated key are resolved on every call. A
    cached client is replaced whenever either the base URL or exact credential
    changes, so switching models does not require an application restart.
    """

    def __init__(self):
        self._client = None
        self._client_signature = None
        logger.info("LLM Adapter initialized with provider: New API")

    @property
    def provider(self) -> str:
        return "newapi"

    @property
    def is_configured(self) -> bool:
        try:
            self.require_configured()
            return True
        except (RuntimeError, ValueError):
            return False

    def require_configured(self, model: Optional[str] = None) -> str:
        from ...models.newapi import normalize_newapi_base_url
        from ..hybrid.provider import hybrid_mode_enabled, provider_gateway_base_url
        from ..hybrid.provider import provider_gateway_token

        target_model = model or get_selected_model(CHAT)
        if hybrid_mode_enabled():
            provider_gateway_token()
            provider_gateway_base_url()
        else:
            resolve_model_api_key(target_model, CHAT)
            normalize_newapi_base_url(os.getenv("NEWAPI_BASE_URL"))
        return target_model

    def _get_client(self, model: str):
        from ...models.newapi import normalize_newapi_base_url
        from ..hybrid.provider import (
            hybrid_mode_enabled,
            provider_gateway_base_url,
            provider_gateway_token,
        )

        if hybrid_mode_enabled():
            api_key = provider_gateway_token()
            base_url = provider_gateway_base_url()
        else:
            api_key = resolve_model_api_key(model, CHAT)
            base_url = normalize_newapi_base_url(os.getenv("NEWAPI_BASE_URL"))
        signature = (model, api_key, base_url)
        if self._client is None or signature != self._client_signature:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The OpenAI-compatible client package is not installed"
                ) from exc
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            self._client_signature = signature
        return self._client

    def _get_default_model(self) -> str:
        return get_selected_model(CHAT)

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        target_model = model or self._get_default_model()
        self.require_configured(target_model)
        # _get_client validates the model category and resolves only that
        # model's dedicated key before any network request is made.
        client = self._get_client(target_model)
        return self._chat_once(client, target_model, messages, response_format)

    def _chat_once(
        self,
        client,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, str]],
    ) -> str:
        kwargs: Dict[str, Any] = {"model": model, "messages": messages}
        if response_format:
            kwargs["response_format"] = response_format

        try:
            kwargs["extra_headers"] = {"Idempotency-Key": uuid.uuid4().hex}
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as exc:
            # Upstream client exception text can contain request metadata. Log
            # only the exception class and never echo the provider's text back
            # into API responses or chained tracebacks.
            logger.warning("New API chat request failed (%s)", type(exc).__name__)
            raise RuntimeError("New API chat request failed") from None
