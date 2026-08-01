from __future__ import annotations

import threading

from src.apps.web_runtime.background_dispatch import DetachedTaskDispatcher
from src.apps.web_runtime.context import bind_tenant, get_tenant, reset_tenant


def test_dispatcher_accepts_followup_work_while_provider_call_is_blocked() -> None:
    dispatcher = DetachedTaskDispatcher(worker_count=1, name_prefix="dispatch-test")
    first_started = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()

    def first() -> None:
        first_started.set()
        assert release_first.wait(timeout=2)

    dispatcher.submit(first)
    assert first_started.wait(timeout=1)

    # This is the loopback-UI regression guard: submission itself returns even
    # though the only provider worker is still occupied by the first request.
    dispatcher.submit(second_finished.set)
    assert not second_finished.is_set()
    assert dispatcher.pending_count == 2

    release_first.set()
    assert dispatcher.wait_for_idle(timeout=2)
    assert second_finished.is_set()


def test_dispatcher_survives_one_crashing_task() -> None:
    dispatcher = DetachedTaskDispatcher(worker_count=1, name_prefix="dispatch-crash-test")
    completed = threading.Event()

    def crash() -> None:
        raise RuntimeError("expected test failure")

    dispatcher.submit(crash)
    dispatcher.submit(completed.set)

    assert dispatcher.wait_for_idle(timeout=2)
    assert completed.is_set()
    assert all(worker.daemon for worker in dispatcher._workers)


def test_dispatcher_propagates_tenant_without_leaking_it_between_jobs() -> None:
    dispatcher = DetachedTaskDispatcher(worker_count=1, name_prefix="dispatch-tenant-test")
    observed: list[tuple[str, str, str] | None] = []

    def capture_tenant() -> None:
        tenant = get_tenant(required=False)
        observed.append(
            None
            if tenant is None
            else (tenant.user_id, tenant.workspace_id, tenant.role)
        )

    token = bind_tenant("user-a", "workspace-a", "admin")
    try:
        dispatcher.submit(capture_tenant)
    finally:
        reset_tenant(token)

    # The next task is submitted outside any request. The worker must not
    # retain the previous tenant after finishing the first item.
    dispatcher.submit(capture_tenant)

    assert dispatcher.wait_for_idle(timeout=2)
    assert observed == [("user-a", "workspace-a", "admin"), None]
