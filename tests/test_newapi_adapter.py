import base64
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests

from src.apps.comic_gen.llm_adapter import LLMAdapter
from src.models.newapi import (
    INPUT_IMAGE_PRIVACY_ERROR_CODE,
    OUTPUT_VIDEO_POLICY_ERROR_CODE,
    OUTPUT_VIDEO_POLICY_PUBLIC_MESSAGE,
    PROVIDER_ACCESS_ERROR_CODE,
    PROVIDER_AUTH_ERROR_CODE,
    PROVIDER_CONCURRENCY_ERROR_CODE,
    PROVIDER_CONNECTION_ERROR_CODE,
    PROVIDER_CONNECTION_PUBLIC_MESSAGE,
    PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
    PROVIDER_PAYLOAD_TOO_LARGE_ERROR_CODE,
    PROVIDER_QUOTA_ERROR_CODE,
    PROVIDER_RATE_LIMIT_ERROR_CODE,
    PROVIDER_REQUEST_ERROR_CODE,
    RATE_CARD_MISSING_ERROR_CODE,
    RATE_CARD_MISSING_PUBLIC_MESSAGE,
    NewAPIImageModel,
    NewAPIProviderError,
    NewAPIVideoModel,
    _extract_provider_progress,
    _extract_video_status,
    _extract_video_url,
    _media_input,
    _request,
    newapi_image_timeout_seconds,
    normalize_newapi_base_url,
    normalize_newapi_image_size,
)


class FakeResponse:
    def __init__(self, payload=None, *, status=200, body=b"", headers=None):
        self._payload = payload
        self.status_code = status
        self.content = body
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=65536):
        del chunk_size
        if self.content:
            yield self.content


class TestNewAPIBaseUrl:
    def test_adds_v1_once(self):
        assert normalize_newapi_base_url("https://gateway.example") == "https://gateway.example/v1"
        assert (
            normalize_newapi_base_url("https://gateway.example/v1/") == "https://gateway.example/v1"
        )

    def test_rejects_plain_http_for_remote_host(self):
        with pytest.raises(ValueError, match="HTTPS"):
            normalize_newapi_base_url("http://gateway.example")

    def test_allows_loopback_http(self):
        assert normalize_newapi_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080/v1"

    def test_requires_explicit_base_url(self):
        with pytest.raises(RuntimeError, match="必须配置"):
            normalize_newapi_base_url(None)

    def test_maps_legacy_image_sizes_and_rejects_unknown(self):
        assert normalize_newapi_image_size("576*1024") == "1024x1536"
        assert normalize_newapi_image_size("1024*576") == "1536x1024"
        with pytest.raises(ValueError, match="尺寸必须"):
            normalize_newapi_image_size("2048x2048")

    def test_image_timeout_defaults_to_fifteen_minutes_and_is_bounded(self, monkeypatch):
        monkeypatch.delenv("NEWAPI_IMAGE_TIMEOUT_SECONDS", raising=False)
        assert newapi_image_timeout_seconds() == 960
        assert newapi_image_timeout_seconds("60") == 60
        with pytest.raises(ValueError, match="60 到 1800"):
            newapi_image_timeout_seconds("59")

    def test_extracts_only_real_provider_progress_values(self):
        assert _extract_provider_progress({"progress": "42%"}) == 42
        assert _extract_provider_progress({"data": {"percentage": 0.73}}) == 73
        assert _extract_provider_progress({"progress": "unknown"}) is None
        assert _extract_provider_progress({"progress": 101}) is None


class TestNewAPIChatAdapter:
    def test_does_not_echo_upstream_exception_text(self):
        credential_like_text = "Bearer should-never-appear"

        class BrokenCompletions:
            def create(self, **kwargs):
                del kwargs
                raise RuntimeError(credential_like_text)

        client = SimpleNamespace(chat=SimpleNamespace(completions=BrokenCompletions()))
        with pytest.raises(RuntimeError, match="New API chat request failed") as exc_info:
            LLMAdapter()._chat_once(
                client,
                "deepseek-v4-flash",
                [{"role": "user", "content": "hello"}],
                None,
            )
        assert credential_like_text not in str(exc_info.value)

    def test_preserves_ambiguous_gateway_failure_without_sdk_replay(self):
        class AmbiguousGatewayError(RuntimeError):
            status_code = 502
            request_id = "gateway-request-123"
            body = {
                "code": "provider_outcome_ambiguous",
                "detail": "credits remain reserved",
            }

        class BrokenCompletions:
            def create(self, **kwargs):
                del kwargs
                raise AmbiguousGatewayError("upstream details must stay hidden")

        client = SimpleNamespace(chat=SimpleNamespace(completions=BrokenCompletions()))

        with pytest.raises(NewAPIProviderError) as exc_info:
            LLMAdapter()._chat_once(
                client,
                "qwen3.7-max",
                [{"role": "user", "content": "hello"}],
                {"type": "json_object"},
            )

        assert exc_info.value.error_code == PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE
        assert exc_info.value.http_status == 502
        assert "upstream details" not in str(exc_info.value)

    def test_managed_chat_retries_refunded_connection_failure_with_a_fresh_key(
        self,
        monkeypatch,
    ):
        keys = []

        class ConnectionFailure(RuntimeError):
            status_code = 502
            request_id = "gateway-request-connection"
            body = {
                "code": "provider_connection_failed",
                "detail": "provider connection failed before acceptance",
            }

        class FlakyCompletions:
            def create(self, **kwargs):
                keys.append(kwargs["extra_headers"]["Idempotency-Key"])
                if len(keys) == 1:
                    raise ConnectionFailure("hidden upstream details")
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="recovered"))]
                )

        adapter = LLMAdapter()
        client = SimpleNamespace(chat=SimpleNamespace(completions=FlakyCompletions()))
        monkeypatch.setattr(adapter, "require_configured", lambda _model: None)
        monkeypatch.setattr(adapter, "_get_client", lambda _model: client)
        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.apps.comic_gen.llm_adapter.time.sleep", lambda _delay: None)

        assert (
            adapter.chat(
                [{"role": "user", "content": "hello"}],
                model="deepseek-v4-flash",
            )
            == "recovered"
        )
        assert len(keys) == 2
        assert keys[0] != keys[1]

    def test_managed_chat_does_not_retry_gateway_exhausted_connection_failure(
        self,
        monkeypatch,
    ):
        calls = []

        class ExhaustedResponse(SimpleNamespace):
            def __bool__(self):
                return False

        class ConnectionFailure(RuntimeError):
            status_code = 502
            request_id = "gateway-request-exhausted"
            body = {
                "code": "provider_connection_failed",
                "detail": "provider connection failed before acceptance",
            }
            response = ExhaustedResponse(headers={"x-enmotion-provider-retry-exhausted": "true"})

        class BrokenCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                raise ConnectionFailure("hidden upstream details")

        adapter = LLMAdapter()
        client = SimpleNamespace(chat=SimpleNamespace(completions=BrokenCompletions()))
        monkeypatch.setattr(adapter, "require_configured", lambda _model: None)
        monkeypatch.setattr(adapter, "_get_client", lambda _model: client)
        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)

        with pytest.raises(NewAPIProviderError) as exc_info:
            adapter.chat(
                [{"role": "user", "content": "hello"}],
                model="deepseek-v4-flash",
            )
        assert exc_info.value.error_code == PROVIDER_CONNECTION_ERROR_CODE
        assert exc_info.value.retry_exhausted is True
        assert len(calls) == 1

    def test_managed_chat_never_retries_an_ambiguous_outcome(self, monkeypatch):
        calls = 0

        class AmbiguousFailure(RuntimeError):
            status_code = 502
            request_id = "gateway-request-ambiguous"
            body = {
                "code": "pending_reconciliation",
                "detail": "credits remain reserved",
            }

        class AmbiguousCompletions:
            def create(self, **kwargs):
                nonlocal calls
                del kwargs
                calls += 1
                raise AmbiguousFailure("hidden upstream details")

        adapter = LLMAdapter()
        client = SimpleNamespace(chat=SimpleNamespace(completions=AmbiguousCompletions()))
        monkeypatch.setattr(adapter, "require_configured", lambda _model: None)
        monkeypatch.setattr(adapter, "_get_client", lambda _model: client)
        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)

        with pytest.raises(NewAPIProviderError) as exc_info:
            adapter.chat(
                [{"role": "user", "content": "hello"}],
                model="qwen3.7-max",
            )

        assert exc_info.value.error_code == PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE
        assert calls == 1


class TestNewAPIImageModel:
    @pytest.mark.parametrize(
        ("status", "payload", "expected_code"),
        [
            (401, {"code": "provider_authentication_failed"}, PROVIDER_AUTH_ERROR_CODE),
            (402, {"code": "provider_quota_exhausted"}, PROVIDER_QUOTA_ERROR_CODE),
            (403, {"code": "provider_access_denied"}, PROVIDER_ACCESS_ERROR_CODE),
            (413, {"code": "provider_payload_too_large"}, PROVIDER_PAYLOAD_TOO_LARGE_ERROR_CODE),
            (429, {"code": "provider_rate_limited"}, PROVIDER_RATE_LIMIT_ERROR_CODE),
            (422, {"code": "provider_rejected"}, PROVIDER_REQUEST_ERROR_CODE),
            (
                502,
                {"code": "provider_outcome_ambiguous", "detail": "credits remain reserved"},
                PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE,
            ),
        ],
    )
    def test_gateway_failure_classes_are_preserved(
        self,
        monkeypatch,
        tmp_path,
        status,
        payload,
        expected_code,
    ):
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(payload, status=status),
        )

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIImageModel({}).generate(
                "draw a fox",
                str(tmp_path / "result.png"),
                model_id="gpt-image-2",
            )

        assert exc_info.value.error_code == expected_code

    def test_managed_submission_retries_only_pre_connection_failures_with_one_key(
        self,
        monkeypatch,
    ):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if len(calls) <= 2:
                raise requests.ConnectTimeout("connect timed out")
            return FakeResponse({"ok": True})

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        response = _request("POST", "https://control.test/images/generations", json={})

        assert response.status_code == 200
        assert len(calls) == 3
        assert len({call[2]["headers"]["Idempotency-Key"] for call in calls}) == 1

    @pytest.mark.parametrize("phase", ["image submission", "video submission"])
    def test_managed_submission_retries_refunded_connection_failure_with_fresh_key(
        self,
        monkeypatch,
        phase,
    ):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs["headers"]["Idempotency-Key"]))
            if len(calls) == 1:
                return FakeResponse(
                    {"detail": "provider connection failed"},
                    status=502,
                )
            return FakeResponse({"ok": True})

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        response = _request(
            "POST",
            "https://control.test/provider/submit",
            phase=phase,
            json={},
        )

        assert response.status_code == 200
        assert len(calls) == 2
        assert calls[0][2] != calls[1][2]

    def test_managed_image_recovers_interrupted_response_with_cached_replay(
        self,
        monkeypatch,
    ):
        calls = []
        responses = iter(
            [
                requests.ReadTimeout("response stalled"),
                FakeResponse(
                    {
                        "idempotent_replay": True,
                        "usage_request": {"status": "reserved", "error_code": None},
                    },
                    status=202,
                ),
                FakeResponse({"data": [{"b64_json": "aW1hZ2U="}]}),
            ]
        )

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        response = _request(
            "POST",
            "https://control.test/images/generations",
            phase="image submission",
            timeout=960,
        )

        assert response.status_code == 200
        assert len(calls) == 3
        assert len({call[2]["headers"]["Idempotency-Key"] for call in calls}) == 1
        assert all(call[2]["headers"]["Accept-Encoding"] == "identity" for call in calls)
        assert calls[0][2]["timeout"] == 960
        assert calls[1][2]["timeout"] == 60
        assert calls[2][2]["timeout"] == 60

    def test_managed_image_rewinds_multipart_files_for_cached_recovery(
        self,
        monkeypatch,
    ):
        reference = io.BytesIO(b"reference-image")
        calls = []

        def fake_request(method, url, **kwargs):
            payload = kwargs["files"][0][1][1].read()
            calls.append((method, url, kwargs, payload))
            if len(calls) == 1:
                raise requests.exceptions.ChunkedEncodingError("response ended early")
            return FakeResponse({"data": [{"b64_json": "aW1hZ2U="}]})

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        response = _request(
            "POST",
            "https://control.test/images/edits",
            phase="image submission",
            timeout=960,
            files=[("image[]", ("reference.png", reference, "image/png"))],
        )

        assert response.status_code == 200
        assert [call[3] for call in calls] == [b"reference-image", b"reference-image"]
        assert (
            calls[0][2]["headers"]["Idempotency-Key"] == calls[1][2]["headers"]["Idempotency-Key"]
        )

    def test_managed_image_refreshes_expired_session_during_cached_recovery(
        self,
        monkeypatch,
    ):
        calls = []
        responses = iter(
            [
                requests.ReadTimeout("response stalled"),
                FakeResponse({"detail": "invalid or expired access token"}, status=401),
                FakeResponse({"data": [{"b64_json": "aW1hZ2U="}]}),
            ]
        )

        def fake_request(method, url, **kwargs):
            calls.append(
                {
                    "method": method,
                    "url": url,
                    "authorization": kwargs["headers"]["Authorization"],
                    "idempotency_key": kwargs["headers"]["Idempotency-Key"],
                }
            )
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr(
            "src.apps.hybrid.provider.refresh_provider_gateway_token",
            lambda: "fresh-access-token",
        )
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        response = _request(
            "POST",
            "https://control.test/images/edits",
            phase="image submission",
            headers={"Authorization": "Bearer expired-access-token"},
            timeout=960,
        )

        assert response.status_code == 200
        assert [call["authorization"] for call in calls] == [
            "Bearer expired-access-token",
            "Bearer expired-access-token",
            "Bearer fresh-access-token",
        ]
        assert len({call["idempotency_key"] for call in calls}) == 1

    def test_managed_provider_authentication_failure_is_not_session_refreshed(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr(
            "src.apps.hybrid.provider.refresh_provider_gateway_token",
            lambda: pytest.fail("upstream provider 401 must not refresh employee session"),
        )
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {"code": "provider_authentication_failed"},
                status=401,
            ),
        )

        with pytest.raises(NewAPIProviderError) as exc_info:
            _request(
                "POST",
                "https://control.test/images/generations",
                phase="image submission",
                headers={"Authorization": "Bearer current-access-token"},
            )

        assert exc_info.value.error_code == PROVIDER_AUTH_ERROR_CODE

    def test_managed_image_recovery_exhaustion_is_typed_ambiguous(
        self,
        monkeypatch,
    ):
        calls = []

        def fail_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            raise requests.ReadTimeout("response stalled")

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi.requests.request", fail_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        with pytest.raises(NewAPIProviderError) as exc_info:
            _request(
                "POST",
                "https://control.test/images/generations",
                phase="image submission",
                timeout=960,
            )

        assert exc_info.value.error_code == PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE
        assert len(calls) == 4
        assert len({call[2]["headers"]["Idempotency-Key"] for call in calls}) == 1

    def test_refunded_replay_and_explicit_refunded_502_rotate_keys(
        self,
        monkeypatch,
    ):
        calls = []
        responses = iter(
            [
                requests.ConnectTimeout("connect timed out"),
                FakeResponse(
                    {
                        "idempotent_replay": True,
                        "usage_request": {
                            "status": "refunded",
                            "error_code": "provider_connect_failed",
                        },
                    },
                    status=202,
                ),
                FakeResponse({"ok": True}),
            ]
        )

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs["headers"]["Idempotency-Key"]))
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        assert _request("POST", "https://control.test/images/generations").status_code == 200
        assert calls[0][2] == calls[1][2]
        assert calls[2][2] != calls[1][2]

        calls.clear()
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda method, url, **kwargs: (
                calls.append((method, url, kwargs["headers"]["Idempotency-Key"]))
                or FakeResponse({"detail": "provider connection failed"}, status=502)
            ),
        )
        with pytest.raises(NewAPIProviderError) as exc_info:
            _request("POST", "https://control.test/images/generations")
        assert exc_info.value.error_code == PROVIDER_CONNECTION_ERROR_CODE
        assert len(calls) == 4
        assert len({call[2] for call in calls}) == 4

    def test_gateway_exhausted_submission_is_not_retried_by_desktop(
        self,
        monkeypatch,
    ):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs["headers"]["Idempotency-Key"]))
            return FakeResponse(
                {"detail": "provider connection failed"},
                status=502,
                headers={"X-EnMotion-Provider-Retry-Exhausted": "true"},
            )

        monkeypatch.setattr("src.apps.hybrid.provider.hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)

        with pytest.raises(NewAPIProviderError) as exc_info:
            _request(
                "POST",
                "https://control.test/images/generations",
                phase="image submission",
            )
        assert exc_info.value.error_code == PROVIDER_CONNECTION_ERROR_CODE
        assert exc_info.value.retry_exhausted is True
        assert len(calls) == 1

    def test_gateway_connection_failure_has_an_actionable_public_error(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {"detail": "provider connection failed"},
                status=502,
            ),
        )

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIImageModel({}).generate(
                "draw a fox",
                str(tmp_path / "result.png"),
                model_id="gpt-image-2",
            )

        assert exc_info.value.error_code == PROVIDER_CONNECTION_ERROR_CODE
        assert str(exc_info.value) == PROVIDER_CONNECTION_PUBLIC_MESSAGE
        assert "HTTP 状态：502" in exc_info.value.diagnostic
        assert "provider connection failed" not in str(exc_info.value)

    def test_missing_rate_card_has_an_actionable_public_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {"detail": "no active rate card for images.generations/gpt-image-2"},
                status=503,
            ),
        )

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIImageModel({}).generate(
                "draw a fox",
                str(tmp_path / "result.png"),
                model_id="gpt-image-2",
            )

        assert exc_info.value.error_code == RATE_CARD_MISSING_ERROR_CODE
        assert str(exc_info.value) == RATE_CARD_MISSING_PUBLIC_MESSAGE
        assert "no active rate card" not in str(exc_info.value)
        assert "阶段：提交图像任务" in exc_info.value.diagnostic
        assert "提交视频任务" not in exc_info.value.diagnostic

    def test_provider_error_redacts_configured_model_key(self, monkeypatch, tmp_path):
        configured_key = "image-test-token"

        def fake_request(method, url, **kwargs):
            del method, url, kwargs
            return FakeResponse(
                {"error": {"message": f"credential {configured_key} rejected"}},
                status=401,
            )

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", configured_key)
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)

        with pytest.raises(RuntimeError) as exc_info:
            NewAPIImageModel({}).generate(
                "draw a fox",
                str(tmp_path / "result.png"),
                model_id="gpt-image-2",
            )
        assert configured_key not in str(exc_info.value)
        assert isinstance(exc_info.value, NewAPIProviderError)
        assert exc_info.value.error_code == PROVIDER_AUTH_ERROR_CODE

    def test_saves_base64_image(self, monkeypatch, tmp_path):
        requests_seen = []

        def fake_request(method, url, **kwargs):
            requests_seen.append((method, url, kwargs))
            return FakeResponse({"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]})

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
        monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "gpt-image-2")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)

        output = tmp_path / "result.png"
        path, _ = NewAPIImageModel({}).generate("draw a fox", str(output), size="1024*1024")

        assert path == str(output)
        assert output.read_bytes() == b"image-bytes"
        method, url, kwargs = requests_seen[0]
        assert method == "POST"
        assert url == "https://gateway.example/v1/images/generations"
        assert kwargs["json"]["model"] == "gpt-image-2"
        assert kwargs["json"]["size"] == "1024x1024"
        assert kwargs["headers"]["Authorization"] == "Bearer image-test-token"

    def test_image_edit_uses_multipart_image_array(self, monkeypatch, tmp_path):
        reference = tmp_path / "ref.png"
        reference.write_bytes(b"reference")
        output = tmp_path / "edited.png"
        requests_seen = []

        def fake_request(method, url, **kwargs):
            requests_seen.append((method, url, kwargs))
            return FakeResponse({"data": [{"b64_json": base64.b64encode(b"edited").decode()}]})

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example")
        monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
        monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "gpt-image-2")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)

        NewAPIImageModel({}).generate(
            "edit it",
            str(output),
            ref_image_paths=[str(reference)],
        )

        assert output.read_bytes() == b"edited"
        method, url, kwargs = requests_seen[0]
        assert method == "POST"
        assert url.endswith("/v1/images/edits")
        assert kwargs["files"][0][0] == "image[]"


class TestNewAPIVideoModel:
    def test_http_safety_rejection_has_safe_message_and_bounded_diagnostics(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {
                    "error": {
                        "code": "InputImageSensitiveContentDetected.PrivacyInformation",
                        "message": "The input image may contain a real person",
                    }
                },
                status=400,
                headers={"x-request-id": "privacy-request-1"},
            ),
        )

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIVideoModel({}).generate(
                "animate this fictional character",
                str(tmp_path / "rejected.mp4"),
                img_url="https://cdn.example/character.png",
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="i2v",
            )

        error = exc_info.value
        assert error.error_code == INPUT_IMAGE_PRIVACY_ERROR_CODE
        assert "真人形象" in str(error)
        assert "InputImageSensitiveContentDetected" not in str(error)
        assert "HTTP 状态：400" in error.diagnostic
        assert "real person" not in error.diagnostic
        assert "privacy-request-1" in error.diagnostic

    def test_serialized_relay_safety_error_uses_inner_code_and_request_id(
        self, monkeypatch, tmp_path
    ):
        upstream = {
            "error": {
                "code": "InputImageSensitiveContentDetected.PrivacyInformation",
                "message": (
                    "The request failed because the input image may contain real "
                    "person. Request id: privacy-request-from-message"
                ),
                "type": "BadRequest",
            }
        }
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {
                    "code": "fail_to_fetch_task",
                    "message": json.dumps(upstream),
                    "data": None,
                },
                status=400,
            ),
        )

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIVideoModel({}).generate(
                "animate this fictional character",
                str(tmp_path / "rejected.mp4"),
                img_url="https://cdn.example/character.png",
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="i2v",
            )

        error = exc_info.value
        assert error.provider_code == ("InputImageSensitiveContentDetected.PrivacyInformation")
        assert "fail_to_fetch_task" not in error.diagnostic
        assert "privacy-request-from-message" in error.diagnostic
        assert '{"error"' not in error.diagnostic

    def test_nested_poll_safety_rejection_is_classified(self, monkeypatch, tmp_path):
        responses = iter(
            [
                FakeResponse({"task_id": "privacy-task", "status": "processing"}),
                FakeResponse(
                    {
                        "data": {
                            "status": "FAILURE",
                            "error": {
                                "code": "InputImageSensitiveContentDetected.PrivacyInformation",
                                "message": "Reference resembles a real person",
                            },
                        }
                    }
                ),
            ]
        )
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: next(responses),
        )
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _: None)

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIVideoModel({}).generate(
                "animate this fictional character",
                str(tmp_path / "rejected.mp4"),
                img_url="https://cdn.example/character.png",
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="i2v",
            )

        assert exc_info.value.error_code == INPUT_IMAGE_PRIVACY_ERROR_CODE
        assert "服务商任务 ID：privacy-task" in exc_info.value.diagnostic

    def test_nested_output_policy_rejection_is_actionable(self, monkeypatch, tmp_path):
        responses = iter(
            [
                FakeResponse({"task_id": "policy-task", "status": "processing"}),
                FakeResponse(
                    {
                        "status": "failed",
                        "error": {
                            "code": "OutputVideoSensitiveContentDetected.PolicyViolation",
                            "message": (
                                "The output video may be related to copyright restrictions. "
                                "Request id: policy-request-1"
                            ),
                        },
                    }
                ),
            ]
        )
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: next(responses),
        )
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _: None)

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIVideoModel({}).generate(
                "abstract lights drifting through space",
                str(tmp_path / "rejected.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )

        error = exc_info.value
        assert error.error_code == OUTPUT_VIDEO_POLICY_ERROR_CODE
        assert str(error) == OUTPUT_VIDEO_POLICY_PUBLIC_MESSAGE
        assert "copyright restrictions" not in str(error)
        assert "服务商任务 ID：policy-task" in error.diagnostic
        assert "policy-request-1" in error.diagnostic

    def test_nested_task_failure_wins_and_echoed_input_url_is_not_output(self):
        source_url = "https://studio.example/provider-media/signed/source.png"
        payload = {
            "status": "SUCCESS",
            "metadata": {
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": source_url},
                    }
                ]
            },
            "data": {
                "status": "FAILURE",
                "fail_reason": "provider rejected the task",
            },
        }

        assert _extract_video_status(payload) == "failure"
        assert _extract_video_url(payload, excluded_urls={source_url}) is None

    def test_completed_openai_video_metadata_url_is_output(self):
        payload = {
            "id": "task-123",
            "status": "completed",
            "metadata": {"url": "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/video.mp4"},
        }

        assert _extract_video_status(payload) == "completed"
        assert _extract_video_url(payload) == payload["metadata"]["url"]

    def test_polls_and_downloads_video(self, monkeypatch, tmp_path):
        calls = []
        responses = iter(
            [
                FakeResponse(
                    {"task_id": "task-123", "status": "processing"},
                    status=201,
                    headers={"x-request-id": "request-456"},
                ),
                FakeResponse(
                    {
                        "task_id": "task-123",
                        "status": "succeeded",
                        "url": "https://cdn.example/video.mp4",
                    }
                ),
                FakeResponse(
                    body=b"video-bytes",
                    headers={"Content-Type": "video/mp4"},
                ),
            ]
        )

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return next(responses)

        provider_ids = []
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_MODEL", "doubao-seedance-2-0-260128")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _: None)

        output = tmp_path / "result.mp4"
        path, _ = NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            duration=5,
            resolution="720p",
            aspect_ratio="16:9",
            img_url="https://cdn.example/first.png",
            on_provider_ids=lambda *args: provider_ids.append(args),
        )

        assert path == str(output)
        assert output.read_bytes() == b"video-bytes"
        assert provider_ids == [("newapi", "task-123", "request-456")]
        assert calls[0][1] == "https://gateway.example/v1/video/generations"
        submitted = calls[0][2]["json"]
        assert "image" not in submitted
        assert "duration" not in submitted
        assert submitted["metadata"] == {
            "content": [
                {"type": "text", "text": "camera pushes in"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://cdn.example/first.png"},
                    "role": "first_frame",
                },
            ],
            "duration": 5,
            "resolution": "720p",
            "ratio": "16:9",
            "generate_audio": True,
            "watermark": False,
        }
        assert "watermark" not in submitted
        assert calls[1][1].endswith("/video/generations/task-123")
        assert calls[1][2]["headers"]["Authorization"] == "Bearer video-test-token"
        assert calls[2][1] == "https://gateway.example/v1/videos/task-123/content"
        assert calls[2][2]["headers"]["Authorization"] == "Bearer video-test-token"

    def test_accepts_deeply_nested_newapi_video_result(self, monkeypatch, tmp_path):
        responses = iter(
            [
                FakeResponse({"data": {"task_id": "task-nested", "status": "PROCESSING"}}),
                requests.ConnectionError("temporary proxy failure"),
                FakeResponse(
                    {
                        "code": "success",
                        "metadata": {"content": [{"type": "text", "text": "prompt"}]},
                        "data": {
                            "task_id": "task-nested",
                            "status": "SUCCESS",
                            "data": {
                                "code": "success",
                                "data": {
                                    "status": "succeeded",
                                    "content": {"video_url": "https://cdn.example/nested.mp4"},
                                },
                            },
                        },
                    }
                ),
                FakeResponse(
                    body=b"nested-video",
                    headers={"Content-Type": "video/mp4"},
                ),
            ]
        )

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_MINI_API_KEY", "mini-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")

        def fake_request(*args, **kwargs):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _: None)

        output = tmp_path / "nested.mp4"
        path, _ = NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            img_url="https://cdn.example/first.png",
            model_id="doubao-seedance-2-0-mini-260615",
        )

        assert path == str(output)
        assert output.read_bytes() == b"nested-video"

    def test_accepts_moyu_success_url_in_fail_reason(self, monkeypatch, tmp_path):
        responses = iter(
            [
                FakeResponse(
                    {
                        "task_id": "task-moyu",
                        "data": {
                            "status": "SUCCESS",
                            "fail_reason": "https://cdn.example/moyu.mp4",
                        },
                    }
                ),
                FakeResponse(
                    body=b"moyu-video",
                    headers={"Content-Type": "video/mp4"},
                ),
            ]
        )
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: next(responses),
        )

        output = tmp_path / "moyu.mp4"
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
        )

        assert output.read_bytes() == b"moyu-video"

    def test_terminal_success_without_url_uses_authenticated_content_proxy(
        self,
        monkeypatch,
        tmp_path,
    ):
        calls = []
        responses = iter(
            [
                FakeResponse({"task_id": "task-proxy", "status": "processing"}),
                FakeResponse({"task_id": "task-proxy", "status": "completed"}),
                FakeResponse(
                    body=b"proxied-video",
                    headers={"Content-Type": "video/mp4"},
                ),
            ]
        )

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return next(responses)

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _: None)

        output = tmp_path / "proxy.mp4"
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
        )

        assert output.read_bytes() == b"proxied-video"
        assert calls[-1][1] == "https://gateway.example/v1/videos/task-proxy/content"

    @pytest.mark.parametrize(
        ("proxy_status", "proxy_content_type", "proxy_body"),
        [
            (404, "application/json", b""),
            (200, "application/json", b'{"error":"not media"}'),
            (200, "image/png", b"not-a-video"),
        ],
        ids=("missing-route", "json-200", "image-200"),
    )
    def test_content_proxy_failure_uses_direct_server_fallback(
        self,
        monkeypatch,
        tmp_path,
        proxy_status,
        proxy_content_type,
        proxy_body,
    ):
        direct_url = "https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/result.mp4"
        calls = []
        downloads = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "POST":
                return FakeResponse(
                    {
                        "task_id": "task-fallback",
                        "status": "completed",
                        "metadata": {"url": direct_url},
                    }
                )
            return FakeResponse(
                {"error": {"message": "content endpoint unavailable"}},
                status=proxy_status,
                body=proxy_body,
                headers={"Content-Type": proxy_content_type},
            )

        def fake_download(url, destination, **kwargs):
            downloads.append((url, kwargs))
            Path(destination).write_bytes(b"fallback-video")

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi._server_mode_enabled", lambda: True)
        monkeypatch.setattr("src.models.newapi._safe_output_path", lambda path: path)
        monkeypatch.setattr(
            "src.utils.media_security.download_remote_media",
            fake_download,
        )

        output = tmp_path / "fallback.mp4"
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
        )

        assert output.read_bytes() == b"fallback-video"
        assert calls[-1][1].endswith("/videos/task-fallback/content")
        assert downloads[0][0] == direct_url

    def test_content_proxy_failure_decodes_bounded_legacy_video_data_url(
        self,
        monkeypatch,
        tmp_path,
    ):
        video_data = base64.b64encode(b"inline-video").decode("ascii")

        def fake_request(method, _url, **_kwargs):
            if method == "POST":
                return FakeResponse(
                    {
                        "task_id": "task-inline",
                        "status": "completed",
                        "result_url": f"data:video/mp4;base64,{video_data}",
                    }
                )
            return FakeResponse(
                {"error": {"message": "content endpoint unavailable"}},
                status=404,
            )

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)

        output = tmp_path / "inline.mp4"
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
        )

        assert output.read_bytes() == b"inline-video"

    def test_submission_surfaces_successful_http_error_envelope(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {
                    "code": "provider_rejected",
                    "error": {"message": "channel is unavailable"},
                }
            ),
        )

        with pytest.raises(
            RuntimeError,
            match="provider_rejected: channel is unavailable",
        ):
            NewAPIVideoModel({}).generate(
                "camera pushes in",
                str(tmp_path / "error.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )

    def test_image_submission_surfaces_successful_http_error_envelope(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "image-test-token")
        monkeypatch.setenv("ENMOTION_IMAGE_MODEL", "gpt-image-2")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {
                    "code": "provider_rejected",
                    "error": {
                        "code": "ImagePromptRejected",
                        "message": "prompt is unavailable",
                    },
                }
            ),
        )

        with pytest.raises(NewAPIProviderError) as captured:
            NewAPIImageModel({}).generate(
                "a rejected image",
                str(tmp_path / "error.png"),
                model_id="gpt-image-2",
            )
        assert captured.value.error_code == PROVIDER_REQUEST_ERROR_CODE
        assert captured.value.provider_code == "ImagePromptRejected"

    def test_timeout_includes_provider_task_and_last_status(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_MAX_WAIT", "2")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: FakeResponse(
                {"task_id": "task-slow", "status": "processing"}
            ),
        )
        monotonic_values = iter([0.0, 3.0])
        monkeypatch.setattr(
            "src.models.newapi.time.monotonic",
            lambda: next(monotonic_values),
        )

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIVideoModel({}).generate(
                "camera pushes in",
                str(tmp_path / "slow.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )
        assert exc_info.value.error_code == PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE
        assert exc_info.value.provider_task_id == "task-slow"

    def test_explicit_provider_concurrency_limit_waits_then_submits(
        self,
        monkeypatch,
        tmp_path,
    ):
        calls = []
        responses = iter(
            [
                FakeResponse(
                    {
                        "code": "provider_concurrency_limited",
                        "detail": "provider rejected request",
                    },
                    status=429,
                ),
                FakeResponse({"task_id": "task-after-wait", "status": "completed"}),
                FakeResponse(
                    body=b"queued-video",
                    headers={"Content-Type": "video/mp4"},
                ),
            ]
        )

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return next(responses)

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        output = tmp_path / "queued.mp4"
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
        )

        assert output.read_bytes() == b"queued-video"
        assert [method for method, _url, _kwargs in calls].count("POST") == 2

    def test_resume_provider_task_skips_submission_and_downloads_result(
        self,
        monkeypatch,
        tmp_path,
    ):
        calls = []
        responses = iter(
            [
                FakeResponse({"task_id": "task-resume", "status": "completed"}),
                FakeResponse(
                    body=b"resumed-video",
                    headers={"Content-Type": "video/mp4"},
                ),
            ]
        )

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return next(responses)

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _delay: None)

        output = tmp_path / "resumed.mp4"
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
            provider_task_id="task-resume",
        )

        assert output.read_bytes() == b"resumed-video"
        assert all(method == "GET" for method, _url, _kwargs in calls)
        assert calls[0][1].endswith("/video/generations/task-resume")

    def test_accepted_task_persistence_failure_is_reported_as_ambiguous(
        self,
        monkeypatch,
        tmp_path,
    ):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse({"task_id": "task-not-persisted", "status": "processing"})

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)

        def fail_persistence(*_args):
            raise OSError("disk write failed")

        with pytest.raises(NewAPIProviderError) as exc_info:
            NewAPIVideoModel({}).generate(
                "camera pushes in",
                str(tmp_path / "not-persisted.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
                on_provider_ids=fail_persistence,
            )

        assert exc_info.value.error_code == PROVIDER_OUTCOME_AMBIGUOUS_ERROR_CODE
        assert exc_info.value.provider_task_id == "task-not-persisted"
        assert [method for method, _url, _kwargs in calls] == ["POST"]

    def test_video_models_share_single_flight_gate_across_callers(
        self,
        monkeypatch,
        tmp_path,
    ):
        entered_first_submission = threading.Event()
        release_first_submission = threading.Event()
        guard = threading.Lock()
        active_submissions = 0
        maximum_active_submissions = 0
        submission_count = 0

        def fake_request(method, _url, **_kwargs):
            nonlocal active_submissions, maximum_active_submissions, submission_count
            if method == "POST":
                with guard:
                    submission_count += 1
                    submission_number = submission_count
                    active_submissions += 1
                    maximum_active_submissions = max(
                        maximum_active_submissions,
                        active_submissions,
                    )
                try:
                    if submission_number == 1:
                        entered_first_submission.set()
                        assert release_first_submission.wait(timeout=5)
                    return FakeResponse(
                        {
                            "task_id": f"task-single-flight-{submission_number}",
                            "status": "completed",
                        }
                    )
                finally:
                    with guard:
                        active_submissions -= 1
            return FakeResponse(
                body=b"video",
                headers={"Content-Type": "video/mp4"},
            )

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_MINI_API_KEY", "video-test-token")
        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)

        def generate(index: int):
            model_id = (
                "doubao-seedance-2-0-fast-260128"
                if index == 1
                else "doubao-seedance-2-0-mini-260615"
            )
            return NewAPIVideoModel({}).generate(
                f"camera move {index}",
                str(tmp_path / f"single-{index}.mp4"),
                model_id=model_id,
                generation_mode="t2v",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(generate, 1)
            assert entered_first_submission.wait(timeout=5)
            second = executor.submit(generate, 2)
            assert not second.done()
            release_first_submission.set()
            first.result(timeout=5)
            second.result(timeout=5)

        assert submission_count == 2
        assert maximum_active_submissions == 1

    def test_surfaces_moyu_failure_reason(self, monkeypatch, tmp_path):
        responses = iter(
            [
                FakeResponse({"task_id": "task-failed", "status": "processing"}),
                FakeResponse(
                    {
                        "code": "success",
                        "metadata": {"content": [{"type": "text", "text": "prompt"}]},
                        "data": {
                            "status": "FAILURE",
                            "fail_reason": "provider quota exhausted",
                        },
                    }
                ),
            ]
        )
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: next(responses),
        )
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _: None)

        with pytest.raises(RuntimeError, match="provider quota exhausted") as exc_info:
            NewAPIVideoModel({}).generate(
                "camera pushes in",
                str(tmp_path / "failed.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )
        assert "success:" not in str(exc_info.value)
        assert "text:" not in str(exc_info.value)

    def test_retries_malformed_successful_poll_response(self, monkeypatch, tmp_path):
        class MalformedPoll(FakeResponse):
            def json(self):
                raise ValueError("truncated JSON")

        responses = iter(
            [
                FakeResponse({"task_id": "task-retry", "status": "processing"}),
                MalformedPoll(headers={"Content-Type": "application/json"}),
                FakeResponse(
                    {
                        "task_id": "task-retry",
                        "status": "succeeded",
                        "url": "https://cdn.example/recovered.mp4",
                    }
                ),
                FakeResponse(
                    body=b"recovered-video",
                    headers={"Content-Type": "video/mp4"},
                ),
            ]
        )
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setenv("NEWAPI_VIDEO_POLL_INTERVAL", "0.1")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: next(responses),
        )
        monkeypatch.setattr("src.models.newapi.time.sleep", lambda _: None)

        output = tmp_path / "recovered.mp4"
        NewAPIVideoModel({}).generate(
            "camera pushes in",
            str(output),
            model_id="doubao-seedance-2-0-fast-260128",
            generation_mode="t2v",
        )
        assert output.read_bytes() == b"recovered-video"

    def test_rejects_unverified_multi_reference_mode(self, monkeypatch, tmp_path):
        with pytest.raises(ValueError, match="does not support generation mode"):
            NewAPIVideoModel({}).generate(
                "prompt",
                str(tmp_path / "out.mp4"),
                generation_mode="r2v",
                ref_image_urls=["one.png", "two.png"],
            )

    def test_rejects_i2v_without_an_image(self, tmp_path):
        with pytest.raises(ValueError, match="需要一张输入图片"):
            NewAPIVideoModel({}).generate(
                "prompt",
                str(tmp_path / "out.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="i2v",
            )

    def test_rejects_t2v_with_an_image(self, tmp_path):
        with pytest.raises(ValueError, match="不能包含输入图片"):
            NewAPIVideoModel({}).generate(
                "prompt",
                str(tmp_path / "out.mp4"),
                img_url="https://cdn.example/first.png",
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )

    def test_server_local_image_uses_short_lived_signed_url(
        self,
        monkeypatch,
        tmp_path,
    ):
        from src.apps.server.config import ServerSettings
        from src.apps.server.provider_media import resolve_provider_media_token
        from src.apps.web_runtime.context import bind_tenant, reset_tenant

        workspace_id = "workspace-1"
        output_root = tmp_path / workspace_id / "output"
        reference = output_root / "playground" / "frame.png"
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b"reference-image")
        (output_root / "playground" / "video").mkdir(parents=True)
        monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
        monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setenv("ENMOTION_SESSION_SECRET", "s" * 32)
        monkeypatch.setenv("ENMOTION_ALLOWED_ORIGINS", "https://studio.example.test")
        monkeypatch.setenv("ENMOTION_PUBLIC_BASE_URL", "https://studio.example.test")
        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/videos/task-local/content"):
                return FakeResponse(
                    body=b"video",
                    headers={"Content-Type": "video/mp4"},
                )
            return FakeResponse(
                {
                    "task_id": "task-local",
                    "status": "succeeded",
                    "url": "https://cdn.example/video.mp4",
                }
            )

        monkeypatch.setattr("src.models.newapi.requests.request", fake_request)
        tenant_token = bind_tenant("user-1", workspace_id)
        try:
            NewAPIVideoModel({}).generate(
                "animate it",
                str(output_root / "playground" / "video" / "result.mp4"),
                img_path=str(reference),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="i2v",
                resolution="720p",
            )
        finally:
            reset_tenant(tenant_token)

        image_url = calls[0][2]["json"]["metadata"]["content"][1]["image_url"]["url"]
        parsed = urlparse(image_url)
        assert parsed.netloc == "studio.example.test"
        assert "data:" not in image_url
        token, filename = parsed.path.removeprefix("/provider-media/").split("/", 1)
        assert filename == "frame.png"
        assert (
            resolve_provider_media_token(
                token,
                settings=ServerSettings.from_env(),
            )
            == reference
        )

    def test_loopback_server_retains_bounded_data_url_fallback(
        self,
        monkeypatch,
        tmp_path,
    ):
        from src.apps.web_runtime.context import bind_tenant, reset_tenant

        workspace_id = "workspace-loopback"
        reference = tmp_path / workspace_id / "output" / "playground" / "frame.png"
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b"small-reference")
        monkeypatch.setenv("ENMOTION_SERVER_MODE", "true")
        monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setenv("ENMOTION_SESSION_SECRET", "s" * 32)
        monkeypatch.setenv("ENMOTION_ALLOWED_ORIGINS", "http://127.0.0.1:8080")
        monkeypatch.delenv("ENMOTION_PUBLIC_BASE_URL", raising=False)

        tenant_token = bind_tenant("user-1", workspace_id)
        try:
            encoded = _media_input(str(reference))
        finally:
            reset_tenant(tenant_token)

        assert encoded == "data:image/png;base64,c21hbGwtcmVmZXJlbmNl"

    def test_rejects_unsupported_resolution_before_submission(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: pytest.fail("provider should not be called"),
        )
        with pytest.raises(ValueError, match="Seedance 2.0 文生视频"):
            NewAPIVideoModel({}).generate(
                "prompt",
                str(tmp_path / "out.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
                resolution="1080p",
            )

    def test_video_submission_reports_malformed_json_context(self, monkeypatch, tmp_path):
        class MalformedResponse(FakeResponse):
            text = "upstream returned an empty document"

            def json(self):
                raise ValueError("unexpected end of JSON input")

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr(
            "src.models.newapi.requests.request",
            lambda *_args, **_kwargs: MalformedResponse(
                headers={
                    "Content-Type": "application/json",
                    "x-request-id": "request-789",
                }
            ),
        )

        with pytest.raises(
            RuntimeError,
            match="video submission returned invalid JSON",
        ) as exc_info:
            NewAPIVideoModel({}).generate(
                "prompt",
                str(tmp_path / "out.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )
        assert "HTTP 200" in str(exc_info.value)
        assert "request-789" in str(exc_info.value)

    def test_video_submission_is_not_retried_after_connection_failure(
        self,
        monkeypatch,
        tmp_path,
    ):
        attempts = 0

        def fail_request(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise requests.ConnectionError("connection dropped")

        monkeypatch.setenv("NEWAPI_BASE_URL", "https://gateway.example/v1")
        monkeypatch.setenv("NEWAPI_SEEDANCE_2_FAST_API_KEY", "video-test-token")
        monkeypatch.setattr("src.models.newapi.requests.request", fail_request)
        with pytest.raises(RuntimeError, match="connection dropped"):
            NewAPIVideoModel({}).generate(
                "prompt",
                str(tmp_path / "out.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )
        assert attempts == 1
