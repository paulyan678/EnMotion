"""Entry point for the single-concurrency durable generation worker."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from sqlalchemy import func, select

from ..web_runtime.media_derivatives import (
    backfill_referenced_image_derivatives,
)
from ..web_runtime.workspace_snapshot import workspace_ids_with_metadata
from .database import get_database
from .jobs import (
    QUEUE_NAME,
    celery_app,
    compact_terminal_jobs,
    reconcile_terminal_job_outbox,
    recover_interrupted_jobs,
    recover_stale_reservations,
    republish_queued_jobs,
    republish_unconfirmed_jobs,
)
from .models import GenerationJob

logger = logging.getLogger(__name__)


def _start_queue_reconciler() -> None:
    try:
        interval = max(5, int(os.getenv("ENMOTION_QUEUE_RECONCILE_INTERVAL_SECONDS", "30")))
    except ValueError:
        interval = 30

    wake = threading.Event()
    last_derivative_maintenance = 0.0

    def maintain_derivatives() -> None:
        nonlocal last_derivative_maintenance
        try:
            maintenance_interval = max(
                30,
                int(
                    os.getenv(
                        "ENMOTION_DERIVATIVE_MAINTENANCE_SECONDS",
                        "60",
                    )
                ),
            )
        except ValueError:
            maintenance_interval = 60
        now = time.monotonic()
        if now - last_derivative_maintenance < maintenance_interval:
            return
        last_derivative_maintenance = now
        database = get_database()
        with database.session() as session:
            active = int(
                session.scalar(
                    select(func.count())
                    .select_from(GenerationJob)
                    .where(GenerationJob.status.in_(("queued", "running")))
                )
                or 0
            )
        if active:
            return
        try:
            batch = max(
                1,
                min(
                    50,
                    int(os.getenv("ENMOTION_DERIVATIVE_MAINTENANCE_BATCH", "16")),
                ),
            )
        except ValueError:
            batch = 16
        workspace_root = (
            Path(os.getenv("ENMOTION_WORKSPACE_ROOT", "data/workspaces")).expanduser().resolve()
        )
        processed = ready = failed = 0
        remaining_batch = batch
        for workspace_id in workspace_ids_with_metadata(workspace_root):
            if remaining_batch <= 0:
                break
            result = backfill_referenced_image_derivatives(
                workspace_root / workspace_id / "output",
                limit=remaining_batch,
            )
            processed += result["processed"]
            ready += result["ready"]
            failed += result["failed"]
            remaining_batch -= result["processed"]
        if processed:
            logger.info(
                "Image derivative maintenance processed=%s ready=%s failed=%s",
                processed,
                ready,
                failed,
            )

    def reconcile() -> None:
        while True:
            wake.wait(interval)
            try:
                reconcile_terminal_job_outbox()
                recover_stale_reservations()
                republish_unconfirmed_jobs()
                compact_terminal_jobs()
                maintain_derivatives()
            except Exception:
                logger.exception("Durable queue reconciliation failed; will retry")

    threading.Thread(
        target=reconcile,
        name="enmotion-queue-reconciler",
        daemon=True,
    ).start()


def main() -> int:
    recover_stale_reservations()
    # A provider handler may have completed just as PostgreSQL restarted. Apply
    # its persistent terminal intent before classifying any remaining running
    # row as an interrupted provider call.
    reconcile_terminal_job_outbox()
    # With one worker process, every `running` row belongs to a worker that no
    # longer exists at startup. Failing it avoids silently repeating a request
    # that may already have charged an external AI provider.
    recover_interrupted_jobs()
    compact_terminal_jobs()
    # Redis is deliberately excluded from portable backups. Recreate its
    # transient queue from the PostgreSQL source of truth after every start.
    republish_queued_jobs()
    # Heal API crashes around reserve/publish without requiring a worker
    # restart. Confirmed queued work is not periodically duplicated.
    _start_queue_reconciler()
    concurrency = os.getenv("ENMOTION_WORKER_CONCURRENCY", "1")
    celery_app.worker_main(
        [
            "worker",
            "--loglevel",
            os.getenv("ENMOTION_WORKER_LOG_LEVEL", "INFO"),
            "--pool",
            "solo",
            "--concurrency",
            concurrency,
            "--queues",
            QUEUE_NAME,
            "--hostname",
            "enmotion-worker@%h",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
