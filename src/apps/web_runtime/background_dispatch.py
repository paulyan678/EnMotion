"""Detached daemon workers for long-running desktop media jobs.

FastAPI ``BackgroundTasks`` still run as part of the response lifecycle.  A
multi-minute provider call therefore keeps the loopback connection occupied
and can make the next WebView submission appear to hang before it is recorded.
These workers own the provider phase independently so the HTTP response can
finish as soon as durable task state has been written.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .context import RequestTenant, bind_tenant, get_tenant, reset_tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _WorkItem:
    function: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    tenant: RequestTenant | None


class DetachedTaskDispatcher:
    """Run submitted callables on a fixed set of daemon worker threads.

    The queue is intentionally unbounded: every accepted item already has a
    durable task record, and rejecting a locally queued item after persisting
    that record would strand it.  Fixed workers bound active provider calls;
    process restart recovery turns any remaining durable tasks into retryable
    failures.
    """

    def __init__(self, *, worker_count: int = 4, name_prefix: str = "enmotion-media"):
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        self._queue: queue.Queue[_WorkItem] = queue.Queue()
        self._active = 0
        self._state_lock = threading.Lock()
        self._idle = threading.Condition(self._state_lock)
        self._workers = [
            threading.Thread(
                target=self._worker,
                name=f"{name_prefix}-{index + 1}",
                daemon=True,
            )
            for index in range(worker_count)
        ]
        for worker in self._workers:
            worker.start()

    def submit(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
        """Queue one callable and return without waiting for provider work.

        Manual worker threads do not inherit ``ContextVar`` values. Capture the
        authenticated tenant explicitly so provider gateway credentials and
        workspace-scoped paths remain available after the HTTP request exits.
        Other request-local context (notably lock ownership) is deliberately
        not copied into the detached worker.
        """

        if not callable(function):
            raise TypeError("function must be callable")
        self._queue.put_nowait(
            _WorkItem(
                function=function,
                args=args,
                kwargs=kwargs,
                tenant=get_tenant(required=False),
            )
        )

    @property
    def pending_count(self) -> int:
        """Return queued plus actively running work for diagnostics and tests."""

        with self._state_lock:
            return self._queue.qsize() + self._active

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        """Wait for tests/diagnostics; production request handlers never call this."""

        with self._idle:
            return self._idle.wait_for(
                lambda: self._active == 0 and self._queue.empty(),
                timeout=timeout,
            )

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            with self._idle:
                self._active += 1
            tenant_token = None
            try:
                if item.tenant is not None:
                    tenant_token = bind_tenant(
                        item.tenant.user_id,
                        item.tenant.workspace_id,
                        item.tenant.role,
                    )
                item.function(*item.args, **item.kwargs)
            except Exception:
                logger.exception(
                    "Detached media task crashed callable=%s",
                    getattr(item.function, "__qualname__", repr(item.function)),
                )
            finally:
                if tenant_token is not None:
                    reset_tenant(tenant_token)
                self._queue.task_done()
                with self._idle:
                    self._active -= 1
                    if self._active == 0 and self._queue.empty():
                        self._idle.notify_all()
