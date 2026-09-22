"""RQ worker. Entry point for the background service.

    python -m foci_screen.worker

Runs screens off the queue. This is the process that carries the headless
browser, which is the reason it is worth separating from the web service at
all: Chromium's memory ceiling should not decide how many API requests can be
served concurrently.
"""
from __future__ import annotations

import logging
import sys

from .config import get_config


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = get_config()
    if not cfg.redis_url:
        print("REDIS_URL is not set — there is no queue to consume.",
              file=sys.stderr)
        return 1

    from redis import Redis
    from rq import Queue, Worker

    connection = Redis.from_url(cfg.redis_url)
    queue = Queue("screens", connection=connection)
    Worker([queue], connection=connection).work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
