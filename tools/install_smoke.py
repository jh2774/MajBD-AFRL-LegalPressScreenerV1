"""Boot an *installed* foci-screen the way the Docker images do, and probe it.

    python -m venv /tmp/v && /tmp/v/bin/pip install ".[api,queue,postgres]"
    /tmp/v/bin/python tools/install_smoke.py

A pre-deploy check for when a container build is not available. Run it with the
interpreter of an environment where the package was installed normally (not
`pip install -e`), so it exercises what a wheel actually ships rather than the
working copy. It runs from a temporary directory for the same reason.

It checks what most often breaks between "tests pass" and "the image boots":
web assets missing from the package, extras that do not exist, entrypoints that
do not import, the Render health check, auth failing closed, and the scheduler
failing its cron run (exit 2) when there is no queue. It does not exercise the
OS layer — system libraries, Chromium, file permissions — which only a real
image build can.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

work = pathlib.Path(tempfile.mkdtemp(prefix="foci-smoke-"))
os.chdir(work)  # nothing importable from the source tree
env = {**os.environ,
       "FOCI_API_KEYS": "default:smoke-key",
       "FOCI_DB": str(work / "smoke.db"),
       "FOCI_CACHE": str(work / "cache"),
       "FOCI_OUT": str(work / "out"),
       "FOCI_USER_AGENT": "foci-screen smoke test (ops@example.org)",
       "PORT": "8099"}
for var in ("DATABASE_URL", "REDIS_URL"):
    env.pop(var, None)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def get(path, key=None):
    req = urllib.request.Request(f"http://127.0.0.1:8099{path}")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


# Same command as the API image's CMD.
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "foci_screen.api.app:app",
     "--host", "127.0.0.1", "--port", "8099", "--workers", "1"],
    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    for _ in range(60):
        try:
            if get("/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.5)
    status, body, _ = get("/health")
    payload = json.loads(body or b"{}")
    # The version comes from installed package metadata, so "unknown" means the
    # package was not really installed and a stale value means a stale install —
    # either way you would be looking at a build you did not think you shipped.
    check("GET /health (Render healthCheckPath) reports a version",
          status == 200 and payload.get("version") not in (None, "unknown"),
          body[:140].decode())
    # The field is how a misconfigured deployment announces itself; a sound
    # local install must produce an empty list, or the banner cries wolf.
    check("GET /health carries configuration warnings, and has none here",
          payload.get("warnings") == [],
          repr(payload.get("warnings")))

    status, body, _ = get("/")
    check("GET / serves the web UI from site-packages",
          status == 200 and b"foci-screen" in body)
    for asset in ("/app.js", "/styles.css"):
        status, body, headers = get(asset)
        check(f"GET {asset}", status == 200 and len(body) > 1000,
              headers.get("content-type", ""))

    status, _, _ = get("/v1/overview")
    check("authenticated route refuses without a key", status == 401, str(status))
    status, body, _ = get("/v1/overview", key="smoke-key")
    check("authenticated route works with a key", status == 200, body[:80].decode())
    status, body, _ = get("/v1/search?q=anything", key="smoke-key")
    check("search against a fresh database", status == 200, body[:80].decode())
finally:
    server.terminate()
    try:
        out, _ = server.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        out, _ = server.communicate()
    tracebacks = [ln for ln in out.splitlines() if "Traceback" in ln or "ERROR" in ln]
    check("server log free of tracebacks", not tracebacks, "; ".join(tracebacks[:3]))

# Worker image CMD and scheduler cron command: must at least import cleanly.
for module in ("foci_screen.worker", "foci_screen.scheduler", "foci_screen.jobs"):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"],
                          env=env, capture_output=True, text=True)
    check(f"import {module}", proc.returncode == 0, proc.stderr.strip()[-200:])

# Scheduler with no Redis: prune runs, then the sweep refuses and the process
# exits 2 so a cron platform marks the run failed rather than green.
proc = subprocess.run([sys.executable, "-m", "foci_screen.scheduler"],
                      env=env, capture_output=True, text=True, timeout=120)
output = proc.stdout + proc.stderr
check("scheduler without Redis fails the cron run (exit 2), after pruning",
      proc.returncode == 2 and "pruned" in output and "Traceback" not in output,
      f"exit {proc.returncode}")

try:
    import psycopg  # noqa: E402  (postgres extra)
    check("psycopg importable (postgres extra)", True, psycopg.__version__)
except ImportError as exc:
    check("psycopg importable (postgres extra)", False, str(exc))

scripts = pathlib.Path(sys.executable).parent
for exe in ("foci-screen", "foci-worker", "foci-scheduler"):
    found = any((scripts / f"{exe}{ext}").exists() for ext in ("", ".exe"))
    check(f"console script {exe}", found)

width = max(len(n) for n, _, _ in results)
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
print(json.dumps({"passed": sum(ok for _, ok, _ in results), "total": len(results)}))
shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
