"""CI only: make two databases and put rows in the first, for the copy step.

OLD and NEW are set by the workflow. The application's own Store creates the
schema and the rows, so what the copy step moves is what a real deployment
would hold.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg

from foci_screen.store import Store

old, new = os.environ["OLD"], os.environ["NEW"]
server = urlunsplit(urlsplit(old)._replace(path="/foci"))

with psycopg.connect(server, autocommit=True) as conn:
    for dsn in (old, new):
        name = urlsplit(dsn).path.lstrip("/")
        conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {name}")

store = Store(old, tenant_id="default")
store.start_run("DoD", {}, run_id="r1")
store.save_adv_snapshot("999001", {"crd": "999001", "name": "HARBORLIGHT"}, "03/31/2026")
store.set_entity_link("UEI777", status="confirmed", cik="0004400123", decided_by="ci")
store.close()
print("seeded", name)
