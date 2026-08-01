from __future__ import annotations

import base64
import gzip
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from app.config import Settings
from app.factory import create_app
from app.models import CreditLedger, RateCard, User
from app.security import hash_password, normalize_username
from fastapi.testclient import TestClient


@pytest.fixture
def provider_calls() -> list[httpx.Request]:
    return []


@pytest.fixture
def provider_transport(provider_calls: list[httpx.Request]) -> httpx.MockTransport:
    attempts_by_prompt: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        provider_calls.append(request)
        if request.url.path.endswith("/models"):
            if request.headers.get("authorization") == "Bearer rejected-provider-secret":
                return httpx.Response(401, json={"error": "invalid credential"})
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": model}
                        for model in (
                            "deepseek-v4-flash",
                            "deepseek-v4-pro",
                            "qwen3.7-max",
                            "gpt-image-2",
                            "doubao-seedance-2-0-260128",
                            "doubao-seedance-2-0-fast-260128",
                            "doubao-seedance-2-0-mini-260615",
                        )
                    ],
                },
            )
        if request.url.host == "private-downloads.test":
            return httpx.Response(
                302,
                headers={"location": "http://downloads.test/EnMotion.dmg?ticket=test"},
            )
        if request.url.host == "github.com":
            return httpx.Response(
                302,
                headers={"location": "http://downloads.test/EnMotion.dmg?ticket=public"},
            )
        if request.url.host == "downloads.test":
            return httpx.Response(
                200,
                content=b"enmotion-release",
                headers={"content-type": "application/octet-stream"},
            )
        if request.url.path.endswith("/video/generations"):
            body = json.loads(request.content)
            if body.get("prompt") == "concurrency limited":
                return httpx.Response(
                    403,
                    json={
                        "error": {
                            "code": "quota_warning_concurrency_limit",
                            "message": "another video task is still running",
                        }
                    },
                )
            if body.get("prompt") == "missing task id":
                return httpx.Response(200, json={"status": "queued"})
            if body.get("prompt") == "nested task id":
                return httpx.Response(
                    200,
                    json={"data": {"result": {"task_id": "task-nested-456"}}},
                )
            return httpx.Response(
                200,
                json={"id": "task-owned-123", "status": "queued"},
            )
        if request.url.path.endswith("/video/generations/task-owned-123"):
            return httpx.Response(200, json={"id": "task-owned-123", "status": "succeeded"})
        if request.url.path.endswith("/videos/task-owned-123/content"):
            return httpx.Response(
                200, content=b"video-content", headers={"content-type": "video/mp4"}
            )
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            return httpx.Response(200, json={"data": [{"url": "https://media.test/image.png"}]})
        body = json.loads(request.content) if request.content else {}
        prompt = body.get("prompt") or str(body.get("messages", ""))
        attempts_by_prompt[prompt] = attempts_by_prompt.get(prompt, 0) + 1
        attempt = attempts_by_prompt[prompt]
        if request.url.path.endswith("/images/generations"):
            if prompt == "logical image rejection":
                return httpx.Response(
                    200,
                    json={
                        "code": "provider_rejected",
                        "error": {
                            "code": "ImagePromptRejected",
                            "message": "prompt rejected",
                        },
                    },
                )
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(b"image").decode()}]},
            )
        if "connect twice" in prompt and attempt <= 2:
            raise httpx.ConnectError("provider connect failed", request=request)
        if "connect forever" in prompt:
            raise httpx.ConnectError("provider connect failed", request=request)
        if "rate limit twice" in prompt and attempt <= 2:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "busy"})
        if "request timeout twice" in prompt and attempt <= 2:
            return httpx.Response(408, json={"error": "request timeout"})
        if "provider server error" in prompt:
            return httpx.Response(503, json={"error": "ambiguous provider failure"})
        if "reject" in prompt:
            return httpx.Response(400, json={"error": "rejected"})
        if "ambiguous" in prompt:
            raise httpx.ReadTimeout("upstream read timed out", request=request)
        if "compressed" in prompt:
            compressed = gzip.compress(
                json.dumps(
                    {
                        "id": "compressed-response",
                        "choices": [{"message": {"content": "compressed ok"}}],
                    }
                ).encode()
            )
            return httpx.Response(
                200,
                content=compressed,
                headers={
                    "content-type": "application/json",
                    "content-encoding": "gzip",
                },
            )
        return httpx.Response(
            200,
            json={"id": "provider-response", "choices": [{"message": {"content": "ok"}}]},
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def app_env(
    tmp_path: Path,
    provider_transport: httpx.MockTransport,
) -> Iterator[tuple[TestClient, object]]:
    manifest = tmp_path / "releases.json"
    manifest.write_text(
        json.dumps(
            {
                "releases": [
                    {
                        "version": "1.2.3",
                        "platform": "macos-arm64",
                        "channel": "stable",
                        "sha256": "b887495c3b01dd67cbb72f4898db62056931f46b1538fc8fd89f0418bfcfa9e1",
                        "size_bytes": 16,
                        "published_at": "2026-07-24T00:00:00Z",
                        "signature": "test-tauri-minisign-signature",
                        "minimum_supported_version": "1.0.0",
                        "notes": "Test release",
                        "source_url": "http://private-downloads.test/EnMotion.dmg",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'control.db'}",
        session_hmac_secret="test-session-secret-" + "x" * 40,
        provider_base_url="http://provider.test/v1",
        provider_credentials={
            "deepseek-v4-flash": "chat-server-secret",
            "gpt-image-2": "image-server-secret",
            "doubao-seedance-2-0-fast-260128": "video-server-secret",
        },
        provider_config_master_key=base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
        release_manifest_path=str(manifest),
        release_allowed_hosts=("private-downloads.test", "github.com", "downloads.test"),
        release_source_credentials={
            "private-downloads.test": "release-source-secret",
        },
        public_base_url="https://control.test",
        public_base_url_aliases=("https://legacy-control.test",),
        cookie_secure=False,
        allow_insecure_upstreams=True,
        environment="test",
        auto_create_schema=True,
        login_attempts_per_minute=1000,
        provider_retry_backoff_seconds=0.001,
    )
    app = create_app(settings, provider_transport=provider_transport)
    with TestClient(app, base_url="https://control.test") as client:
        with app.state.db.session() as session:
            admin = User(
                username="admin",
                normalized_username=normalize_username("admin"),
                password_hash=hash_password("Admin-password-123"),
                role="admin",
                available_credits=100,
            )
            user = User(
                username="employee",
                normalized_username=normalize_username("employee"),
                password_hash=hash_password("Employee-password-123"),
                role="user",
                available_credits=100,
            )
            other = User(
                username="other",
                normalized_username=normalize_username("other"),
                password_hash=hash_password("Other-password-123"),
                role="user",
                available_credits=100,
            )
            session.add_all([admin, user, other])
            session.flush()
            for account in (admin, user, other):
                session.add(
                    CreditLedger(
                        user_id=account.id,
                        actor_user_id=admin.id,
                        entry_type="adjustment",
                        delta_available=100,
                        delta_reserved=0,
                        available_after=100,
                        reserved_after=0,
                        reason="test seed",
                        idempotency_key=f"seed:{account.id}",
                    )
                )
            session.add_all(
                [
                    RateCard(
                        operation="chat.completions",
                        model="deepseek-v4-flash",
                        unit_cost=7,
                    ),
                    RateCard(
                        operation="images.generations",
                        model="gpt-image-2",
                        unit_cost=11,
                    ),
                    RateCard(
                        operation="images.edits",
                        model="gpt-image-2",
                        unit_cost=13,
                    ),
                    RateCard(
                        operation="video.generations",
                        model="doubao-seedance-2-0-fast-260128",
                        unit_cost=25,
                    ),
                ]
            )
        yield client, app
    app.state.db.engine.dispose()


def login(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "device_label": "pytest"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def admin_token(app_env) -> str:
    client, _app = app_env
    return login(client, "admin", "Admin-password-123")["access_token"]


@pytest.fixture
def user_token(app_env) -> str:
    client, _app = app_env
    return login(client, "employee", "Employee-password-123")["access_token"]
