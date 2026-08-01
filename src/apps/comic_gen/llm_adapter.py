"""New API-only chat adapter with strict model-specific credentials."""

from __future__ import annotations

import os
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from ...utils.newapi_models import (
    CHAT,
    get_selected_model,
    resolve_model_api_key,
)

logger = logging.getLogger(__name__)

_MANAGED_SAFE_RETRY_CODES = frozenset(
    {
        "provider_connection_failed",
        "provider_rate_limited",
        "provider_concurrency_limited",
    }
)
_MANAGED_CHAT_MAX_ATTEMPTS = 3


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
            # The control plane owns the only safe provider-submission retry
            # policy. A provider 5xx has an ambiguous billing outcome and the
            # gateway intentionally answers a same-key replay with 202 instead
            # of submitting again. The OpenAI SDK's automatic 5xx retry would
            # misparse that 202 as a chat completion, so keep SDK retries off.
            self._client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=0,
            )
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
        from ...models.newapi import NewAPIProviderError
        from ..hybrid.provider import hybrid_mode_enabled

        attempts = _MANAGED_CHAT_MAX_ATTEMPTS if hybrid_mode_enabled() else 1
        for attempt in range(attempts):
            try:
                return self._chat_once(client, target_model, messages, response_format)
            except NewAPIProviderError as exc:
                retryable = exc.error_code in _MANAGED_SAFE_RETRY_CODES
                if not retryable or attempt == attempts - 1:
                    raise
                # These managed-gateway failures are explicit pre-acceptance
                # rejections with refunded reservations. A fresh idempotency
                # key is therefore safe; ambiguous outcomes are never retried.
                logger.warning(
                    "Retrying safely rejected managed chat request code=%s attempt=%d/%d",
                    exc.error_code,
                    attempt + 1,
                    attempts,
                )
                time.sleep(min(2**attempt, 5))
        raise RuntimeError("New API chat request failed")

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
            from ...models.newapi import (
                NewAPIProviderError,
                _classified_provider_error,
                _extract_provider_error,
            )

            status_code = getattr(exc, "status_code", None)
            body = getattr(exc, "body", None)
            if isinstance(status_code, int) and isinstance(body, (dict, list)):
                code, message = _extract_provider_error(body)
                classified = _classified_provider_error(
                    code,
                    message,
                    http_status=status_code,
                    request_id=str(getattr(exc, "request_id", "") or ""),
                    phase="chat completion",
                )
                if isinstance(classified, NewAPIProviderError):
                    logger.warning(
                        "New API chat request failed with classified code=%s HTTP=%s",
                        classified.error_code,
                        status_code,
                    )
                    raise classified from None
            # Upstream client exception text can contain request metadata. Log
            # only the exception class and never echo the provider's text back
            # into API responses or chained tracebacks.
            logger.warning("New API chat request failed (%s)", type(exc).__name__)
            raise RuntimeError("New API chat request failed") from None
