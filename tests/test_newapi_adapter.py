import base64
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests

from src.apps.comic_gen.llm_adapter import LLMAdapter
from src.models.newapi import (
    INPUT_IMAGE_PRIVACY_ERROR_CODE,
    RATE_CARD_MISSING_ERROR_CODE,
    RATE_CARD_MISSING_PUBLIC_MESSAGE,
    NewAPIImageModel,
    NewAPIProviderError,
    NewAPIVideoModel,
    _extract_provider_progress,
    _extract_video_status,
    _extract_video_url,
    _media_input,
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


class TestNewAPIImageModel:
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
        assert "[REDACTED]" in str(exc_info.value)

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

        with pytest.raises(RuntimeError, match="task-slow.*last status: processing"):
            NewAPIVideoModel({}).generate(
                "camera pushes in",
                str(tmp_path / "slow.mp4"),
                model_id="doubao-seedance-2-0-fast-260128",
                generation_mode="t2v",
            )

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
