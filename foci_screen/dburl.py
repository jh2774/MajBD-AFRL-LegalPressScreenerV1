"""Which Postgres address to use, and in what shape.

Hosts hand the database address over in different ways, and two of them need
correcting before this application can use it.

**Supabase.** It offers three ways in, on two hosts:

  * the direct connection, `db.<project>.supabase.co:5432` — IPv6 only on
    every plan without a paid add-on, and Vercel cannot reach IPv6;
  * its pooler (Supavisor) in *transaction* mode, `…pooler.supabase.com:6543`,
    which hands each transaction to whichever server connection is free.
    That breaks the three things here that belong to one connection: the
    lock that stops two alert checks running at once, prepared statements,
    and the `search_path` set below;
  * the same pooler in *session* mode, port 5432 on the same host — one
    server connection per client, IPv4. That is the one used.

Vercel's Supabase integration sets `POSTGRES_URL` (the pooler, transaction
mode, with query parameters of its own) and `POSTGRES_URL_NON_POOLING`, which
has been the direct, IPv6-only host. So whatever address is chosen, a
Supabase pooler address among those given is moved to port 5432, and a direct
address is swapped for the pooler's when there is one.

**Supabase's Data API** publishes tables in the `public` schema through its
REST endpoints, to anyone holding the project's public key, unless row-level
security is set up for each one. This application's tables are not meant for
that, so on Supabase they are kept in a schema of their own, `foci`, which the
Data API does not publish. `FOCI_DB_SCHEMA` chooses another; an empty value
keeps everything in `public`, as on any other Postgres.

**Unknown query parameters.** libpq refuses a connection string carrying one
it does not know — `supa=…`, `pgbouncer=true` and `workaround=…` all appear
in addresses meant for other drivers — so those are dropped.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# The connection-string parameters libpq accepts that can usefully appear in a
# URL. Anything else in the query string is another driver's and is dropped.
LIBPQ_PARAMS = {
    "host", "hostaddr", "port", "dbname", "user", "password", "passfile",
    "channel_binding", "connect_timeout", "client_encoding", "options",
    "application_name", "fallback_application_name", "keepalives",
    "keepalives_idle", "keepalives_interval", "keepalives_count",
    "tcp_user_timeout", "replication", "gssencmode", "sslmode", "sslnegotiation",
    "sslcompression", "sslcert", "sslkey", "sslpassword", "sslcertmode",
    "sslrootcert", "sslcrl", "sslcrldir", "sslsni", "requirepeer",
    "ssl_min_protocol_version", "ssl_max_protocol_version", "krbsrvname",
    "gsslib", "gssdelegation", "service", "target_session_attrs", "load_balance_hosts",
}

SESSION_PORT = 5432
DEFAULT_SUPABASE_SCHEMA = "foci"

# In the order they are preferred when more than one is set. An address set by
# hand (DATABASE_URL) comes before the ones an integration sets for itself;
# Neon's direct address before its pooled one.
CANDIDATES = ("DATABASE_URL_UNPOOLED", "DATABASE_URL", "POSTGRES_URL_NON_POOLING",
              "POSTGRES_URL")

_DIRECT = re.compile(r"^db\.([a-z0-9]+)\.supabase\.co$")


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_supabase(url: str) -> bool:
    host = _host(url)
    return host.endswith(".supabase.co") or host.endswith(".supabase.com")


def is_supabase_pooler(url: str) -> bool:
    return _host(url).endswith(".pooler.supabase.com")


def _project_of(url: str) -> str:
    """The Supabase project a URL belongs to: from the direct host's name, or
    from the pooler's user name, `postgres.<project>`."""
    parts = urlsplit(url)
    direct = _DIRECT.match((parts.hostname or "").lower())
    if direct:
        return direct.group(1)
    user = parts.username or ""
    return user.split(".", 1)[1].lower() if "." in user else ""


def clean(url: str) -> str:
    """The URL with only parameters libpq understands, and the scheme psycopg
    expects (Render and others hand out `postgres://`)."""
    parts = urlsplit(url)
    scheme = "postgresql" if parts.scheme == "postgres" else parts.scheme
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if k in LIBPQ_PARAMS])
    return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))


def session_mode(url: str) -> str:
    """A Supabase pooler address on its session-mode port."""
    parts = urlsplit(url)
    if not is_supabase_pooler(url) or parts.port in (None, SESSION_PORT):
        return url
    netloc = parts.netloc.rsplit(":", 1)[0] + f":{SESSION_PORT}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def choose(environ) -> str:
    """The address to connect to, from whichever of `CANDIDATES` are set."""
    given = [(environ.get(name) or "").strip() for name in CANDIDATES]
    given = [url for url in given if url]
    if not given:
        return ""
    url = given[0]
    if is_supabase(url) and not is_supabase_pooler(url):
        # Direct and IPv6-only: the same project's pooler, if it was given too.
        project = _project_of(url)
        pooled = [u for u in given if is_supabase_pooler(u) and _project_of(u) == project]
        if pooled:
            url = pooled[0]
    return clean(session_mode(url)) if is_supabase(url) else url


def schema_for(dsn: str, environ=None) -> str:
    """The schema this application keeps its tables in, or "" for the default.

    `FOCI_DB_SCHEMA` when set — empty for `public` — otherwise `foci` on
    Supabase and the default everywhere else.
    """
    environ = os.environ if environ is None else environ
    if "FOCI_DB_SCHEMA" in environ:
        schema = environ["FOCI_DB_SCHEMA"].strip()
    else:
        schema = DEFAULT_SUPABASE_SCHEMA if is_supabase(dsn) else ""
    if schema and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError(f"FOCI_DB_SCHEMA must be a lower-case Postgres name, not {schema!r}.")
    return schema
