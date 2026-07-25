from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from scripts.performance import seed_asset_library
from src.apps.server.models import LoginSession, User, Workspace, utc_now
from src.apps.server.service import create_user_with_personal_workspace


def test_cleanup_removes_active_sessions_before_fixture_workspace(
    database,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(seed_asset_library, "get_database", lambda: database)
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path))
    username = "enmotion-perf-cleanup-test"

    with database.session() as session:
        user, workspace = create_user_with_personal_workspace(
            session,
            username=username,
            password="fixture password long enough",
            workspace_name=f"{seed_asset_library.WORKSPACE_PREFIX} cleanup",
        )
        session.add(
            LoginSession(
                user_id=user.id,
                workspace_id=workspace.id,
                token_hash="a" * 64,
                csrf_hash="b" * 64,
                expires_at=utc_now() + timedelta(hours=1),
                last_seen_at=utc_now(),
            )
        )
        session.commit()
        workspace_id = workspace.id

    workspace_root = tmp_path / workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "fixture.txt").write_text("synthetic", encoding="utf-8")

    assert seed_asset_library._cleanup(username) == {
        "removed_users": 1,
        "removed_workspaces": 1,
    }
    assert not workspace_root.exists()

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0
        assert session.scalar(select(func.count()).select_from(Workspace)) == 0
        assert session.scalar(select(func.count()).select_from(User)) == 0
