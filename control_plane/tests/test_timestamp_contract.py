from datetime import datetime, timezone

from app.schemas import UsagePublic, UserPublic


def test_response_models_serialize_sqlite_naive_datetimes_as_utc():
    naive = datetime(2026, 7, 30, 0, 0, 0)
    aware = datetime(2026, 7, 30, 0, 0, 0, tzinfo=timezone.utc)

    user = UserPublic(
        id="user-1",
        username="tester",
        role="user",
        active=True,
        available_credits=10,
        reserved_credits=0,
        created_at=naive,
        updated_at=aware,
    )
    usage = UsagePublic(
        id="usage-1",
        idempotency_key="request-1234",
        operation="images.generations",
        model="gpt-image-2",
        status="settled",
        reserved_units=1,
        settled_units=1,
        upstream_task_id=None,
        upstream_status=200,
        error_code=None,
        created_at=naive,
        updated_at=aware,
        settled_at=naive,
    )

    assert '"created_at":"2026-07-30T00:00:00Z"' in user.model_dump_json()
    assert '"updated_at":"2026-07-30T00:00:00Z"' in user.model_dump_json()
    assert '"created_at":"2026-07-30T00:00:00Z"' in usage.model_dump_json()
    assert '"settled_at":"2026-07-30T00:00:00Z"' in usage.model_dump_json()
