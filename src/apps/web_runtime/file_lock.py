"""Small re-entrant inter-process lock used by workspace JSON stores.

EnMotion's existing project format is a collection of JSON documents and media
files.  Server mode runs an API process and a worker process, so the original
thread-only locks are not enough: two processes could otherwise replace the
same JSON file at the same time.  This module deliberately uses only the
standard library and degrades to a process-local lock on platforms without
``fcntl`` (the supported Mac and Linux server targets both provide it).
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Iterator

try:  # pragma: no cover - exercised on the supported macOS/Linux targets
    import fcntl
except ImportError:  # pragma: no cover - defensive Windows fallback
    fcntl = None


class _LockState:
    def __init__(self) -> None:
        self.thread_lock = threading.RLock()
        self.local = threading.local()


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, _LockState] = {}
_EXTERNALLY_HELD_LOCKS: ContextVar[frozenset[str]] = ContextVar(
    "enmotion_externally_held_locks", default=frozenset()
)
_NONBLOCKING_READ_LOCKS: ContextVar[frozenset[str]] = ContextVar(
    "enmotion_nonblocking_read_locks", default=frozenset()
)


def _state_for(path: str) -> _LockState:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, _LockState())


@contextmanager
def interprocess_lock(path: str | Path) -> Iterator[None]:
    """Hold a named lock across threads and processes.

    Calls from the same thread are re-entrant.  That matters when a playground
    operation saves an output and then registers it in the workspace library;
    both storage layers intentionally use the same workspace lock.
    """

    canonical = str(Path(path).expanduser().resolve())
    if canonical in _EXTERNALLY_HELD_LOCKS.get() or canonical in _NONBLOCKING_READ_LOCKS.get():
        yield
        return
    state = _state_for(canonical)
    with state.thread_lock:
        depth = int(getattr(state.local, "depth", 0))
        if depth:
            state.local.depth = depth + 1
            try:
                yield
            finally:
                state.local.depth -= 1
            return

        Path(canonical).parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(canonical, os.O_CREAT | os.O_RDWR, 0o600)
        state.local.depth = 1
        state.local.descriptor = descriptor
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            state.local.depth = 0
            state.local.descriptor = None


def acquire_lock_file(path: str | Path) -> tuple[int, str]:
    """Acquire only the OS lock and return its descriptor and canonical path.

    The auth middleware calls this through ``asyncio.to_thread`` so waiting for
    a busy workspace never blocks the ASGI event loop.
    """

    canonical = str(Path(path).expanduser().resolve())
    Path(canonical).parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(canonical, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, canonical


def release_lock_file(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def bind_external_lock(path: str | Path) -> Token[frozenset[str]]:
    canonical = str(Path(path).expanduser().resolve())
    return _EXTERNALLY_HELD_LOCKS.set(_EXTERNALLY_HELD_LOCKS.get() | {canonical})


def reset_external_lock(token: Token[frozenset[str]]) -> None:
    _EXTERNALLY_HELD_LOCKS.reset(token)


def bind_nonblocking_read(path: str | Path) -> Token[frozenset[str]]:
    """Let one request read an atomic workspace snapshot without waiting.

    Workspace metadata writers persist through a temporary file followed by
    ``os.replace``. A reader therefore sees either the previous complete JSON
    document or the next complete document, never a partially written file.
    Server-mode GET/HEAD requests use this context so a long-running provider
    call cannot make unrelated workspace, library, or Playground reads hang.
    """

    canonical = str(Path(path).expanduser().resolve())
    return _NONBLOCKING_READ_LOCKS.set(_NONBLOCKING_READ_LOCKS.get() | {canonical})


def reset_nonblocking_read(token: Token[frozenset[str]]) -> None:
    _NONBLOCKING_READ_LOCKS.reset(token)


def nonblocking_read_active(path: str | Path) -> bool:
    """Return whether the current request is bound to an immutable read path."""

    canonical = str(Path(path).expanduser().resolve())
    return canonical in _NONBLOCKING_READ_LOCKS.get()
