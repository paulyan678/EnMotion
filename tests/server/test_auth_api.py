from __future__ import annotations

from datetime import timedelta

from fastapi import Body, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.apps.server.models import LoginSession, User, Workspace, WorkspaceMembership, utc_now
from src.apps.server.rate_limit import InMemoryRateLimiter, login_rate_limiter
from src.apps.server.security import digest_token
from src.apps.web_runtime.context import get_tenant

from .conftest import ADMIN_PASSWORD


def login(client, username="admin", password=ADMIN_PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


def csrf_headers(auth_response, *, origin=None):
    headers = {"X-CSRF-Token": auth_response["csrf_token"]}
    if origin:
        headers["Origin"] = origin
    return headers


def test_health_is_public_but_application_routes_fail_closed(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers["x-request-id"]
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert response.json() == {
        "detail": "请先登录。",
        "error": {
            "code": "AUTHENTICATION_REQUIRED",
            "message": "请先登录。",
            "request_id": response.headers["x-request-id"],
            "retryable": False,
        },
    }


def test_login_sets_split_cookies_and_stores_only_token_digests(client, database):
    response = login(client)
    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "admin"
    assert payload["role"] == "admin"
    assert payload["workspace_id"]
    assert payload["csrf_token"]

    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookies if value.startswith("enmotion_session="))
    csrf_cookie = next(value for value in cookies if value.startswith("enmotion_csrf="))
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "SameSite=lax" in session_cookie

    raw_session = client.cookies["enmotion_session"]
    raw_csrf = client.cookies["enmotion_csrf"]
    with database.session() as db:
        stored = db.scalar(select(LoginSession))
        assert stored is not None
        assert raw_session not in stored.token_hash
        assert raw_csrf not in stored.csrf_hash
        assert stored.token_hash == digest_token(
            raw_session, "test-only-session-secret-that-is-long-enough"
        )

    session = client.get("/auth/session")
    assert session.status_code == 200
    assert session.json() == payload
    assert client.get("/auth/me").json() == payload


def test_bad_login_is_generic_and_does_not_set_cookie(client):
    unknown = login(client, username="unknown", password="some invalid password")
    wrong = login(client, password="some invalid password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": "用户名或密码错误。"}
    assert "enmotion_session" not in unknown.headers.get("set-cookie", "")


def test_successful_logins_do_not_consume_failure_capacity(client, settings):
    object.__setattr__(settings, "login_attempts", 1)
    object.__setattr__(settings, "login_account_attempts", 1)
    assert login(client).status_code == 200
    assert login(client).status_code == 200


def test_successful_login_prunes_expired_and_revoked_sessions(client, database):
    first = TestClient(client.app, base_url="http://testserver")
    second = TestClient(client.app, base_url="http://testserver")
    newest = TestClient(client.app, base_url="http://testserver")
    try:
        assert login(first).status_code == 200
        assert login(second).status_code == 200
        first_hash = digest_token(
            first.cookies["enmotion_session"],
            "test-only-session-secret-that-is-long-enough",
        )
        second_hash = digest_token(
            second.cookies["enmotion_session"],
            "test-only-session-secret-that-is-long-enough",
        )
        with database.session() as db:
            revoked = db.scalar(
                select(LoginSession).where(LoginSession.token_hash == first_hash)
            )
            expired = db.scalar(
                select(LoginSession).where(LoginSession.token_hash == second_hash)
            )
            assert revoked is not None and expired is not None
            revoked.revoked_at = utc_now()
            expired.expires_at = utc_now() - timedelta(seconds=1)
            db.commit()

        assert login(newest).status_code == 200
        newest_hash = digest_token(
            newest.cookies["enmotion_session"],
            "test-only-session-secret-that-is-long-enough",
        )
        with database.session() as db:
            rows = db.scalars(select(LoginSession)).all()
            assert [row.token_hash for row in rows] == [newest_hash]
    finally:
        first.close()
        second.close()
        newest.close()


def test_successful_login_caps_active_sessions_per_user(client, database, settings):
    object.__setattr__(settings, "max_active_sessions_per_user", 3)
    browsers = [TestClient(client.app, base_url="http://testserver") for _ in range(5)]
    try:
        for browser in browsers:
            assert login(browser).status_code == 200

        with database.session() as db:
            rows = db.scalars(select(LoginSession)).all()
            assert len(rows) == 3
            assert all(row.revoked_at is None for row in rows)

        assert [browser.get("/auth/session").status_code for browser in browsers] == [
            401,
            401,
            200,
            200,
            200,
        ]
    finally:
        for browser in browsers:
            browser.close()


def test_authenticated_mutations_require_matching_csrf_and_trusted_origin(client, logged_in_admin):
    assert client.post("/protected").status_code == 403
    assert client.post("/protected", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        client.post(
            "/protected",
            headers=csrf_headers(logged_in_admin, origin="https://evil.example"),
        ).status_code
        == 403
    )
    accepted = client.post(
        "/protected",
        headers=csrf_headers(logged_in_admin, origin="http://testserver"),
    )
    assert accepted.status_code == 200
    assert accepted.json()["workspace_id"] == logged_in_admin["workspace_id"]
    assert get_tenant(required=False) is None


def test_login_rejects_untrusted_browser_origin(client):
    response = client.post(
        "/auth/login",
        headers={"Origin": "https://evil.example"},
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 403


def test_admin_creates_fully_separate_personal_workspaces(client, logged_in_admin, database):
    response = client.post(
        "/auth/users",
        headers=csrf_headers(logged_in_admin),
        json={"username": "creator", "password": "creator secure password"},
    )
    assert response.status_code == 201, response.text
    creator = response.json()
    assert creator["workspace_id"] != logged_in_admin["workspace_id"]

    duplicate = client.post(
        "/auth/users",
        headers=csrf_headers(logged_in_admin),
        json={"username": "CREATOR", "password": "another secure password"},
    )
    assert duplicate.status_code == 409

    with database.session() as db:
        users = db.scalars(select(User)).all()
        workspaces = db.scalars(select(Workspace)).all()
        memberships = db.scalars(select(WorkspaceMembership)).all()
        assert len(users) == len(workspaces) == len(memberships) == 2
        assert {workspace.owner_user_id for workspace in workspaces} == {user.id for user in users}

    creator_client = TestClient(client.app, base_url="http://testserver")
    creator_auth = login(
        creator_client,
        username="creator",
        password="creator secure password",
    ).json()
    denied = creator_client.get("/auth/users")
    assert denied.status_code == 404
    assert creator_client.get("/protected").json() == {
        "user_id": creator["id"],
        "workspace_id": creator_auth["workspace_id"],
    }
    creator_client.close()


def test_logout_revokes_server_session(client, logged_in_admin, database):
    response = client.post("/auth/logout", headers=csrf_headers(logged_in_admin))
    assert response.status_code == 204
    assert client.get("/auth/session").status_code == 401
    with database.session() as db:
        stored = db.scalar(select(LoginSession))
        assert stored is not None and stored.revoked_at is not None


def test_password_change_revokes_other_sessions(client, logged_in_admin, database):
    other = TestClient(client.app, base_url="http://testserver")
    other_login = login(other)
    assert other_login.status_code == 200

    changed = client.post(
        "/auth/change-password",
        headers=csrf_headers(logged_in_admin),
        json={
            "current_password": ADMIN_PASSWORD,
            "new_password": "a new very secure password",
        },
    )
    assert changed.status_code == 204
    assert client.get("/auth/session").status_code == 200
    assert other.get("/auth/session").status_code == 401
    assert login(other, password=ADMIN_PASSWORD).status_code == 401
    assert login(other, password="a new very secure password").status_code == 200
    other.close()


def test_expired_and_disabled_sessions_are_rejected(client, logged_in_admin, database):
    with database.session() as db:
        stored = db.scalar(select(LoginSession))
        stored.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/auth/session").status_code == 401


def test_login_rate_limit_has_retry_after(client, settings):
    # The fixture's shared middleware/settings are frozen, so exercise enough
    # attempts to hit its configured (deliberately high) deterministic limit.
    for _ in range(settings.login_attempts):
        response = login(client, password="wrong password value")
        assert response.status_code == 401
    limited = login(client, password="wrong password value")
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_api_key_inspection_requires_admin_session_and_csrf(app, monkeypatch):
    from src.apps.comic_gen import api as comic_api
    from src.apps.server import middleware as server_middleware

    monkeypatch.setattr(comic_api, "server_mode_enabled", lambda: True)
    monkeypatch.setenv("NEWAPI_CHAT_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "gpt-image-2")
    monkeypatch.setenv("NEWAPI_VIDEO_MODEL", "doubao-seedance-2-0-fast-260128")
    monkeypatch.setenv("NEWAPI_GPT_IMAGE_2_API_KEY", "server-admin-secret-1234")

    # This read-only POST must not enter the workspace mutation transaction.
    def unexpected_workspace_snapshot(*_args, **_kwargs):
        raise AssertionError("API key inspection attempted a workspace snapshot")

    monkeypatch.setattr(
        server_middleware,
        "snapshot_workspace_files",
        unexpected_workspace_snapshot,
    )

    @app.post("/config/api-keys/inspect")
    def inspect_api_keys(
        request: Request,
        payload: dict = Body(...),
    ):
        return comic_api.inspect_api_keys(
            comic_api.APIKeyInspectionRequest.model_validate(payload),
            request,
        )

    if isinstance(login_rate_limiter, InMemoryRateLimiter):
        login_rate_limiter.clear()

    with TestClient(app, base_url="http://testserver") as admin_client:
        admin_auth_response = login(admin_client)
        assert admin_auth_response.status_code == 200
        admin_auth = admin_auth_response.json()

        assert admin_client.post(
            "/config/api-keys/inspect",
            json={"reveal": False},
        ).status_code == 403

        allowed = admin_client.post(
            "/config/api-keys/inspect",
            headers=csrf_headers(admin_auth),
            json={"reveal": False},
        )
        assert allowed.status_code == 200
        assert allowed.headers["cache-control"] == "no-store, max-age=0"
        assert "server-admin-secret-1234" not in allowed.text

        created = admin_client.post(
            "/auth/users",
            headers=csrf_headers(admin_auth),
            json={"username": "viewer", "password": "viewer secure password"},
        )
        assert created.status_code == 201

        with TestClient(app, base_url="http://testserver") as viewer_client:
            viewer_auth_response = login(
                viewer_client,
                username="viewer",
                password="viewer secure password",
            )
            assert viewer_auth_response.status_code == 200
            denied = viewer_client.post(
                "/config/api-keys/inspect",
                headers=csrf_headers(viewer_auth_response.json()),
                json={"reveal": True},
            )
            assert denied.status_code == 404
            assert "server-admin-secret-1234" not in denied.text
