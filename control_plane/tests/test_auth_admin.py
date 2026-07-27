from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import AuditEvent, CreditLedger, LoginSession, RateCard, User
from tests.conftest import login


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_rotating_refresh_detects_reuse_and_revokes_descendant(app_env):
    client, _app = app_env
    first = login(client, "employee", "Employee-password-123")
    rotated = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get(
        "/api/v1/auth/session", headers=bearer(first["access_token"])
    ).status_code == 401

    reuse = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first["refresh_token"]},
    )
    assert reuse.status_code == 401
    assert client.get(
        "/api/v1/auth/session", headers=bearer(second["access_token"])
    ).status_code == 401


def test_cookie_mutations_require_csrf(app_env):
    client, _app = app_env
    logged_in = login(client, "admin", "Admin-password-123")
    rejected = client.post(
        "/api/v1/admin/users",
        json={
            "username": "newuser",
            "password": "New-user-password-123",
            "role": "user",
            "initial_credits": 0,
        },
    )
    assert rejected.status_code == 403
    accepted = client.post(
        "/api/v1/admin/users",
        headers={"X-CSRF-Token": logged_in["csrf_token"]},
        json={
            "username": "newuser",
            "password": "New-user-password-123",
            "role": "user",
            "initial_credits": 0,
        },
    )
    assert accepted.status_code == 201, accepted.text


def test_csrf_cookie_lives_long_enough_to_refresh_the_admin_session(app_env):
    client, app = app_env
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "admin",
            "password": "Admin-password-123",
            "device_label": "cookie-lifetime-test",
        },
    )
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    csrf = next(item for item in cookies if item.startswith("enmotion_admin_csrf="))
    assert f"Max-Age={app.state.settings.refresh_ttl_seconds}" in csrf


def test_invalid_refresh_clears_all_browser_session_cookies(app_env):
    client, _app = app_env
    login(client, "admin", "Admin-password-123")
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "x" * 64},
    )
    assert response.status_code == 401
    deleted = response.headers.get_list("set-cookie")
    for name in (
        "enmotion_admin_session",
        "enmotion_admin_refresh",
        "enmotion_admin_csrf",
    ):
        cookie = next(item for item in deleted if item.startswith(f"{name}="))
        assert "Max-Age=0" in cookie


def test_password_verification_has_a_dedicated_concurrency_bound(app_env):
    client, app = app_env
    acquired = 0
    while app.state.password_hash_slots.acquire(blocking=False):
        acquired += 1
    assert acquired == 4
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": "employee",
                "password": "Employee-password-123",
            },
        )
    finally:
        for _ in range(acquired):
            app.state.password_hash_slots.release()
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"


def test_login_rate_limit_cannot_be_bypassed_by_rotating_usernames(app_env):
    client, app = app_env
    from app.security import SlidingWindowLimiter

    app.state.login_global_limiter = SlidingWindowLimiter(100)
    app.state.login_ip_limiter = SlidingWindowLimiter(2)
    app.state.login_account_limiter = SlidingWindowLimiter(100)

    for username in ("unknown-one", "unknown-two"):
        assert client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "incorrect-password"},
        ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "unknown-three", "password": "incorrect-password"},
    ).status_code == 429


def test_admin_lifecycle_and_idempotent_credit_adjustment(app_env, admin_token):
    client, app = app_env
    headers = bearer(admin_token)
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": "managed",
            "password": "Managed-password-123",
            "role": "user",
            "initial_credits": 5,
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    idempotency = str(uuid.uuid4())
    adjustment = {
        "delta": 20,
        "reason": "monthly allocation",
        "idempotency_key": idempotency,
    }
    first = client.post(
        f"/api/v1/admin/users/{user_id}/credits",
        headers=headers,
        json=adjustment,
    )
    second = client.post(
        f"/api/v1/admin/users/{user_id}/credits",
        headers=headers,
        json=adjustment,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    tokens = login(client, "managed", "Managed-password-123")
    deactivated = client.patch(
        f"/api/v1/admin/users/{user_id}/status",
        headers=headers,
        json={"active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False
    assert client.get(
        "/api/v1/account/me", headers=bearer(tokens["access_token"])
    ).status_code == 401

    with app.state.db.session() as session:
        user = session.get(User, user_id)
        entries = session.scalars(
            select(CreditLedger).where(CreditLedger.user_id == user_id)
        ).all()
        assert user.available_credits == 25
        assert len([item for item in entries if item.entry_type == "adjustment"]) == 2
        actions = session.scalars(
            select(AuditEvent.action).where(AuditEvent.target_id == user_id)
        ).all()
        assert actions.count("admin.credit_adjusted") == 1
        assert actions.count("admin.credit_adjustment_replayed") == 1
        assert all(
            item.revoked_at is not None
            for item in session.scalars(
                select(LoginSession).where(LoginSession.user_id == user_id)
            ).all()
        )


def test_admin_password_reset_accepts_six_characters_only_for_reset(
    app_env,
    admin_token,
    monkeypatch,
):
    client, app = app_env
    admin_headers = bearer(admin_token)
    employee_tokens = login(client, "employee", "Employee-password-123")
    with app.state.db.session() as session:
        employee = session.scalar(select(User).where(User.normalized_username == "employee"))
        assert employee is not None
        employee_id = employee.id

    create_with_short_password = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "username": "short-reset-only",
            "password": "Abc123",
            "role": "user",
            "initial_credits": 0,
        },
    )
    assert create_with_short_password.status_code == 422

    self_change_with_short_password = client.post(
        "/api/v1/auth/change-password",
        headers=bearer(employee_tokens["access_token"]),
        json={
            "current_password": "Employee-password-123",
            "new_password": "Abc123",
        },
    )
    assert self_change_with_short_password.status_code == 422

    too_short = client.post(
        f"/api/v1/admin/users/{employee_id}/password",
        headers=admin_headers,
        json={"new_password": "Ab123"},
    )
    assert too_short.status_code == 422

    whitespace_only = client.post(
        f"/api/v1/admin/users/{employee_id}/password",
        headers=admin_headers,
        json={"new_password": "      "},
    )
    assert whitespace_only.status_code == 422
    assert whitespace_only.json()["detail"] == "password must not be only whitespace"

    reset = client.post(
        f"/api/v1/admin/users/{employee_id}/password",
        headers=admin_headers,
        json={"new_password": "Abc123"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["message"] == "password reset; all sessions revoked"
    assert (
        client.get(
            "/api/v1/account/me",
            headers=bearer(employee_tokens["access_token"]),
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "employee",
                "password": "Employee-password-123",
            },
        ).status_code
        == 401
    )

    monkeypatch.setattr(
        "app.routers.auth.password_needs_rehash",
        lambda _password_hash: True,
    )
    replacement_login = client.post(
        "/api/v1/auth/login",
        json={"username": "employee", "password": "Abc123"},
    )
    assert replacement_login.status_code == 200, replacement_login.text

    with app.state.db.session() as session:
        reset_events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_id == employee_id)
            .where(AuditEvent.action == "admin.password_reset")
        ).all()
        assert len(reset_events) == 1
        assert reset_events[0].detail["sessions_revoked"] >= 1


def test_change_password_revokes_all_sessions(app_env):
    client, _app = app_env
    tokens = login(client, "employee", "Employee-password-123")
    changed = client.post(
        "/api/v1/auth/change-password",
        headers=bearer(tokens["access_token"]),
        json={
            "current_password": "Employee-password-123",
            "new_password": "Employee-password-456",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["reauthentication_required"] is True
    assert client.get(
        "/api/v1/account/me", headers=bearer(tokens["access_token"])
    ).status_code == 401
    login(client, "employee", "Employee-password-456")


def test_updating_an_older_rate_card_allocates_the_next_global_version(
    app_env,
    admin_token,
):
    client, app = app_env
    headers = bearer(admin_token)
    payload = {
        "operation": "chat.completions",
        "model": "qwen3.7-max",
        "unit_cost": 5,
        "selectors": {},
        "priority": 0,
        "active": True,
    }
    first = client.post("/api/v1/admin/rate-cards", headers=headers, json=payload)
    second = client.post("/api/v1/admin/rate-cards", headers=headers, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2

    updated = client.patch(
        f"/api/v1/admin/rate-cards/{first.json()['id']}",
        headers=headers,
        json={"unit_cost": 8},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 3

    with app.state.db.session() as session:
        versions = session.scalars(
            select(RateCard.version)
            .where(RateCard.model == "qwen3.7-max")
            .order_by(RateCard.version)
        ).all()
    assert versions == [2, 3]
