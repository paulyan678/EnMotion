import json
from types import SimpleNamespace
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.llm import ScriptProcessor, StyleAnalysisError

VALID_RECOMMENDATIONS = [
    {
        "name": "Cinematic Realism",
        "description": "电影级写实光影",
        "reason": "适合紧张的叙事氛围",
        "positive_prompt": "cinematic lighting, film grain",
        "negative_prompt": "flat lighting, low quality",
    },
    {
        "name": "Graphic Noir",
        "description": "高反差黑色电影质感",
        "reason": "强化悬疑和压迫感",
        "positive_prompt": "high contrast, noir shadows",
        "negative_prompt": "pastel colors, cheerful lighting",
    },
    {
        "name": "Painterly Drama",
        "description": "富有笔触的戏剧化画面",
        "reason": "适合表现人物情绪变化",
        "positive_prompt": "painterly texture, dramatic color",
        "negative_prompt": "plastic texture, sterile lighting",
    },
]
VALID_RESPONSE = json.dumps({"recommendations": VALID_RECOMMENDATIONS}, ensure_ascii=False)


class FakeLLM:
    def __init__(self, *, config_error=None, chat_error=None, content=VALID_RESPONSE):
        self.config_error = config_error
        self.chat_error = chat_error
        self.content = content
        self.chat_called = False
        self.messages = None
        self.response_format = None

    def require_configured(self):
        if self.config_error:
            raise self.config_error
        return "chat-model"

    def chat(self, *, messages, response_format):
        self.chat_called = True
        self.messages = messages
        self.response_format = response_format
        if self.chat_error:
            raise self.chat_error
        return self.content


def _processor(fake_llm: FakeLLM) -> ScriptProcessor:
    processor = ScriptProcessor.__new__(ScriptProcessor)
    processor.llm = fake_llm
    return processor


def test_style_analysis_missing_config_is_explicit_and_never_calls_provider():
    fake_llm = FakeLLM(config_error=RuntimeError("missing credential"))

    with pytest.raises(StyleAnalysisError) as caught:
        _processor(fake_llm).analyze_script_for_styles("script")

    assert caught.value.reason == "missing_config"
    assert fake_llm.chat_called is False


def test_style_analysis_timeout_is_explicit():
    fake_llm = FakeLLM(chat_error=TimeoutError("provider timeout"))

    with pytest.raises(StyleAnalysisError) as caught:
        _processor(fake_llm).analyze_script_for_styles("script")

    assert caught.value.reason == "provider_timeout"


def test_style_analysis_malformed_json_is_explicit_not_a_mock_recommendation():
    fake_llm = FakeLLM(content='{"recommendations": [')

    with pytest.raises(StyleAnalysisError) as caught:
        _processor(fake_llm).analyze_script_for_styles("script")

    assert caught.value.reason == "malformed_response"


def test_style_analysis_returns_only_validated_provider_recommendations():
    fake_llm = FakeLLM()

    result = _processor(fake_llm).analyze_script_for_styles(
        "script",
        custom_style_prompt="custom style system prompt",
    )

    assert [item["name"] for item in result] == [item["name"] for item in VALID_RECOMMENDATIONS]
    assert all(item["id"].startswith("ai-rec-") for item in result)
    assert all(item["is_custom"] is False for item in result)
    assert fake_llm.messages[0] == {
        "role": "system",
        "content": "custom style system prompt",
    }
    assert fake_llm.response_format == {"type": "json_object"}


@pytest.fixture
def client(monkeypatch):
    script = SimpleNamespace(
        prompt_config=SimpleNamespace(style_analysis=""),
    )
    monkeypatch.setattr(comic_api.pipeline, "get_script", lambda script_id: script)
    return TestClient(comic_api.app)


@pytest.mark.parametrize(
    "fake_llm,expected_status,expected_reason",
    [
        (FakeLLM(config_error=RuntimeError("missing credential")), 503, "missing_config"),
        (FakeLLM(chat_error=TimeoutError("timeout")), 504, "provider_timeout"),
        (FakeLLM(chat_error=RuntimeError("upstream failed")), 502, "provider_error"),
        (FakeLLM(content="not json"), 502, "malformed_response"),
    ],
)
def test_style_analysis_api_returns_typed_non_200_failures(
    client,
    monkeypatch,
    fake_llm,
    expected_status,
    expected_reason,
):
    monkeypatch.setattr(comic_api.pipeline.script_processor, "llm", fake_llm)

    response = client.post(
        "/projects/project-1/art_direction/analyze",
        json={"script_text": "script"},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == {
        "error": "style_analysis_failed",
        "reason": expected_reason,
        "message": ANY,
    }
    assert "recommendations" not in response.json()


def test_style_analysis_api_returns_valid_provider_recommendations(client, monkeypatch):
    monkeypatch.setattr(comic_api.pipeline.script_processor, "llm", FakeLLM())

    response = client.post(
        "/projects/project-1/art_direction/analyze",
        json={"script_text": "script"},
    )

    assert response.status_code == 200
    recommendations = response.json()["recommendations"]
    assert len(recommendations) == 3
    assert all(item["id"].startswith("ai-rec-") for item in recommendations)
    assert not any(item["id"].startswith("mock-") for item in recommendations)
