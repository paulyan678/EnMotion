"""Tests for PolishError 显式失败 + 双语锚点迭代 (#117 + #119).

旧实现遇到任何问题（LLM 未配置、JSON 解析失败、缺 key、API 异常）
都静默返回原文，前端无法区分"成功"和"失败 fallback"。本套测试
锁定新约定：hard failure 都抛 PolishError(reason=...)；model_echo
作为成功响应中的 warning，保留双语原文且不污染 API failure 指标。
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.llm import (
    PolishError,
    ScriptProcessor,
    _is_echo,
    _resolve_image_for_vision,
)

# ---------------------------------------------------------------------------
# _is_echo unit tests
# ---------------------------------------------------------------------------


class TestIsEcho:
    def test_exact_match_after_normalize(self):
        assert _is_echo("Hello World", "  hello world  ") is True

    def test_high_similarity_above_threshold(self):
        a = "A cinematic wide shot of a hero standing on a cliff"
        b = "A cinematic wide shot of a hero standing on a cliff."  # 仅多一个句号
        assert _is_echo(a, b) is True

    def test_clearly_different(self):
        a = "wide shot of hero on cliff"
        b = "close-up of villain crying in the rain"
        assert _is_echo(a, b) is False

    def test_empty_inputs(self):
        assert _is_echo("", "anything") is False
        assert _is_echo("anything", "") is False


def test_vision_resolver_reads_uploaded_image_from_workspace_output_root(tmp_path):
    uploaded = tmp_path / "uploads" / "first-frame.png"
    uploaded.parent.mkdir()
    uploaded.write_bytes(b"\x89PNG\r\n\x1a\nimage")

    resolved = _resolve_image_for_vision("uploads/first-frame.png", str(tmp_path))

    assert resolved is not None
    assert resolved.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# polish_video_prompt error paths
# ---------------------------------------------------------------------------


class TestPolishVideoPromptErrors:
    def test_is_configured_false_raises(self):
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.require_configured.side_effect = RuntimeError("missing selected-model key")
        with pytest.raises(PolishError) as exc_info:
            sp.polish_video_prompt("draft")
        assert exc_info.value.reason == "is_configured_false"

    def test_api_error_raises(self):
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.chat.side_effect = RuntimeError("New API error: timeout")
        with pytest.raises(PolishError) as exc_info:
            sp.polish_video_prompt("draft prompt")
        assert exc_info.value.reason == "api_error"
        assert "timeout" in exc_info.value.message_zh or "timeout" in exc_info.value.message_en

    def test_json_parse_error_raises(self):
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.is_configured = True
        sp.llm.chat.return_value = "this is not JSON at all"
        with pytest.raises(PolishError) as exc_info:
            sp.polish_video_prompt("draft prompt")
        assert exc_info.value.reason == "json_parse_error"

    def test_missing_keys_raises(self):
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.is_configured = True
        sp.llm.chat.return_value = json.dumps({"only_cn": "x"})  # 缺 prompt_en
        with pytest.raises(PolishError) as exc_info:
            sp.polish_video_prompt("draft prompt")
        assert exc_info.value.reason == "missing_keys"

    def test_model_echo_returns_success_warning_with_prompts(self):
        """model_echo 是成功 warning，不应抛异常或记为 API failure。"""
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.is_configured = True
        sp.llm.chat.return_value = json.dumps(
            {
                "prompt_cn": "镜头：一个英雄站在悬崖上",
                "prompt_en": "draft prompt",  # 模型偷懒回 echo
            }
        )
        result = sp.polish_video_prompt("draft prompt")
        assert result == {
            "prompt_cn": "镜头：一个英雄站在悬崖上",
            "prompt_en": "draft prompt",
            "warning": "model_echo",
        }

    def test_success_returns_bilingual(self):
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.is_configured = True
        sp.llm.chat.return_value = json.dumps(
            {
                "prompt_cn": "电影感广角：英雄静立悬崖之上，眺望远方海雾",
                "prompt_en": "Cinematic wide shot: a heroic figure standing on a cliff, gazing into distant sea mist",
            }
        )
        result = sp.polish_video_prompt("hero on cliff")
        assert "prompt_cn" in result
        assert "prompt_en" in result
        assert "Cinematic" in result["prompt_en"]

    def test_api_returns_model_echo_as_http_200_warning(self, monkeypatch):
        class EchoProcessor:
            def polish_video_prompt(self, *args, **kwargs):
                return {
                    "prompt_cn": "镜头保持原有动作",
                    "prompt_en": "draft prompt",
                    "warning": "model_echo",
                }

        monkeypatch.setattr(comic_api, "ScriptProcessor", EchoProcessor)
        response = TestClient(comic_api.app).post(
            "/video/polish_prompt",
            json={"draft_prompt": "draft prompt"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "prompt_cn": "镜头保持原有动作",
            "prompt_en": "draft prompt",
            "warning": "model_echo",
        }

    def test_api_records_successful_hybrid_text_activity(self, monkeypatch):
        class SuccessfulProcessor:
            def polish_video_prompt(self, *args, **kwargs):
                return {
                    "prompt_cn": "镜头缓慢推近雨中的霓虹招牌",
                    "prompt_en": "The camera slowly pushes toward the neon sign in the rain",
                }

        rows: list[dict] = []
        updates: list[dict] = []
        monkeypatch.setattr(comic_api, "ScriptProcessor", SuccessfulProcessor)
        monkeypatch.setattr(comic_api, "hybrid_mode_enabled", lambda: True)
        monkeypatch.setattr(
            comic_api,
            "get_tenant",
            lambda required=False: type(
                "Tenant",
                (),
                {"workspace_id": "workspace-alice"},
            )(),
        )
        monkeypatch.setattr(
            comic_api,
            "record_text_activity",
            lambda workspace_id, **payload: rows.append({"workspace_id": workspace_id, **payload}),
        )
        monkeypatch.setattr(
            comic_api,
            "update_asset_activity",
            lambda workspace_id, task_id, **payload: updates.append(
                {"workspace_id": workspace_id, "task_id": task_id, **payload}
            ),
        )

        response = comic_api.polish_video_prompt(
            comic_api.PolishVideoPromptRequest(
                draft_prompt="A neon sign in the rain",
                feedback="让镜头缓慢推近",
                polish_model="qwen3.7-max",
            )
        )

        assert response["prompt_en"].startswith("The camera")
        assert len(rows) == 1
        assert rows[0]["workspace_id"] == "workspace-alice"
        assert rows[0]["detail"] == "优化视频提示词"
        assert rows[0]["model_name"] == "qwen3.7-max"
        assert "A neon sign in the rain" in rows[0]["prompt"]
        assert "让镜头缓慢推近" in rows[0]["prompt"]
        assert [update["status"] for update in updates] == ["running", "completed"]

    def test_text_only_catalog_model_omits_available_first_frame(self, tmp_path):
        image = tmp_path / "first-frame.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.require_configured.return_value = "deepseek-v4-flash"
        sp.llm.chat.return_value = json.dumps(
            {
                "prompt_cn": "镜头缓慢推近，雨水沿着霓虹招牌滑落",
                "prompt_en": "The camera slowly pushes in as rain runs over the neon sign",
            }
        )

        sp.polish_video_prompt(
            "camera moves through the rain",
            image_urls=[str(image)],
            polish_model="deepseek-v4-flash",
            output_root=str(tmp_path),
        )

        messages = sp.llm.chat.call_args.kwargs["messages"]
        assert messages[-1]["content"] == "camera moves through the rain"
        assert "attached the first frame" not in messages[0]["content"]

    def test_custom_prompt_keeps_json_response_contract(self):
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.require_configured.return_value = "qwen3.7-max"
        sp.llm.chat.return_value = json.dumps(
            {
                "prompt_cn": "镜头缓慢推近，雨水沿着霓虹招牌滑落",
                "prompt_en": "The camera slowly pushes in as rain runs over the neon sign",
            }
        )

        sp.polish_video_prompt(
            "camera moves through the rain",
            custom_system_prompt="只返回视频提示词。",
            polish_model="qwen3.7-max",
        )

        system_prompt = sp.llm.chat.call_args.kwargs["messages"][0]["content"]
        assert system_prompt.startswith("只返回视频提示词。")
        assert "JSON" in system_prompt
        assert '"prompt_cn"' in system_prompt
        assert '"prompt_en"' in system_prompt


class TestPolishStoryboardPromptErrors:
    def _processor(self, response=None, error=None):
        processor = ScriptProcessor.__new__(ScriptProcessor)
        processor.llm = MagicMock()
        processor.llm.is_configured = True
        if error is not None:
            processor.llm.chat.side_effect = error
        else:
            processor.llm.chat.return_value = response
        return processor

    def test_missing_configuration_is_explicit(self):
        processor = self._processor()
        processor.llm.require_configured.side_effect = RuntimeError("missing key")

        with pytest.raises(PolishError) as exc_info:
            processor.polish_storyboard_prompt("draft", [])

        assert exc_info.value.reason == "is_configured_false"

    def test_provider_error_is_not_returned_as_mock_success(self):
        processor = self._processor(error=TimeoutError("provider timeout"))

        with pytest.raises(PolishError) as exc_info:
            processor.polish_storyboard_prompt("draft", [])

        assert exc_info.value.reason == "api_error"

    @pytest.mark.parametrize(
        ("response", "reason"),
        [
            ("not-json", "json_parse_error"),
            (json.dumps({"prompt_cn": "only one language"}), "missing_keys"),
            (json.dumps({"prompt_cn": "", "prompt_en": "English"}), "missing_keys"),
        ],
    )
    def test_malformed_result_is_not_returned_as_original_prompt(self, response, reason):
        processor = self._processor(response=response)

        with pytest.raises(PolishError) as exc_info:
            processor.polish_storyboard_prompt("draft", [])

        assert exc_info.value.reason == reason

    def test_success_returns_validated_bilingual_prompts(self):
        processor = self._processor(
            response=json.dumps(
                {
                    "prompt_cn": "电影感广角镜头",
                    "prompt_en": "A cinematic wide shot with deliberate lighting",
                }
            )
        )

        result = processor.polish_storyboard_prompt("draft", [])

        assert result == {
            "prompt_cn": "电影感广角镜头",
            "prompt_en": "A cinematic wide shot with deliberate lighting",
        }

    def test_api_maps_typed_failure_to_bad_gateway(self, monkeypatch):
        def fail_refine(*args, **kwargs):
            raise PolishError(
                reason="api_error",
                message_zh="上游失败",
                message_en="Upstream failed",
            )

        monkeypatch.setattr(comic_api.pipeline, "refine_frame_prompt", fail_refine)
        response = TestClient(comic_api.app).post(
            "/projects/project-1/storyboard/refine_prompt",
            json={"frame_id": "frame-1", "raw_prompt": "draft", "assets": []},
        )

        assert response.status_code == 502
        assert response.json()["detail"]["reason"] == "api_error"


# ---------------------------------------------------------------------------
# 双语锚点迭代 (#119)
# ---------------------------------------------------------------------------


class TestBilingualAnchoredIteration:
    def test_first_polish_no_prev_cn(self):
        """首次 polish：不传 prev_cn，user_message 是 draft_prompt 本身。"""
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.is_configured = True
        sp.llm.chat.return_value = json.dumps(
            {
                "prompt_cn": "改写版",
                "prompt_en": "Polished version",
            }
        )
        sp.polish_video_prompt("hero on cliff")
        # 第一参数 messages，最后一条 user 消息就是 draft
        call = sp.llm.chat.call_args
        messages = call.kwargs["messages"] if "messages" in call.kwargs else call.args[0]
        assert messages[-1]["content"] == "hero on cliff"

    def test_iteration_with_prev_cn_uses_bilingual_anchor(self):
        """迭代 + prev_cn：user_message 同时包含 CN + EN + 反馈。"""
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.is_configured = True
        sp.llm.chat.return_value = json.dumps(
            {
                "prompt_cn": "迭代版",
                "prompt_en": "Iterated version unique",
            }
        )
        sp.polish_video_prompt(
            draft_prompt="Polished EN previous",
            feedback="把第二句改成俯视角",
            prev_cn="电影感广角：英雄站立悬崖",
        )
        call = sp.llm.chat.call_args
        messages = call.kwargs["messages"] if "messages" in call.kwargs else call.args[0]
        user_content = messages[-1]["content"]
        assert "电影感广角" in user_content  # CN 锚点
        assert "Polished EN previous" in user_content  # EN 上次结果
        assert "把第二句改成俯视角" in user_content  # 反馈
        assert "双语" in user_content  # 提示模型同步双语

    def test_iteration_without_prev_cn_falls_back_to_legacy_format(self):
        """向后兼容：旧调用方未带 prev_cn 时，仍走单语反馈格式。"""
        sp = ScriptProcessor.__new__(ScriptProcessor)
        sp.llm = MagicMock()
        sp.llm.is_configured = True
        sp.llm.chat.return_value = json.dumps(
            {
                "prompt_cn": "改",
                "prompt_en": "Changed text X",
            }
        )
        sp.polish_video_prompt(
            draft_prompt="Polished EN previous",
            feedback="make it darker",
        )
        call = sp.llm.chat.call_args
        messages = call.kwargs["messages"] if "messages" in call.kwargs else call.args[0]
        user_content = messages[-1]["content"]
        assert "[当前提示词]" in user_content  # 旧格式
        assert "[当前提示词-CN]" not in user_content  # 没用双语锚点格式
