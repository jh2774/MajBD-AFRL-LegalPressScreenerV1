"""Nightly watchlist sweep. Entry point for a cron job.

    python -m foci_screen.scheduler

Enqueues one screen per active watchlist, across every tenant, then exits. It
does not wait for the work — the queue owns that — so the cron job's own
runtime stays in seconds.

This is where the change-detection design pays for itself. After the baseline
run, most nights produce no findings at all, and that is the correct output: the
tool reports what moved, and on most nights nothing did.
"""
from __future__ import annotations

import json
import logging
import sys

from .config import get_config
from .jobs import JobQueue
from .store import DEFAULT_TENANT, Store

log = logging.getLogger("foci.scheduler")

# Revisions of a document to keep the text of. Enough to answer "what changed
# recently"; not so many that a page rewritten daily accumulates a year of
# 40KB bodies. The hashes are kept regardless, so the fact that a revision
# existed survives even when its text does not.
RETAIN_REVISIONS = 20


class QueueUnavailable(RuntimeError):
    """A sweep was asked for but there is no queue that would outlive the process."""


def prune() -> dict:
    """Drop revision text beyond the retention window. Safe to run any time."""
    cfg = get_config()
    store = Store(cfg.dsn, tenant_id=DEFAULT_TENANT)
    try:
        stats = store.prune_snapshot_bodies(keep_per_document=RETAIN_REVISIONS)
        log.info("pruned %s history row(s); %s bodies retained",
                 stats["history_rows_removed"], stats["bodies_remaining"])
        return stats
    finally:
        store.close()


def sweep() -> int:
    """Enqueue every active watchlist. Returns the number enqueued.

    Raises `QueueUnavailable` rather than returning 0 when there is no queue:
    "nothing to do tonight" and "cannot do anything at all" must not look the
    same to whoever reads the cron history.
    """
    cfg = get_config()
    queue = JobQueue(cfg)

    if queue.backend == "thread":
        # Threads die with this process, and this process exits in a second.
        raise QueueUnavailable(
            "No REDIS_URL configured. A scheduled sweep needs a real queue and a "
            "worker to run the jobs; nothing would survive this process exiting.")

    admin = Store(cfg.dsn, tenant_id=DEFAULT_TENANT)
    try:
        watchlists = admin.all_active_watchlists()
        if not watchlists:
            log.info("no active watchlists")
            return 0

        enqueued = 0
        for row in watchlists:
            tenant = row["tenant_id"]
            try:
                options = json.loads(row["params"])
            except (ValueError, TypeError):
                log.warning("watchlist %s has unreadable params — skipping",
                            row["watchlist_id"])
                continue

            store = Store(cfg.dsn, tenant_id=tenant)
            try:
                run_id = store.start_run(options.get("agency", ""), options,
                                         status="queued")
                store.mark_watchlist_run(row["watchlist_id"], run_id)
                queue.enqueue(tenant, run_id, options)
                enqueued += 1
                log.info("queued %s for watchlist '%s' (tenant %s)",
                         run_id, row["name"], tenant)
            finally:
                store.close()
        return enqueued
    finally:
        admin.close()


EXIT_QUEUE_UNAVAILABLE = 2


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Pruning does not need a queue; do it even when there is nothing to enqueue.
    stats = prune()
    try:
        count = sweep()
    except QueueUnavailable as exc:
        # Non-zero so the cron run is marked failed. A green run that screened
        # nothing is how a misconfigured deploy goes unnoticed for weeks.
        log.error("%s Refusing to enqueue.", exc)
        print(f"sweep FAILED: queue unavailable; "
              f"pruned {stats['history_rows_removed']} old revision(s)")
        return EXIT_QUEUE_UNAVAILABLE
    print(f"enqueued {count} screen(s); "
          f"pruned {stats['history_rows_removed']} old revision(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
