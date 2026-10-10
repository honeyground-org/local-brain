"""Storage backends — ★which database serves similarity search and graph traversal★, chosen by the person.

## Why (user instructions, 2026-10-07 and 2026-10-10)

*"The vector DB and the graph DB must be replaceable with databases of that nature."* (10-07) Until then
both lived only in the local SQLite file: embeddings as BLOBs scanned in full, links as a table joined by
name. That is the right default for one person's notes and the wrong ceiling for everyone else — a
team that already runs a vector database or a graph database should be able to point brain at it.

*"Any database must be possible — adding an adapter must be all it takes — and every option must be
installable on its own."* (10-10) So a backend is ★one file★, and each role is installed separately.

## The shape: the local copy stays, the dedicated database serves

SQLite keeps the ★canonical copy★ of both. Embeddings are earned (each one an API call) and the graph is
derived from your own files, so neither may live only on someone else's server. A dedicated database
is a ★serving index★ for the one capability it is built for:

  vector   similarity search over note embeddings  →  sqlite (default) · any vector backend
  graph    traversal of the links between notes      →  sqlite (default) · any graph backend

  · switching is a ★sync, not a re-embed★ — `brain stores --sync` pushes the local copy
  · a ★diff★ decides what is sent (a ledger of what each target already holds), so unchanged data
    costs no network, and an interrupted sync resumes where it stopped
  · the dedicated database answers only once it is ★known to be in sync★; before that, or when it
    does not answer, the local copy answers — and `brain stores` says so (never silently)
  · `brain stores --check` asks both the same questions and reports where they disagree

## ★One file per backend★ — `brain/backends/<name>.py`

A backend is a module in `brain/backends/` that defines `BACKEND = stores.Backend(...)`: its name and
role, its usual address, its credential, its options, how to run it in Docker, and the class that speaks
to it. ★Nothing else is edited to add one.★ From that one declaration come:

  brain stores --set ROLE=NAME         the choice, checked against the role and the options it declares
  brain stores --<option>              one flag per declared option
  brain stores --docker NAME           its container, its data folders, its readiness check
  brain install --vector-store NAME    (and --graph-store) — each role installed on its own, or not at all
  brain stores                         what it is sent, whether it answers, whether it is in sync
  tests/verify_store_adapters.py       the contract every backend must pass, with failing controls

What a backend must answer is its role's contract (`vecstore.CONTRACT`, `graphstore.CONTRACT`). A module
that does not load, or does not meet the contract, is never offered — and `brain stores` says why. A
module whose name starts with `_` is a helper several backends share (a query language, a wire
protocol), not a backend. How to write one: docs/STORAGE.md.

## ⛔ Chosen, never guessed

Order: `BRAIN_<ROLE>_STORE` → `config.json` `"stores"` → `sqlite`. A database that happens to be running
is not a choice. Credentials come from the environment or `secrets.json` (0600), never from config.

## ⛔ What leaves this machine — decided by the role, not by the backend

A backend is handed only what its role's sync gives it, so it cannot send what it never receives:

  vector   the vectors and `{doc_id, chunk_no}` — no text (§vecstore.sync)
  graph    document ids and the links between them; document names only with `"names": true`, and
           then through `privacy.scrub` like every other outbound text (§graphstore.sync)

Standard library only, like the rest of brain: each backend speaks its database's own protocol.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

ROLES = ("vector", "graph")

QUERY_TIMEOUT = float(os.environ.get("BRAIN_STORE_TIMEOUT", "3") or 3)      # one search · one traversal
SYNC_TIMEOUT = float(os.environ.get("BRAIN_STORE_SYNC_TIMEOUT", "60") or 60)
DOWN_RETRY_SEC = 60.0      # after a failure, this process asks the local copy for this long


class StoreError(RuntimeError):
    """A dedicated database did not answer, or answered with an error."""


class StoreGone(StoreError):
    """The database answered that what was asked for is not there — an HTTP 404, a missing table.

    The copy it held is gone (a deleted collection, a Docker reset): the next sync refills it from the
    local copy, and until then the local copy answers.
    """


def gone(exc: BaseException) -> bool:
    """Does this failure mean "not there" — by its type, or (for a backend written before the type) its text."""
    return isinstance(exc, StoreGone) or "HTTP 404" in str(exc)


# ── what a backend declares ─────────────────────────────────────────────────
class Option:
    """One setting a backend takes: `brain stores --<name>` on the command line, a key in config.json.

    The default's type is the option's kind — `bool` is a switch, `str` and `int` take a value.
    """

    def __init__(self, name: str, default, help: str):
        self.name, self.default, self.help = name, default, help
        self.kind = type(default)


class Secret:
    """Where a backend's credential is looked for: a variable, then a field of `secrets.json` (0600)."""

    def __init__(self, env: str, field: str, required: bool = False):
        self.env, self.field, self.required = env, field, required


class Port:
    """A port the container listens on. Bound to 127.0.0.1 only; the host port can be moved with `env`."""

    def __init__(self, name: str, container: int, env: str, host: int = 0):
        self.name, self.container, self.env, self.host = name, int(container), env, int(host or container)


class Docker:
    """How `brain stores --docker` runs a backend (§dockerstores).

    image            pinned — a tag with a version, never `latest`
    ports            `Port`s; `url_port` names the one brain talks to (default: the first), over `scheme`,
                     with `url_path` after it (a Postgres database name, for example)
    data             (subfolder, path in the container) — kept in `<brain home>/stores/<name>/<subfolder>`
    env              {NAME: value}; a value holding `{secret}` is handed to Docker ★by name★ and becomes the
                     credential, any other value is written into the compose file
    generate_secret  create the credential once into secrets.json when there is none
    command          arguments for the image's entry point
    user             "host" runs the container as your own user (files in the data folder stay yours)
    telemetry_off    ★required★ — how the image's own usage reporting is turned off: "NAME=VALUE" (a
                     variable) or "--flag=value" (an argument), or () when the image reports nothing.
                     ⛔ Qdrant, Neo4j and Memgraph all report home by default (measured 2026-10-10): a
                     database brain starts must not send anything off the machine that nobody chose.
    """

    def __init__(self, image: str, ports: Sequence[Port], data: Sequence[Tuple[str, str]],
                 scheme: str = "http", url_port: str = "", url_path: str = "", env: Optional[Dict[str, str]] = None,
                 generate_secret: bool = False, command: Sequence[str] = (), user: str = "",
                 telemetry_off: Optional[Sequence[str]] = None):
        self.image, self.ports, self.data = image, tuple(ports), tuple(data)
        self.scheme, self.url_port, self.url_path = scheme, url_port or (ports[0].name if ports else ""), url_path
        self.env, self.generate_secret = dict(env or {}), generate_secret
        self.command, self.user = tuple(command), user
        self.telemetry_off = None if telemetry_off is None else tuple(telemetry_off)

    def settings(self) -> Tuple[Dict[str, str], Tuple[str, ...]]:
        """(environment, arguments) the container runs with — the declared ones plus the reporting switch."""
        env, args = dict(self.env), list(self.command)
        for s in self.telemetry_off or ():
            if s.startswith("--"):
                args.append(s)
            else:
                k, _, v = s.partition("=")
                env[k] = v
        return env, tuple(args)


class Backend:
    """One storage backend — everything brain needs to know about it, in the file that implements it."""

    def __init__(self, name: str, role: str, make: Optional[Callable] = None, title: str = "",
                 url: str = "", secret: Optional[Secret] = None, options: Sequence[Option] = (),
                 docker: Optional[Docker] = None):
        self.name, self.role, self.make, self.title = name, role, make, title or name
        self.url, self.secret, self.options, self.docker = url, secret, tuple(options), docker
        self.module = ""

    def serves(self, role: str) -> bool:
        return self.role == "*" or self.role == role


# The local copy: both roles, always there, nothing to install.
SQLITE = Backend("sqlite", "*", title="SQLite (the local copy)")

# Options that belong to the role, not to a backend — every backend of the role takes them.
ROLE_OPTIONS: Dict[str, Tuple[Option, ...]] = {
    "vector": (Option("namespace", "", "keeps this brain's vectors apart in a database several brains share"),),
    "graph": (Option("names", False, "also send document names (off by default; scrubbed like all outbound text)"),
              Option("namespace", "", "this brain's name inside a database several brains share (default: derived)")),
}
RESERVED = ("backend", "url", "managed")      # config keys that are not options

# What `--stores docker` and `brain stores --docker ROLE` start when no backend is named.
DOCKER_DEFAULT = {"vector": "qdrant", "graph": "neo4j"}


# ── the registry: discovered from brain/backends/, never listed by hand ─────
_REGISTRY: Optional[Dict[str, Backend]] = None
_BROKEN: List[Tuple[str, str]] = []
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_PORT_ENV = re.compile(r"^BRAIN_[A-Z0-9_]+_PORT$")


def contract(role: str) -> Tuple[str, ...]:
    """The methods a dedicated backend of `role` must have."""
    if role == "vector":
        from brain import vecstore
        return vecstore.CONTRACT
    from brain import graphstore
    return graphstore.CONTRACT


def problems(b, module: str, seen: Dict[str, Backend]) -> str:
    """Why `b` (a module's BACKEND) cannot be offered — "" when it can."""
    if not isinstance(b, Backend):
        return "defines no BACKEND = stores.Backend(...)"
    if b.name != module:
        return "BACKEND.name is %r but the file is %s.py — they must match" % (b.name, module)
    if not _NAME.match(b.name) or b.name in seen:
        return "the name %r is not a lowercase identifier, or is taken" % b.name
    if b.role not in ROLES:
        return "role %r is not one of %s" % (b.role, ", ".join(ROLES))
    if not callable(b.make):
        return "BACKEND.make is not a class"
    missing = [m for m in contract(b.role) if not callable(getattr(b.make, m, None))]
    if missing:
        return "does not meet the %s contract — missing %s" % (b.role, ", ".join(missing))
    kinds = {o.name: o.kind for x in list(seen.values()) for o in x.options}
    kinds.update({o.name: o.kind for opts in ROLE_OPTIONS.values() for o in opts})
    names = set()
    for o in b.options:
        if not isinstance(o, Option) or not _NAME.match(o.name) or o.kind not in (bool, str, int):
            return "an option is not stores.Option(name, default bool|str|int, help)"
        if o.name in RESERVED or o.name in names or any(o.name == r.name for r in ROLE_OPTIONS[b.role]):
            return "the option %r is reserved or declared twice" % o.name
        if o.name in kinds and kinds[o.name] is not o.kind:
            return "the option %r is a %s in another backend — one flag cannot be both" % (
                o.name, kinds[o.name].__name__)
        names.add(o.name)
    if b.secret is not None and not (isinstance(b.secret, Secret) and b.secret.env and b.secret.field):
        return "BACKEND.secret needs both a variable and a secrets.json field"
    d = b.docker
    if d is not None:
        if not isinstance(d, Docker) or not d.ports:
            return "BACKEND.docker is not stores.Docker(image, ports, data)"
        if (d.image.endswith(":latest") or ":" not in d.image.rsplit("/", 1)[-1]
                or not any(c.isdigit() for c in d.image.rsplit(":", 1)[-1])) and "@sha256:" not in d.image:
            return "the image %r is not pinned to a version" % d.image
        used = {p.env for x in seen.values() if x.docker for p in x.docker.ports}
        for p in d.ports:
            if not _PORT_ENV.match(p.env) or p.env in used:
                return "the port variable %r is not BRAIN_…_PORT, or is taken" % p.env
        if d.url_port not in {p.name for p in d.ports}:
            return "url_port %r is not one of the ports" % d.url_port
        for sub, path in d.data:
            if not path.startswith("/") or ".." in sub.split("/") or sub.startswith("/"):
                return "a data folder must be a subfolder here and an absolute path in the container"
        if d.generate_secret and b.secret is None:
            return "generate_secret needs BACKEND.secret (where to keep it)"
        if d.telemetry_off is None:
            return ("say how the image's own usage reporting is turned off — telemetry_off=(\"NAME=VALUE\" "
                    "or \"--flag=value\",), or () when it reports nothing")
        for s in d.telemetry_off:
            if not re.match(r"^(?:--[A-Za-z0-9][\w.-]*|[A-Za-z_][A-Za-z0-9_]*)=\S+$", s):
                return "telemetry_off %r is not NAME=VALUE or --flag=value" % s
    return ""


def backends(role: str = "") -> Dict[str, Backend]:
    """Every backend that can be chosen — the local copy, then each module in `brain/backends/` that loads.

    Discovered once per process. ⛔ A module that fails is ★not offered★ and is listed by `broken()` —
    a backend that is silently missing would look like one that was never written.
    """
    global _REGISTRY
    if _REGISTRY is None:
        import importlib
        import pkgutil
        reg: Dict[str, Backend] = {"sqlite": SQLITE}
        broken: List[Tuple[str, str]] = []
        try:
            from brain import backends as pkg
            found = sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name)
        except ImportError as exc:
            # ⛔ an install without brain/backends/ must still answer everything from the local copy — a
            #    missing folder took every command down with it, `brain recall` included (control, 2026-10-10)
            found = []
            broken.append(("brain.backends", "the folder is missing from this install — %s" % str(exc)[:160]))
        for m in found:
            if m.name.startswith("_"):
                continue
            try:
                b = getattr(importlib.import_module("brain.backends." + m.name), "BACKEND", None)
                why = problems(b, m.name, reg)
            except Exception as exc:                       # noqa: BLE001 — one bad file must not take the rest down
                b, why = None, "does not load — %s: %s" % (type(exc).__name__, str(exc)[:200])
            if why:
                broken.append((m.name, why))
                continue
            b.module = "brain.backends." + m.name
            reg[b.name] = b
        _REGISTRY, _BROKEN[:] = reg, broken
    return {n: b for n, b in _REGISTRY.items() if not role or b.serves(role)}


def broken() -> List[Tuple[str, str]]:
    """[(module, why)] — backend files that are not offered, and the reason."""
    backends()
    return list(_BROKEN)


def names(role: str) -> List[str]:
    return list(backends(role))


def spec(name: str) -> Optional[Backend]:
    return backends().get(name)


def option_flags() -> Dict[str, dict]:
    """One command-line flag per option any backend or role declares: {name: {kind, default, help}}."""
    out: Dict[str, dict] = {}

    def add(o: Option, owner: str) -> None:
        f = out.setdefault(o.name, {"kind": o.kind, "help": []})
        f["help"].append("%s: %s (default %s)" % (owner, o.help, json.dumps(o.default))
                         if o.kind is not bool and o.default != "" else "%s: %s" % (owner, o.help))
    for role in ROLES:
        for o in ROLE_OPTIONS[role]:
            add(o, role)
    for b in backends().values():
        for o in b.options:
            add(o, b.name)
    return out


def opt(c: dict, name: str):
    """A chosen option's value — or the default its backend (or role) declares for it."""
    o = c.get("options") or {}
    if o.get(name) not in (None, ""):
        return o[name]
    b = spec(c.get("backend", ""))
    role = c.get("role") or (b.role if b else "")
    for x in (b.options if b else ()) + ROLE_OPTIONS.get(role, ()):
        if x.name == name:
            return x.default
    return None


# ── which backend fills a role ─────────────────────────────────────────────
def _config_stores() -> dict:
    try:
        from brain import store
        return store.load_config(tolerant=True).get("stores") or {}
    except Exception:                                    # noqa: BLE001
        return {}


def choice(role: str) -> dict:
    """The backend filling `role`: {role, backend, url, source, options, error}. source = env | config | default.

    error = "unknown" (no such backend for this role) or "broken" (its file does not load) — and then the
    local copy answers.
    """
    env = role.upper()
    cfg = dict(_config_stores().get(role) or {})
    backend = (os.environ.get("BRAIN_%s_STORE" % env) or "").strip().lower()
    source = "env" if backend else ""
    if not backend and cfg.get("backend"):
        backend, source = str(cfg["backend"]).strip().lower(), "config"
    if not backend:
        backend, source = "sqlite", "default"
    b = backends(role).get(backend)
    if b is None:
        err = "broken" if backend in dict(broken()) else "unknown"
        return {"role": role, "backend": backend, "url": "", "source": source, "options": cfg, "error": err}
    url = (os.environ.get("BRAIN_%s_STORE_URL" % env) or cfg.get("url") or b.url).rstrip("/")
    return {"role": role, "backend": backend, "url": url, "source": source, "options": cfg, "error": ""}


def is_local(c: dict) -> bool:
    """Does this backend keep everything on this machine."""
    if c.get("backend") == "sqlite":
        return True
    p = urllib.parse.urlsplit(c.get("url") or "")
    host = p.hostname or ""
    return p.scheme == "file" or host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


def secret(c: dict) -> str:
    """The backend's credential: its variable → `secrets.json`. "" when there is none.

    A container brain runs itself (`managed: docker`) uses the credential brain generated for it first —
    a variable meant for another server of the same kind must not lock brain out of its own.
    """
    if c.get("secret"):
        return str(c["secret"])
    b = spec(c.get("backend", ""))
    s = b.secret if b else None
    if s is None:
        return ""
    from_env = os.environ.get(s.env, "").strip()
    try:
        from brain import engines
        with open(engines.secrets_path(), encoding="utf-8") as fh:
            from_file = (json.load(fh).get(s.field) or "").strip()
    except (OSError, ValueError, AttributeError):
        from_file = ""
    own = (c.get("options") or {}).get("managed") == "docker" and b.docker and b.docker.generate_secret
    return (from_file or from_env) if own else (from_env or from_file)


def namespace() -> str:
    """Which brain this is, inside a database several brains may share — derived, never configured by hand."""
    from brain import store
    return hashlib.sha1(os.path.realpath(store.brain_home()).encode("utf-8")).hexdigest()[:10]


def set_choice(role: str, backend: str, url: str = "", options: Optional[dict] = None) -> dict:
    """Write one role's backend into config.json. ⛔ Only a person (the CLI, the installer) calls this.

    Refuses a backend that cannot serve the role, and an option the backend does not declare — an option
    that is silently ignored looks like one that took effect.
    """
    from brain import store
    backend = backend.strip().lower()
    if role not in ROLES:
        raise ValueError("role must be one of %s" % ", ".join(ROLES))
    b = backends(role).get(backend)
    if b is None:
        bad = dict(broken()).get(backend)
        if bad:
            raise ValueError("%s is not available: %s" % (backend, bad))
        raise ValueError("%s cannot serve the %s role — choose one of %s" % (backend, role, ", ".join(names(role))))
    allowed = {o.name: o for o in b.options + ROLE_OPTIONS[role]}
    cfg = store.load_config()             # ⛔ not tolerant — no config means not installed yet
    entry = {"backend": backend}
    if url:
        entry["url"] = url.rstrip("/")
    for k, v in (options or {}).items():
        if v in (None, ""):
            continue
        if k != "managed" and k not in allowed:
            raise ValueError("%s takes no option %r — its options: %s" % (
                backend, k, ", ".join(sorted(allowed)) or "none"))
        if k in allowed and allowed[k].kind is int:
            v = int(v)
        entry[k] = v
    cfg.setdefault("stores", {})[role] = entry
    store.save_config(cfg)
    return entry


def parse_assignments(items: List[str]) -> Dict[str, str]:
    """`vector=qdrant graph=neo4j` → {role: backend}."""
    out = {}
    for it in items:
        role, _, backend = it.strip().lower().partition("=")
        if role not in ROLES or not backend:
            raise ValueError("expected role=backend, e.g. vector=qdrant — got %r" % it)
        out[role] = backend
    return out


# ── HTTP (standard library only) ───────────────────────────────────────────
def http_json(method: str, url: str, body=None, headers: Optional[Dict[str, str]] = None,
              timeout: float = QUERY_TIMEOUT) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    h = {"Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        kind = StoreGone if exc.code == 404 else StoreError
        raise kind("%s %s → HTTP %d %s" % (method, _redact(url), exc.code, detail)) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise StoreError("%s %s → %s" % (method, _redact(url), getattr(exc, "reason", exc))) from None
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise StoreError("%s %s → not JSON" % (method, _redact(url))) from None


def basic_auth(user: str, password: str) -> Dict[str, str]:
    tok = base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + tok}


def _redact(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.hostname or "", p.path, "", "")) if p.password else url


# ── the ledger: what each target already holds ─────────────────────────────
def _ensure_ledger(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS store_ledger("
               " role TEXT NOT NULL, target TEXT NOT NULL, key TEXT NOT NULL, sig TEXT NOT NULL,"
               " PRIMARY KEY(role, target, key)) WITHOUT ROWID")


def ledger(db: sqlite3.Connection, role: str, target: str) -> Dict[str, str]:
    _ensure_ledger(db)
    return {r[0]: r[1] for r in db.execute(
        "SELECT key, sig FROM store_ledger WHERE role=? AND target=?", (role, target))}


def ledger_apply(db: sqlite3.Connection, role: str, target: str,
                 upserts: Dict[str, str], deletes: Iterable[str] = ()) -> None:
    _ensure_ledger(db)
    with db:
        dl = [(role, target, k) for k in deletes]
        if dl:
            db.executemany("DELETE FROM store_ledger WHERE role=? AND target=? AND key=?", dl)
        if upserts:
            db.executemany("INSERT OR REPLACE INTO store_ledger(role,target,key,sig) VALUES(?,?,?,?)",
                           [(role, target, k, v) for k, v in upserts.items()])


def ledger_clear(db: sqlite3.Connection, role_prefix: str, target: str) -> None:
    _ensure_ledger(db)
    with db:
        db.execute("DELETE FROM store_ledger WHERE role LIKE ? AND target=?", (role_prefix + "%", target))


# ── health: is the target in sync, and did it answer ────────────────────────
def _health_key(role: str) -> str:
    return "store_health_" + role


def health(db: sqlite3.Connection, role: str) -> dict:
    from brain import store
    try:
        h = json.loads(store.get_meta(db, _health_key(role), "") or "{}")
    except ValueError:
        h = {}
    return h if isinstance(h, dict) else {}


def record(db: sqlite3.Connection, role: str, **fields) -> None:
    from brain import store
    h = health(db, role)
    h.update(fields)
    h["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with db:
            store.set_meta(db, _health_key(role), json.dumps(h, ensure_ascii=False))
    except sqlite3.Error:
        pass                                             # a status note must never block an answer


_DOWN: Dict[str, float] = {}


def serve(db: sqlite3.Connection, role: str, target: str, remote: Callable[[], object],
          local: Callable[[], object]):
    """Ask the dedicated database — ★only if it is known to hold this target in sync★ — else the local copy.

    ⛔ A failure is recorded (so `brain stores` shows it) and this process uses the local copy for
       `DOWN_RETRY_SEC`, so a database that is down costs one timeout, not one per question.
    """
    h = health(db, role)
    if h.get("synced") != target or time.time() - _DOWN.get(role, 0.0) < DOWN_RETRY_SEC:
        return local()
    try:
        return remote()
    except StoreError as exc:
        _DOWN[role] = time.time()
        lost = {"synced": ""} if gone(exc) else {}       # it no longer holds this copy — the next sync heals it
        record(db, role, ok=False, error=str(exc)[:300], fallbacks=int(h.get("fallbacks") or 0) + 1, **lost)
        return local()


# ── the two roles, built from their choice ─────────────────────────────────
def vector_backend(c: Optional[dict] = None):
    from brain import vecstore
    return vecstore.make(c or choice("vector"))


def graph_backend(c: Optional[dict] = None):
    from brain import graphstore
    return graphstore.make(c or choice("graph"))


def sync(db: sqlite3.Connection, roles: Tuple[str, ...] = ROLES, full: bool = False,
         progress: bool = False) -> Dict[str, dict]:
    """Push the local copy to every chosen dedicated database. Never raises — the error is the result."""
    out: Dict[str, dict] = {}
    for role in roles:
        c = choice(role)
        if c["backend"] == "sqlite" or c.get("error"):
            out[role] = {"backend": c["backend"], "skipped": "local" if not c.get("error") else c["error"]}
            continue
        try:
            if role == "vector":
                from brain import vecstore
                out[role] = vecstore.sync(db, vector_backend(c), full=full, progress=progress)
            else:
                from brain import graphstore
                out[role] = graphstore.sync(db, graph_backend(c), full=full, progress=progress,
                                            names=bool(opt(c, "names")))
            record(db, role, ok=True, error="", synced=out[role]["target"])
            _DOWN.pop(role, None)
        except StoreError as exc:
            record(db, role, ok=False, error=str(exc)[:300])
            out[role] = {"backend": c["backend"], "error": str(exc)[:300]}
    return out


def sync_if_chosen(db: sqlite3.Connection, roles: Tuple[str, ...] = ROLES) -> Dict[str, dict]:
    """The cheap call indexing makes — nothing at all unless a dedicated database is chosen."""
    wanted = tuple(r for r in roles if choice(r)["backend"] != "sqlite")
    return sync(db, wanted) if wanted else {}


# ── what the screens show ──────────────────────────────────────────────────
def _ledger_role(role: str) -> str:
    return "vector" if role == "vector" else "graph.node"


def describe(db: sqlite3.Connection) -> List[dict]:
    """One row per role: the choice, whether the target answers, what it holds, and whether it serves."""
    from brain import vectors
    rows = []
    for role in ROLES:
        c = choice(role)
        row = dict(c, local=is_local(c), health=health(db, role), spec=spec(c["backend"]))
        if role == "vector":
            vectors.ensure_table(db)
            row["local_count"] = db.execute(
                "SELECT COUNT(DISTINCT v.doc_id) FROM vectors v JOIN docs d ON d.id=v.doc_id "
                "WHERE v.model=? AND v.dim=?", (vectors.model_tag(), vectors.DIM)).fetchone()[0]
        else:
            row["local_count"] = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        if c["backend"] != "sqlite" and not c.get("error"):
            be = vector_backend(c) if role == "vector" else graph_backend(c)
            target = be.target(vectors.model_tag(), vectors.DIM) if role == "vector" else be.target()
            row["target"] = target
            row["held"] = len(ledger(db, _ledger_role(role), target))
            row["in_sync"] = row["health"].get("synced") == target
            try:
                row["ping"] = be.ping()
            except StoreError as exc:
                row["ping"] = {"ok": False, "error": str(exc)[:200]}
        rows.append(row)
    return rows


def check(db: sqlite3.Connection, roles: Tuple[str, ...] = ROLES, sample: int = 40) -> Dict[str, dict]:
    """★Ask the local copy and the dedicated database the same questions★ and say where they disagree.

    vector  the last `sample` cached questions (no embedding call): top-1 agreement, overlap of the top 10,
            the largest score difference, and how long each took
    graph   every document's one-hop neighbours, every edge, every link target
    """
    out: Dict[str, dict] = {}
    for role in roles:
        c = choice(role)
        if c["backend"] == "sqlite" or c.get("error"):
            out[role] = {"backend": c["backend"], "skipped": "local"}
            continue
        try:
            out[role] = _check_vector(db, c, sample) if role == "vector" else _check_graph(db, c)
        except StoreError as exc:
            out[role] = {"backend": c["backend"], "error": str(exc)[:300]}
    return out


def _check_vector(db: sqlite3.Connection, c: dict, sample: int) -> dict:
    from brain import vectors, vecstore
    be, local = vecstore.make(c), vecstore.SqliteVectors()
    model, dim = vectors.model_tag(), vectors.DIM
    try:
        qs = [vecstore._unpack(r[0]) for r in db.execute(
            "SELECT vec FROM vec_qcache WHERE model=? AND dim=? ORDER BY rowid DESC LIMIT ?", (model, dim, sample))]
    except sqlite3.Error:
        qs = []
    top1 = 0
    overlap = delta = t_local = t_store = 0.0
    for q in qs:
        t = time.perf_counter()
        a = local.search(db, q, model, dim, 10)
        t_local += time.perf_counter() - t
        t = time.perf_counter()
        b = be.search(db, q, model, dim, 10)
        t_store += time.perf_counter() - t
        top1 += 1 if a[:1] and b[:1] and a[0][0] == b[0][0] else 0
        overlap += len({d for d, _ in a} & {d for d, _ in b}) / float(max(1, len(a)))
        got = dict(b)
        delta = max([delta] + [abs(s - got[d]) for d, s in a if d in got])
    n = len(qs)
    return {"backend": c["backend"], "target": be.target(model, dim), "queries": n, "top1_agree": top1,
            "overlap_at_10": round(overlap / n, 4) if n else None, "max_score_delta": round(delta, 6),
            "ms_local": round(1000 * t_local / n, 1) if n else None,
            "ms_store": round(1000 * t_store / n, 1) if n else None,
            "chunks_local": local.count(db, model, dim), "chunks_store": be.count(db, model, dim)}


def _check_graph(db: sqlite3.Connection, c: dict) -> dict:
    from brain import graphstore
    be, local = graphstore.make(c), graphstore.LOCAL
    ids = [r[0] for r in db.execute("SELECT id FROM docs ORDER BY id")]
    t = time.perf_counter()
    a = local.neighbors(db, ids)
    t_local = time.perf_counter() - t
    t = time.perf_counter()
    b = be.neighbors(db, ids)
    t_store = time.perf_counter() - t
    same = sum(1 for i in ids if a.get(i, set()) == b.get(i, set()))
    ea, eb = set(local.edges(db)), set(be.edges(db))
    return {"backend": c["backend"], "target": be.target(), "docs": len(ids), "neighbors_same": same,
            "edges_local": len(ea), "edges_store": len(eb), "edges_same": ea == eb,
            "targets_same": local.targets(db) == be.targets(db),
            "ms_local": round(1000 * t_local, 1), "ms_store": round(1000 * t_store, 1)}
