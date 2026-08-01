from __future__ import annotations

import asyncio
import hashlib
import json
import re

import httpx
import pytest
from app.models import ProviderTask, ReleaseGrant, UsageRequest, User
from app.routers.gateway import (
    _read_response_limited,
    _ResponseTooLarge,
    _stream_response,
)
from app.routers.releases import _stage_verified_release
from app.security import ConcurrentKeyLimiter
from fastapi import HTTPException
from sqlalchemy import select

from tests.conftest import login


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def chat_payload(text: str) -> dict:
    return {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": text}],
    }


def test_gateway_injects_server_credential_and_suppresses_duplicates(app_env, provider_calls):
    client, app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]
    headers = {**bearer(token), "Idempotency-Key": "gateway-request-001"}
    first = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers,
        json=chat_payload("hello"),
    )
    assert first.status_code == 200, first.text
    assert first.json()["choices"][0]["message"]["content"] == "ok"
    assert len(provider_calls) == 1
    assert provider_calls[0].headers["authorization"] == "Bearer chat-server-secret"
    upstream_key = provider_calls[0].headers["idempotency-key"]
    assert upstream_key != "gateway-request-001"
    assert re.fullmatch(r"[a-f0-9]{64}", upstream_key)

    replay = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers,
        json=chat_payload("hello"),
    )
    assert replay.status_code == 202
    assert replay.json()["idempotent_replay"] is True
    assert len(provider_calls) == 1
    conflict = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers,
        json=chat_payload("different request"),
    )
    assert conflict.status_code == 409

    other_token = login(client, "other", "Other-password-123")["access_token"]
    other_request = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(other_token), "Idempotency-Key": "gateway-request-001"},
        json=chat_payload("hello"),
    )
    assert other_request.status_code == 200
    assert len(provider_calls) == 2
    assert provider_calls[1].headers["idempotency-key"] != upstream_key

    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.username == "employee"))
        other_user = session.scalar(select(User).where(User.username == "other"))
        assert user.available_credits == 93
        assert user.reserved_credits == 0
        assert other_user.available_credits == 93
        assert other_user.reserved_credits == 0


def test_compressed_provider_response_preserves_content_encoding(app_env):
    client, _app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]
    response = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "compressed-chat-001"},
        json=chat_payload("compressed"),
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "compressed ok"


def test_gateway_accepts_strict_multimodal_chat_content(app_env, provider_calls):
    client, _app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "Describe only what is visible."},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
                    },
                    {"type": "text", "text": "Polish this prompt"},
                ],
            },
        ],
    }

    response = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "gateway-vision-001"},
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert json.loads(provider_calls[-1].content) == payload


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "text", "text": "missing image"}],
        [
            {"type": "image_url", "image_url": {"url": "http://insecure/image.png"}},
            {"type": "text", "text": "prompt"},
        ],
        [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
            {"type": "input_audio", "input_audio": {"data": "abc"}},
        ],
    ],
)
def test_gateway_rejects_invalid_multimodal_chat_content(app_env, content):
    client, _app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]

    response = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "gateway-vision-invalid-001"},
        json={
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": content}],
        },
    )

    assert response.status_code == 422


def test_rejection_refunds_but_ambiguous_timeout_stays_reserved(app_env):
    client, app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]
    rejected = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "gateway-reject-001"},
        json=chat_payload("reject this"),
    )
    assert rejected.status_code == 400
    ambiguous = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "gateway-ambiguous-001"},
        json=chat_payload("ambiguous outcome"),
    )
    assert ambiguous.status_code == 502
    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.username == "employee"))
        statuses = session.scalars(
            select(UsageRequest.status)
            .where(UsageRequest.user_id == user.id)
            .order_by(UsageRequest.created_at)
        ).all()
        assert statuses == ["refunded", "pending_reconciliation"]
        assert user.available_credits == 93
        assert user.reserved_credits == 7
    admin = login(client, "admin", "Admin-password-123")["access_token"]
    pending = client.get(
        "/api/v1/admin/usage",
        headers=bearer(admin),
        params={"usage_status": "pending_reconciliation"},
    )
    assert pending.status_code == 200
    assert len(pending.json()) == 1
    assert pending.json()[0]["user_id"] == user.id


def test_provider_connect_and_rate_limit_failures_retry_before_charging(
    app_env,
    provider_calls,
):
    client, app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]

    connected = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "connect-retry-001"},
        json=chat_payload("connect twice"),
    )
    assert connected.status_code == 200, connected.text
    connect_calls = [call for call in provider_calls if b"connect twice" in call.content]
    assert len(connect_calls) == 3
    assert len({call.headers["idempotency-key"] for call in connect_calls}) == 1

    rate_limited = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "rate-retry-001"},
        json=chat_payload("rate limit twice"),
    )
    assert rate_limited.status_code == 200, rate_limited.text
    rate_calls = [call for call in provider_calls if b"rate limit twice" in call.content]
    assert len(rate_calls) == 3
    assert len({call.headers["idempotency-key"] for call in rate_calls}) == 1

    timed_out = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "timeout-retry-001"},
        json=chat_payload("request timeout twice"),
    )
    assert timed_out.status_code == 200, timed_out.text
    timeout_calls = [call for call in provider_calls if b"request timeout twice" in call.content]
    assert len(timeout_calls) == 3
    assert len({call.headers["idempotency-key"] for call in timeout_calls}) == 1

    with app.state.db.session() as session:
        usages = session.scalars(
            select(UsageRequest).where(
                UsageRequest.idempotency_key.in_(
                    ["connect-retry-001", "rate-retry-001", "timeout-retry-001"]
                )
            )
        ).all()
        assert {usage.status for usage in usages} == {"settled"}


def test_exhausted_connect_retries_refund_but_server_error_is_not_replayed(
    app_env,
    provider_calls,
):
    client, app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]

    unavailable = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "connect-exhausted-001"},
        json=chat_payload("connect forever"),
    )
    assert unavailable.status_code == 502
    assert unavailable.headers["x-enmotion-provider-retry-exhausted"] == "true"
    assert len([call for call in provider_calls if b"connect forever" in call.content]) == 4

    replay = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "connect-exhausted-001"},
        json=chat_payload("connect forever"),
    )
    assert replay.status_code == 202
    assert replay.headers["x-enmotion-provider-retry-exhausted"] == "true"
    assert len([call for call in provider_calls if b"connect forever" in call.content]) == 4

    ambiguous = client.post(
        "/api/v1/gateway/chat/completions",
        headers={**bearer(token), "Idempotency-Key": "server-error-001"},
        json=chat_payload("provider server error"),
    )
    assert ambiguous.status_code == 502
    assert ambiguous.json()["code"] == "provider_outcome_ambiguous"
    assert len([call for call in provider_calls if b"provider server error" in call.content]) == 1

    with app.state.db.session() as session:
        refunded = session.scalar(
            select(UsageRequest).where(UsageRequest.idempotency_key == "connect-exhausted-001")
        )
        pending = session.scalar(
            select(UsageRequest).where(UsageRequest.idempotency_key == "server-error-001")
        )
        assert refunded.status == "refunded"
        assert refunded.error_code == "provider_connect_failed"
        assert pending.status == "pending_reconciliation"
        assert pending.error_code == "ambiguous_provider_server_error"


def test_image_edit_multipart_is_bounded_allowlisted_and_server_authenticated(
    app_env, provider_calls
):
    client, app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]
    response = client.post(
        "/api/v1/gateway/images/edits",
        headers={**bearer(token), "Idempotency-Key": "image-edit-001"},
        data={
            "model": "gpt-image-2",
            "prompt": "make it brighter",
            "n": "1",
            "size": "1024x1024",
            "quality": "high",
        },
        files=[("image[]", ("source.png", b"not-a-real-png", "image/png"))],
    )
    assert response.status_code == 200, response.text
    assert provider_calls[-1].headers["authorization"] == "Bearer image-server-secret"
    assert provider_calls[-1].headers["content-type"].startswith("multipart/form-data")
    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.username == "employee"))
        assert user.available_credits == 87
        assert user.reserved_credits == 0


def test_image_edit_idempotent_replay_recovers_the_cached_provider_result(
    app_env,
    provider_calls,
):
    client, _app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]
    headers = {**bearer(token), "Idempotency-Key": "image-edit-cache-001"}
    data = {
        "model": "gpt-image-2",
        "prompt": "make it brighter",
        "n": "1",
        "size": "1024x1024",
        "quality": "high",
    }
    files = [("image[]", ("source.png", b"not-a-real-png", "image/png"))]

    first = client.post(
        "/api/v1/gateway/images/edits",
        headers=headers,
        data=data,
        files=files,
    )
    assert first.status_code == 200, first.text
    assert first.headers["x-enmotion-usage-id"]
    first_provider_call_count = len(provider_calls)

    replay = client.post(
        "/api/v1/gateway/images/edits",
        headers=headers,
        data=data,
        files=files,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert replay.headers["x-enmotion-idempotent-replay"] == "true"
    assert len(provider_calls) == first_provider_call_count


def test_image_generation_replay_recovers_cached_result_and_logical_errors_refund(
    app_env,
    provider_calls,
):
    client, app = app_env
    token = login(client, "employee", "Employee-password-123")["access_token"]
    headers = {**bearer(token), "Idempotency-Key": "image-generation-cache-001"}
    payload = {
        "model": "gpt-image-2",
        "prompt": "generate an image",
        "n": 1,
        "size": "1024x1024",
        "quality": "high",
    }
    first = client.post("/api/v1/gateway/images/generations", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    first_provider_call_count = len(provider_calls)

    replay = client.post("/api/v1/gateway/images/generations", headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.content == first.content
    assert replay.headers["x-enmotion-idempotent-replay"] == "true"
    assert len(provider_calls) == first_provider_call_count

    rejected = client.post(
        "/api/v1/gateway/images/generations",
        headers={**bearer(token), "Idempotency-Key": "image-logical-rejection-001"},
        json={**payload, "prompt": "logical image rejection"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "ImagePromptRejected"
    with app.state.db.session() as session:
        usage = session.scalar(
            select(UsageRequest).where(
                UsageRequest.idempotency_key == "image-logical-rejection-001"
            )
        )
        assert usage.status == "refunded"
        assert usage.error_code == "provider_rejected"


def test_video_task_is_bound_to_owner_and_polling_does_not_charge(app_env):
    client, app = app_env
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    other = login(client, "other", "Other-password-123")["access_token"]
    payload = {
        "model": "doubao-seedance-2-0-fast-260128",
        "prompt": "a test clip",
        "metadata": {
            "content": [{"type": "text", "text": "a test clip"}],
            "duration": 5,
            "resolution": "720p",
            "ratio": "16:9",
            "generate_audio": True,
            "watermark": False,
        },
    }
    submitted = client.post(
        "/api/v1/gateway/video/generations",
        headers={**bearer(employee), "Idempotency-Key": "video-request-001"},
        json=payload,
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["id"] == "task-owned-123"
    status_response = client.get(
        "/api/v1/gateway/video/generations/task-owned-123",
        headers=bearer(employee),
    )
    assert status_response.status_code == 200
    content = client.get(
        "/api/v1/gateway/videos/task-owned-123/content",
        headers=bearer(employee),
    )
    assert content.status_code == 200
    assert content.content == b"video-content"
    assert (
        client.get(
            "/api/v1/gateway/video/generations/task-owned-123",
            headers=bearer(other),
        ).status_code
        == 404
    )
    with app.state.db.session() as session:
        owner = session.scalar(select(User).where(User.username == "employee"))
        assert owner.available_credits == 75
        assert owner.reserved_credits == 0
        assert session.scalar(select(ProviderTask)).user_id == owner.id


def test_video_provider_concurrency_limit_is_refunded_and_exposed_as_retryable(app_env):
    client, app = app_env
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    submitted = client.post(
        "/api/v1/gateway/video/generations",
        headers={**bearer(employee), "Idempotency-Key": "video-concurrency-001"},
        json={
            "model": "doubao-seedance-2-0-fast-260128",
            "prompt": "concurrency limited",
            "metadata": {
                "content": [{"type": "text", "text": "concurrency limited"}],
                "duration": 5,
                "resolution": "720p",
                "ratio": "16:9",
                "generate_audio": True,
                "watermark": False,
            },
        },
    )

    assert submitted.status_code == 429, submitted.text
    assert submitted.headers["retry-after"] == "15"
    assert submitted.json() == {
        "detail": "provider rejected request",
        "code": "provider_concurrency_limited",
        "provider_status": 403,
    }
    with app.state.db.session() as session:
        owner = session.scalar(select(User).where(User.username == "employee"))
        usage = session.scalar(
            select(UsageRequest).where(UsageRequest.idempotency_key == "video-concurrency-001")
        )
        assert owner.available_credits == 100
        assert owner.reserved_credits == 0
        assert usage.status == "refunded"
        assert usage.error_code == "provider_concurrency_limited"
        assert session.scalar(select(ProviderTask)) is None


def test_video_acceptance_without_task_id_stays_reserved_for_reconciliation(app_env):
    client, app = app_env
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    submitted = client.post(
        "/api/v1/gateway/video/generations",
        headers={**bearer(employee), "Idempotency-Key": "video-missing-task-001"},
        json={
            "model": "doubao-seedance-2-0-fast-260128",
            "prompt": "missing task id",
            "metadata": {
                "content": [{"type": "text", "text": "missing task id"}],
                "duration": 5,
                "resolution": "720p",
                "ratio": "16:9",
                "generate_audio": True,
                "watermark": False,
            },
        },
    )
    assert submitted.status_code == 502
    assert "remain reserved" in submitted.json()["detail"]

    with app.state.db.session() as session:
        owner = session.scalar(select(User).where(User.username == "employee"))
        usage = session.scalar(
            select(UsageRequest).where(UsageRequest.idempotency_key == "video-missing-task-001")
        )
        assert owner.available_credits == 75
        assert owner.reserved_credits == 25
        assert usage.status == "pending_reconciliation"
        assert usage.error_code == "invalid_video_submission_response"
        assert session.scalar(select(ProviderTask)) is None


def test_health_runtime_and_release_download_are_public_and_allowlisted(
    app_env,
    provider_calls,
):
    client, _app = app_env
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.headers["x-content-type-options"] == "nosniff"
    assert client.get("/health/ready").json()["status"] == "ready"
    admin = client.get("/admin/")
    assert admin.status_code == 200
    assert "EnMotion 管理中心" in admin.text
    runtime = client.get("/api/v1/runtime-config")
    assert runtime.status_code == 200
    assert runtime.json()["app_name"] == "EnMotion"
    assert (
        client.get(
            "/api/v1/releases/latest",
            params={"platform": "macos-arm64", "channel": "stable"},
        ).status_code
        == 401
    )
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    latest = client.get(
        "/api/v1/releases/latest",
        headers=bearer(employee),
        params={"platform": "macos-arm64", "channel": "stable"},
    )
    assert latest.status_code == 200
    assert latest.json()["version"] == "1.2.3"
    assert latest.json()["signature"] == "test-tauri-minisign-signature"
    assert "source_url" not in latest.json()
    release = client.get(latest.json()["download_url"], headers=bearer(employee))
    assert release.status_code == 200
    assert release.content == b"enmotion-release"
    assert release.headers["x-content-sha256"] == hashlib.sha256(b"enmotion-release").hexdigest()
    private_source = next(
        call for call in provider_calls if call.url.host == "private-downloads.test"
    )
    redirected_source = next(call for call in provider_calls if call.url.host == "downloads.test")
    assert private_source.headers["authorization"] == "Bearer release-source-secret"
    assert "authorization" not in redirected_source.headers


def test_public_github_release_download_needs_no_server_token(app_env, provider_calls):
    client, app = app_env
    manifest_path = app.state.settings.release_manifest
    assert manifest_path is not None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["releases"][0]["source_url"] = (
        "https://github.com/acme/EnMotion/releases/download/"
        "desktop-v1.2.3/EnMotion-1.2.3-macOS-arm64.app.tar.gz"
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    employee = login(client, "employee", "Employee-password-123")["access_token"]
    latest = client.get(
        "/api/v1/releases/latest",
        headers=bearer(employee),
        params={"platform": "macos-arm64", "channel": "stable"},
    )
    release = client.get(latest.json()["download_url"], headers=bearer(employee))
    assert release.status_code == 200
    public_source = next(call for call in provider_calls if call.url.host == "github.com")
    redirected_source = next(call for call in provider_calls if call.url.host == "downloads.test")
    assert "authorization" not in public_source.headers
    assert "authorization" not in redirected_source.headers


def test_release_capability_is_hashed_bound_same_origin_and_one_time(app_env):
    client, app = app_env
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    created = client.post(
        "/api/v1/releases/session",
        headers=bearer(employee),
        json={
            "target": "darwin",
            "arch": "aarch64",
            "current_version": "1.0.0",
            "channel": "stable",
        },
    )
    assert created.status_code == 201, created.text
    manifest_url = created.json()["manifest_url"]
    assert manifest_url.startswith("https://control.test/api/v1/releases/session/")
    assert manifest_url.endswith("/manifest")
    token = manifest_url.removesuffix("/manifest").rsplit("/", 1)[-1]
    assert re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token)

    with app.state.db.session() as session:
        grant = session.scalar(select(ReleaseGrant))
        assert grant is not None
        assert grant.user_id == session.scalar(select(User.id).where(User.username == "employee"))
        assert grant.platform == "macos-arm64"
        assert grant.release_version == "1.2.3"
        assert grant.token_digest != token
        assert token not in grant.token_digest
        assert grant.manifest_consumed_at is None
        assert grant.download_consumed_at is None

    download_url = f"https://control.test/api/v1/releases/session/{token}/download"
    assert client.get(download_url).status_code == 404

    manifest = client.get(manifest_url)
    assert manifest.status_code == 200, manifest.text
    payload = manifest.json()
    assert payload == {
        "version": "1.2.3",
        "url": (f"https://control.test/api/v1/releases/session/{token}/download"),
        "signature": "test-tauri-minisign-signature",
        "notes": "Test release",
        "pub_date": "2026-07-24T00:00:00Z",
    }
    assert client.get(manifest_url).status_code == 404
    with app.state.db.session() as session:
        grant = session.scalar(select(ReleaseGrant))
        assert grant.manifest_consumed_at is not None
        assert grant.download_consumed_at is None

    archive = client.get(payload["url"])
    assert archive.status_code == 200
    assert archive.content == b"enmotion-release"
    assert archive.headers["x-content-sha256"] == hashlib.sha256(b"enmotion-release").hexdigest()
    assert client.get(payload["url"]).status_code == 404
    with app.state.db.session() as session:
        grant = session.scalar(select(ReleaseGrant))
        assert grant.download_consumed_at is not None


def test_release_capability_uses_only_an_explicitly_approved_request_origin(app_env):
    client, _app = app_env
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    payload = {
        "target": "darwin",
        "arch": "aarch64",
        "current_version": "1.0.0",
        "channel": "stable",
    }

    legacy = client.post(
        "https://legacy-control.test/api/v1/releases/session",
        headers=bearer(employee),
        json=payload,
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["manifest_url"].startswith(
        "https://legacy-control.test/api/v1/releases/session/"
    )

    unapproved = client.post(
        "https://attacker.test/api/v1/releases/session",
        headers=bearer(employee),
        json=payload,
    )
    assert unapproved.status_code == 201, unapproved.text
    assert unapproved.json()["manifest_url"].startswith(
        "https://control.test/api/v1/releases/session/"
    )


def test_release_capability_returns_no_update_and_rejects_inactive_owner(app_env):
    client, app = app_env
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    current = client.post(
        "/api/v1/releases/session",
        headers=bearer(employee),
        json={
            "target": "darwin",
            "arch": "aarch64",
            "current_version": "1.2.3",
            "channel": "stable",
        },
    )
    assert current.status_code == 201
    assert client.get(current.json()["manifest_url"]).status_code == 204
    assert client.get(current.json()["manifest_url"]).status_code == 404

    older = client.post(
        "/api/v1/releases/session",
        headers=bearer(employee),
        json={
            "target": "darwin",
            "arch": "aarch64",
            "current_version": "1.0.0",
            "channel": "stable",
        },
    )
    manifest_url = older.json()["manifest_url"]
    with app.state.db.session() as session:
        user = session.scalar(select(User).where(User.username == "employee"))
        user.active = False
    assert client.get(manifest_url).status_code == 404


def test_release_download_stage_failure_keeps_capability_retryable(app_env):
    client, app = app_env
    employee = login(client, "employee", "Employee-password-123")["access_token"]
    created = client.post(
        "/api/v1/releases/session",
        headers=bearer(employee),
        json={
            "target": "darwin",
            "arch": "aarch64",
            "current_version": "1.0.0",
            "channel": "stable",
        },
    )
    manifest = client.get(created.json()["manifest_url"])
    assert manifest.status_code == 200
    download_url = manifest.json()["url"]

    manifest_path = app.state.settings.release_manifest
    assert manifest_path is not None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_hash = payload["releases"][0]["sha256"]
    payload["releases"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    failed = client.get(download_url)
    assert failed.status_code == 502
    with app.state.db.session() as session:
        grant = session.scalar(select(ReleaseGrant))
        assert grant.download_consumed_at is None

    payload["releases"][0]["sha256"] = expected_hash
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    succeeded = client.get(download_url)
    assert succeeded.status_code == 200
    assert succeeded.content == b"enmotion-release"
    assert client.get(download_url).status_code == 404


def test_release_stream_rejects_content_that_does_not_match_manifest() -> None:
    async def stage():
        response = httpx.Response(200, content=b"tampered-release")
        return await _stage_verified_release(
            response,
            expected_size=len(b"tampered-release"),
            expected_sha256="0" * 64,
        )

    with pytest.raises(HTTPException, match="integrity verification"):
        asyncio.run(stage())


def test_gateway_stream_closes_upstream_when_iteration_fails() -> None:
    class FailingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadError("truncated provider stream")

        async def aclose(self) -> None:
            self.closed = True

    stream = FailingStream()
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://provider.test/stream"),
        stream=stream,
    )

    async def consume() -> None:
        async for _chunk in _stream_response(response):
            pass

    with pytest.raises(httpx.ReadError):
        asyncio.run(consume())
    assert stream.closed is True


def test_video_submission_response_is_bounded_while_streaming() -> None:
    class LargeStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.yielded = 0

        async def __aiter__(self):
            for chunk in (b"a" * 1024, b"b" * 1024, b"overflow"):
                self.yielded += 1
                yield chunk

    stream = LargeStream()
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://provider.test/video"),
        stream=stream,
    )

    async def read() -> bytes:
        try:
            return await _read_response_limited(response, 2048)
        finally:
            await response.aclose()

    with pytest.raises(_ResponseTooLarge):
        asyncio.run(read())
    assert stream.yielded == 3


def test_release_download_concurrency_is_bounded_per_user_and_globally() -> None:
    limiter = ConcurrentKeyLimiter(global_limit=2, per_key_limit=1)
    assert limiter.acquire("user-a") is True
    assert limiter.acquire("user-a") is False
    assert limiter.acquire("user-b") is True
    assert limiter.acquire("user-c") is False
    limiter.release("user-a")
    assert limiter.acquire("user-c") is True
    limiter.release("user-b")
    limiter.release("user-c")
