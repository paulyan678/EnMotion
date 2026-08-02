from __future__ import annotations

import json

from app.models import AuditEvent, ProviderConfiguration, ProviderTask
from sqlalchemy import select

from tests.conftest import login


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_provider_config_is_admin_only_masked_encrypted_and_csrf_protected(app_env):
    client, app = app_env
    employee = login(client, "employee", "Employee-password-123")
    assert (
        client.get(
            "/api/v1/admin/provider-config",
            headers=bearer(employee["access_token"]),
        ).status_code
        == 403
    )

    admin = login(client, "admin", "Admin-password-123")
    initial = client.get("/api/v1/admin/provider-config")
    assert initial.status_code == 200
    assert initial.json()["source"] == "environment"
    assert initial.json()["version"] == 0
    assert initial.json()["writable"] is True
    assert "chat-server-secret" not in initial.text
    assert (
        next(item for item in initial.json()["models"] if item["model"] == "deepseek-v4-flash")[
            "configured"
        ]
        is True
    )

    payload = {
        "base_url": "http://rotated-provider.test/v1",
        "credentials": {
            "deepseek-v4-flash": "rotated-chat-secret",
            "gpt-image-2": None,
        },
    }
    assert (
        client.patch(
            "/api/v1/admin/provider-config",
            json=payload,
        ).status_code
        == 403
    )
    updated = client.patch(
        "/api/v1/admin/provider-config",
        headers={"X-CSRF-Token": admin["csrf_token"]},
        json=payload,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["source"] == "managed"
    assert updated.json()["version"] == 1
    assert updated.json()["base_url"] == "http://rotated-provider.test/v1"
    assert "rotated-chat-secret" not in updated.text
    assert (
        next(item for item in updated.json()["models"] if item["model"] == "gpt-image-2")[
            "configured"
        ]
        is False
    )

    with app.state.db.session() as session:
        stored = session.scalar(select(ProviderConfiguration))
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "admin.provider_config_updated")
        )
        assert stored is not None
        serialized = json.dumps(
            {
                "nonce": stored.credentials_nonce,
                "ciphertext": stored.credentials_ciphertext,
            }
        )
        assert "rotated-chat-secret" not in serialized
        assert "rotated-chat-secret" not in json.dumps(audit.detail)
        assert audit.detail == {
            "version": 1,
            "changed_fields": ["base_url", "credentials"],
            "changed_models": ["deepseek-v4-flash", "gpt-image-2"],
        }


def test_provider_config_preflight_rejects_bad_credentials_without_persisting(
    app_env,
    provider_calls,
):
    client, app = app_env
    admin = login(client, "admin", "Admin-password-123")

    rejected = client.patch(
        "/api/v1/admin/provider-config",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
        json={
            "credentials": {
                "gpt-image-2": "rejected-provider-secret",
            },
        },
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "provider validation failed: credentials rejected"
    assert "rejected-provider-secret" not in rejected.text
    assert provider_calls[-1].url.path.endswith("/models")
    with app.state.db.session() as session:
        assert session.scalar(select(ProviderConfiguration)) is None
        assert (
            session.scalar(
                select(AuditEvent).where(AuditEvent.action == "admin.provider_config_updated")
            )
            is None
        )


def test_admin_can_revalidate_model_access_without_a_billable_generation(
    app_env,
    provider_calls,
):
    client, _app = app_env
    admin = login(client, "admin", "Admin-password-123")

    response = client.post(
        "/api/v1/admin/provider-config/validate",
        headers=bearer(admin["access_token"]),
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["balance_available"] is False
    assert response.json()["configured_models"] == [
        "deepseek-v4-flash",
        "doubao-seedance-2-0-fast-260128",
        "gpt-image-2",
    ]
    assert response.json()["validated_at"].endswith("Z")
    validation_calls = [call for call in provider_calls if call.url.path.endswith("/models")]
    assert len(validation_calls) == 3
    assert all(call.method == "GET" for call in validation_calls)


def test_rotated_config_is_shared_by_users_and_video_tasks_keep_their_version(
    app_env,
    provider_calls,
):
    client, app = app_env
    admin = login(client, "admin", "Admin-password-123")
    employee = login(client, "employee", "Employee-password-123")
    other = login(client, "other", "Other-password-123")

    first_config = client.patch(
        "/api/v1/admin/provider-config",
        headers={
            "Authorization": f"Bearer {admin['access_token']}",
        },
        json={
            "credentials": {
                "deepseek-v4-flash": "shared-chat-v1",
                "doubao-seedance-2-0-fast-260128": "shared-video-v1",
            },
        },
    )
    assert first_config.status_code == 200, first_config.text
    assert first_config.json()["version"] == 1

    for token, key in (
        (employee["access_token"], "shared-user-request-1"),
        (other["access_token"], "shared-user-request-2"),
    ):
        response = client.post(
            "/api/v1/gateway/chat/completions",
            headers={**bearer(token), "Idempotency-Key": key},
            json={
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert response.status_code == 200, response.text
    assert provider_calls[-2].headers["authorization"] == "Bearer shared-chat-v1"
    assert provider_calls[-1].headers["authorization"] == "Bearer shared-chat-v1"

    submitted = client.post(
        "/api/v1/gateway/video/generations",
        headers={
            **bearer(employee["access_token"]),
            "Idempotency-Key": "versioned-video-request",
        },
        json={
            "model": "doubao-seedance-2-0-fast-260128",
            "prompt": "versioned task",
            "metadata": {
                "content": [{"type": "text", "text": "versioned task"}],
                "duration": 5,
                "resolution": "720p",
                "ratio": "16:9",
                "generate_audio": True,
                "watermark": False,
            },
        },
    )
    assert submitted.status_code == 200, submitted.text

    rotated = client.patch(
        "/api/v1/admin/provider-config",
        headers=bearer(admin["access_token"]),
        json={
            "credentials": {
                "doubao-seedance-2-0-fast-260128": "shared-video-v2",
            },
        },
    )
    assert rotated.status_code == 200
    status_response = client.get(
        "/api/v1/gateway/video/generations/task-owned-123",
        headers=bearer(employee["access_token"]),
    )
    assert status_response.status_code == 200
    assert provider_calls[-1].headers["authorization"] == "Bearer shared-video-v1"

    with app.state.db.session() as session:
        task = session.scalar(select(ProviderTask))
        assert task.provider_config_version == 1
