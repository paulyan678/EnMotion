"""Request-local progress reporting for durable generation workers.

Provider adapters are also used by desktop mode and tests, so they cannot
depend directly on the server database.  A ContextVar keeps the adapter API
small: server workers bind a durable callback while all other callers get a
safe no-op.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Callable

ProgressReporter = Callable[[str, str | None, int | None, bool], None]

_reporter: ContextVar[ProgressReporter | None] = ContextVar(
    "enmotion_generation_progress_reporter",
    default=None,
)


def bind_generation_progress(reporter: ProgressReporter) -> Token:
    return _reporter.set(reporter)


def reset_generation_progress(token: Token) -> None:
    _reporter.reset(token)


def report_generation_progress(
    stage: str,
    message: str | None = None,
    percent: int | float | None = None,
    *,
    estimated: bool = True,
) -> None:
    """Report a real workflow transition or provider percentage.

    Callers should invoke this only after reaching the named stage.  Percent is
    optional; omitting it keeps the dashboard indeterminate rather than
    inventing elapsed-time progress.
    """

    reporter = _reporter.get()
    if reporter is None:
        return
    normalized: int | None
    if percent is None:
        normalized = None
    else:
        numeric = float(percent)
        if 0 <= numeric <= 1:
            numeric *= 100
        normalized = max(0, min(100, round(numeric)))
    reporter(stage, message, normalized, estimated)
