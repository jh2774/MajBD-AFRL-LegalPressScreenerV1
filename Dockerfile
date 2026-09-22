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

ENV PORT=8000
EXPOSE 8000

# Single worker: Store holds one lock-guarded connection per process, and
# screening happens in the worker service, not here.
CMD uvicorn foci_screen.api.app:app --host 0.0.0.0 --port ${PORT} --workers 1
