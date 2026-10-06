# API and scheduler. No browser here — see Dockerfile.worker for that.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY foci_screen ./foci_screen
RUN pip install --no-cache-dir ".[api,queue,postgres]"

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# The host says which port to listen on: Render and Cloud Run both set PORT
# (Cloud Run to 8080). 8000 is only the default for running the image by hand.
ENV PORT=8000
EXPOSE 8000

# Single worker: Store holds one lock-guarded connection per process, and the
# in-process pieces — screens run as threads where there is no worker service,
# the alert check's lock — assume one process. Run one instance of this image.
#
# --forwarded-allow-ips: the container is only ever reached through the host's
# own proxy, which terminates HTTPS. Trusting its X-Forwarded-* headers is what
# lets the app know its real public address, which it puts in alert emails and
# uses to keep itself awake during a screen. Without it, it sees http://.
CMD uvicorn foci_screen.api.app:app --host 0.0.0.0 --port ${PORT} --workers 1 \
    --proxy-headers --forwarded-allow-ips '*'
