"""Screening as a background job.

A screen takes minutes — it walks USAspending, FPDS, EDGAR, OFAC and a handful
of contractor websites, all of them rate-limited on purpose. Holding an HTTP
connection open for that is the single thing that stops the CLI design from
being a service, so the API creates a run row, hands the work to a queue, and
returns a `run_id` immediately.

Two backends, chosen by whether `REDIS_URL` is set:

  * **RQ.** The real one. Work survives a web-process restart, and the worker
    scales independently of the API — which matters because the worker is the
    process carrying a headless browser.
  * **Thread.** Local development and the CLI. No Redis, no worker to run, and
    a job dies with the process. Fine when the process is a terminal someone
    is watching; not fine in production, which is why a deployment sets Redis.

The job function is module-level and takes only JSON-serialisable arguments so
RQ can address it by import path.
"""
from __future__ import annotations

import logging
import threading
import traceback

from .config import Config, get_config
from .connectors.browser import BrowserRenderer
from .httpclient import HttpClient
from .notify import render as render_notice
from .pipeline import Screener, ScreenOptions
from .policy import ScreeningPolicy, decide_notice
from .store import Store

log = logging.getLogger("foci.jobs")

# The default notice threshold. A tenant's screening policy may move it; see
# policy.py. Findings below it remain queryable — the point of the change feed
# is that most nights are quiet — but nobody is asked to review them.
NOTICE_THRESHOLD = "medium"
SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def severity_at_least(severity: str, floor: str) -> bool:
    try:
        return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(floor)
    except ValueError:
        return False


def build_screener(cfg: Config, store: Store) -> tuple[Screener, BrowserRenderer]:
    """Assemble a screener with a headless browser attached."""
    http = HttpClient(cfg)
    browser = BrowserRenderer(timeout=cfg.browser_timeout, enabled=cfg.browser_enabled,
                              identity=cfg.user_agent)
    return Screener(cfg, http, store, browser=browser), browser


# --------------------------------------------------------------------- the job

def run_screen_job(tenant_id: str, run_id: str, options: dict) -> dict:
    """Execute one screen. Called by an RQ worker or a local thread."""
    cfg = get_config()
    store = Store(cfg.dsn, tenant_id=tenant_id)
    screener, browser = build_screener(cfg, store)

    def progress(msg: str) -> None:
        text = msg.strip()
        if text:
            store.set_run_progress(run_id, text)

    try:
        store.mark_running(run_id)
        opts = ScreenOptions(**options)
        result = screener.run(opts, progress=progress, run_id=run_id)

        notices, held = raise_notices(store, result.findings, run_id)

        stats = dict(result.stats)
        stats["findings"] = len(result.findings)
        stats["notices_pending"] = len(notices)
        # Why the rest did not become notices. A quiet queue after a screen is
        # the intended outcome most nights, and this is how anyone can tell
        # "nothing new" from "something is misconfigured".
        stats["notices_held"] = held
        stats["notes"] = result.notes
        store.finish_run(run_id, status="complete", stats=stats)
        return {"run_id": run_id, "findings": len(result.findings),
                "notices": len(notices)}
    except Exception as exc:
        log.exception("screen %s failed", run_id)
        store.finish_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}",
                         stats={"traceback": traceback.format_exc()[-2000:]})
        raise
    finally:
        browser.close()
        store.close()


def raise_notices(store: Store, findings, run_id: str) -> tuple[list[str], dict]:
    """Decide which findings become draft notices, under the tenant's policy.

    Returns the new notice ids and a count of why the others were held back.
    """
    policy, _ = ScreeningPolicy.from_dict(store.screening_policy())
    raised: list[str] = []
    held: dict[str, int] = {}

    for finding in findings:
        decision = decide_notice(
            finding.severity, finding.signals, policy,
            already_notified=store.notified_signal_ids(finding.entity.key()))
        if not decision.raise_notice:
            held[decision.reason] = held.get(decision.reason, 0) + 1
            continue
        notice_id = queue_notice(store, finding, run_id,
                                 signal_ids=decision.signal_ids,
                                 trigger_reason=decision.reason)
        store.supersede_pending(finding.entity.key(), by_notice_id=notice_id)
        raised.append(notice_id)
    return raised, held


def queue_notice(store: Store, finding, run_id: str, *,
                 signal_ids: list[str] | None = None, trigger_reason: str = "") -> str:
    """Persist a notice in `pending` — nothing is addressed without a human."""
    officer = finding.top_officer()
    return store.create_notice(
        run_id=run_id,
        entity_key=finding.entity.key(),
        entity_name=finding.entity.name,
        severity=finding.severity,
        recipient=officer.email,
        officer_confidence=officer.confidence if officer.is_addressable else "unresolved",
        subject=render_notice.subject_for(finding),
        body_text=render_notice.render_text(finding, run_id=run_id),
        status="pending",
        # Every signal in the finding is recorded as covered, not only the
        # ones that tipped it over: the draft describes all of them, so all of
        # them have been put in front of a reviewer.
        signal_ids=sorted({s.key() for s in finding.signals} | set(signal_ids or [])),
        trigger_reason=trigger_reason,
    )


# ------------------------------------------------------------------- dispatch

class JobQueue:
    """Enqueues screens onto RQ, or onto a thread when Redis is absent."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._queue = None
        if cfg.redis_url:
            try:
                from redis import Redis
                from rq import Queue

                self._queue = Queue("screens", connection=Redis.from_url(cfg.redis_url),
                                    default_timeout=cfg.job_timeout)
                log.info("job queue: RQ via Redis")
            except Exception as exc:
                # Failing over to threads silently would be worse than loud
                # degradation: jobs would start dying with the web process.
                log.error("REDIS_URL is set but the queue could not be created "
                          "(%s) — falling back to in-process threads", exc)
        else:
            log.info("job queue: in-process threads (no REDIS_URL)")

    @property
    def backend(self) -> str:
        return "rq" if self._queue is not None else "thread"

    def worker_count(self) -> int | None:
        """Workers listening on this queue, or None when it cannot be asked.

        A queue with no worker is the one arrangement that is strictly worse
        than having no queue at all: screens are accepted, they queue, and
        nothing ever runs them — while the service reports a real queue and
        looks healthier than the state it replaced.

        None means the question could not be answered (no queue configured, or
        Redis did not respond) and is deliberately not reported as zero.
        """
        if self._queue is None:
            return None
        try:
            from rq import Worker

            return Worker.count(queue=self._queue)
        except Exception as exc:       # noqa: BLE001 - diagnostics must not raise
            log.debug("could not count workers: %s", exc)
            return None

    def enqueue(self, tenant_id: str, run_id: str, options: dict) -> None:
        if self._queue is not None:
            self._queue.enqueue(run_screen_job, tenant_id, run_id, options,
                                job_id=run_id, job_timeout=self.cfg.job_timeout)
            return
        threading.Thread(
            target=_thread_target, args=(tenant_id, run_id, options),
            name=f"screen-{run_id}", daemon=True).start()


def _thread_target(tenant_id: str, run_id: str, options: dict) -> None:
    try:
        run_screen_job(tenant_id, run_id, options)
    except Exception:
        # Already recorded on the run row by run_screen_job; this keeps a dead
        # thread from printing an unhandled-exception traceback to the log.
        log.debug("threaded screen %s ended in failure", run_id)
