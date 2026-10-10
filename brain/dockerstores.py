"""The dedicated databases in Docker — ★started for you, with the data in a folder of your own★ (§stores).

## Why (user instructions, 2026-10-07 and 2026-10-10)

*"Let people choose to install the databases with Docker — and a reboot or a Docker reinstall must not
lose the data."* (10-07) *"Every option must be installable on its own."* (10-10) — so each role is
started separately, with whichever backend the person names, and nothing here knows a backend by name:
what to run comes from the backend's own declaration (`BACKEND.docker`, §stores.Docker).

## Where the data lives — a plain folder in the brain's home, never a Docker volume

    <brain home>/stores/<backend>/<subfolder>      e.g. stores/qdrant · stores/neo4j/data · stores/neo4j/logs

⛔ A named volume lives inside Docker's own disk image, and a Docker Desktop reset or reinstall deletes it
   with everything else. A bind-mounted folder is an ordinary directory: removing the containers, the
   images or Docker itself leaves it where it is, and the next `brain stores --docker` picks it up again.
⛔ And under that sits the canonical copy: the local SQLite file. A database that comes back empty anyway
   is noticed by the next sync and refilled from it (§vecstore.sync · §graphstore.sync — "healed").

## What it runs

One compose project holding ★one service per role★ — the backend chosen for it — with the pinned image
the backend declares, ports bound to 127.0.0.1 only, and `restart: unless-stopped` (the containers come
back after a reboot as soon as Docker itself starts). Starting one role leaves the other running;
replacing a role's backend removes the old container (its data folder stays).
A credential the backend needs is generated once into `secrets.json` (0600) and handed to Docker ★by
name★ through the environment, never on a command line (a value on a command line is visible to `ps`).
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import shutil
import subprocess
import time
from typing import Dict, List, Optional, Sequence, Union

READY_TIMEOUT = float(os.environ.get("BRAIN_DOCKER_READY_TIMEOUT", "180") or 180)


def project() -> str:
    return os.environ.get("BRAIN_DOCKER_PROJECT", "local-brain").strip() or "local-brain"


def base_dir() -> str:
    from brain import store
    return os.path.join(store.brain_home(), "stores")


def compose_path() -> str:
    return os.path.join(base_dir(), "compose.yml")


# ── what a backend declares, turned into places and addresses ───────────────
def dockerable(role: str = "") -> Dict[str, object]:
    """Every backend that can be run in Docker: {name: Backend}."""
    from brain import stores
    return {n: b for n, b in stores.backends(role).items() if b.docker is not None}


def ports(b) -> Dict[str, int]:
    """The host ports: the declared ones, moved by their variable (`BRAIN_…_PORT`)."""
    return {p.name: int(os.environ.get(p.env, "") or p.host) for p in b.docker.ports}


def data_dirs(b) -> List[str]:
    return [os.path.join(base_dir(), b.name, *([sub] if sub else [])) for sub, _ in b.docker.data]


def url(b) -> str:
    return "%s://127.0.0.1:%d" % (b.docker.scheme, ports(b)[b.docker.url_port])


def resolve(tokens: Sequence[str]) -> Dict[str, str]:
    """`vector` · `graph=memgraph` · `memgraph` → {role: backend}. A role alone means its default.

    Raises ValueError naming what is accepted.
    """
    from brain import stores
    out: Dict[str, str] = {}
    for tok in [t.strip().lower() for t in tokens if t.strip()]:
        role, _, name = tok.partition("=")
        if name:
            if role not in stores.ROLES:
                raise ValueError(tok)
        elif tok in stores.ROLES:
            role, name = tok, stores.DOCKER_DEFAULT[tok]
        else:
            b = stores.spec(tok)
            if b is None or b.role not in stores.ROLES:
                raise ValueError(tok)
            role, name = b.role, tok
        b = dockerable(role).get(name)
        if b is None:
            raise ValueError(tok)
        out[role] = name
    return out


def managed() -> Dict[str, str]:
    """{role: backend} for every role whose chosen backend brain runs in Docker itself."""
    from brain import stores
    out = {}
    for role in stores.ROLES:
        c = stores.choice(role)
        if (c.get("options") or {}).get("managed") == "docker" and c["backend"] in dockerable(role):
            out[role] = c["backend"]
    return out


def services_for(wanted: Dict[str, str]) -> Dict[str, str]:
    """What the compose file must hold when `wanted` starts: every role brain already runs, then `wanted`.

    ⛔ Writing only `wanted` (2026-10-07 to 10-10) made `--remove-orphans` delete the other role's
       container — `brain stores --docker graph` took Qdrant down while config still pointed at it.
    """
    return dict(managed(), **wanted)


def compose_text(services: Dict[str, str]) -> str:
    """The compose file for {role: backend} — written next to the data it describes, so the folder explains itself."""
    from brain import stores
    name = project()
    out = ["# Written by local-brain (`brain stores --docker`). The data lives in the folders next to this file;",
           "# removing the containers, the images or Docker itself does not touch them.",
           "name: %s" % json.dumps(name), "services:"]
    for role in stores.ROLES:
        if role not in services:
            continue
        b = stores.spec(services[role])
        d, p = b.docker, ports(b)
        out += ["  %s:" % b.name,
                "    image: %s" % d.image,
                "    container_name: %s" % json.dumps("%s-%s" % (name, b.name)),
                "    restart: unless-stopped"]
        if d.user == "host" and hasattr(os, "getuid"):
            out.append("    user: %s" % json.dumps("%d:%d" % (os.getuid(), os.getgid())))
        if d.command:
            out.append("    command: %s" % json.dumps(list(d.command)))
        out.append("    ports:")
        out += ["      - %s" % json.dumps("127.0.0.1:%d:%d" % (p[x.name], x.container)) for x in d.ports]
        if d.env:
            out.append("    environment:")
            out += ["      - %s          # passed by name: the value comes from the environment, never the file" % k
                    for k in sorted(d.env)]
        out.append("    volumes:")
        out += ["      - %s" % json.dumps("%s:%s" % (host, path))
                for host, (_, path) in zip(data_dirs(b), d.data)]
    return "\n".join(out) + "\n"


# ── a credential: generated once, kept in secrets.json (0600) ───────────────
def generated_secret(b, create: bool = True) -> str:
    """The credential brain keeps for `b`'s container — made once when `create` and there is none."""
    from brain import engines
    path = engines.secrets_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    v = (data.get(b.secret.field) or "").strip()
    if v or not create:
        return v
    v = _secrets.token_urlsafe(24)
    data[b.secret.field] = v
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)                             # every other key in the file is kept as it was
    return v


def _secret_of(b, create: bool) -> str:
    return generated_secret(b, create) if b.docker.generate_secret else ""


def environment(services: Dict[str, str], create: bool = True) -> Dict[str, str]:
    """The values Docker reads by name — for the `docker compose` process only, never written to a file."""
    from brain import stores
    env = {}
    for name in services.values():
        b = stores.spec(name)
        sec = _secret_of(b, create)
        env.update({k: v.replace("{secret}", sec) for k, v in b.docker.env.items()})
    return env


# ── Docker itself ───────────────────────────────────────────────────────────
def _docker(args: List[str], env: Optional[dict] = None, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["docker"] + args, capture_output=True, text=True, env=env, timeout=timeout)


def available() -> dict:
    """Can Docker run containers here: {ok, why}."""
    if not shutil.which("docker"):
        return {"ok": False, "why": "no docker command on PATH"}
    try:
        r = _docker(["info", "--format", "{{.ServerVersion}}"], timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "why": str(exc)[:160]}
    if r.returncode != 0:
        return {"ok": False, "why": "the Docker daemon is not running (start Docker, then run this again)"}
    try:
        c = _docker(["compose", "version", "--short"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        c = None
    if not c or c.returncode != 0:
        return {"ok": False, "why": "`docker compose` is not available"}
    return {"ok": True, "why": "", "server": r.stdout.strip(), "compose": c.stdout.strip()}


def ready(services: Dict[str, str]) -> Dict[str, str]:
    """Wait until each service answers its own backend's `ping` → {role: version} ("" = never answered)."""
    from brain import stores
    left, out = set(services), {}
    end = time.time() + READY_TIMEOUT
    while left and time.time() < end:
        for role in sorted(left):
            b = stores.spec(services[role])
            c = {"role": role, "backend": b.name, "url": url(b), "options": {"managed": "docker"},
                 "secret": _secret_of(b, False)}      # the credential just generated, not a variable meant elsewhere
            try:
                out[role] = b.make(c).ping().get("version", "") or "?"
                left.discard(role)
            except (stores.StoreError, OSError):
                pass
        if left:
            time.sleep(2)
    for role in left:
        out[role] = ""
    return out


def up(wanted: Union[None, Sequence[str], Dict[str, str]] = None, dry_run: bool = False, say=print) -> dict:
    """Start (or repair) the chosen databases in Docker and point brain at them. Idempotent.

    `wanted` = {role: backend}, or roles (each gets its default), or None (every role's default).
    Returns {ok, roles: {role: version}, backends: {role: name}, compose, data: {role: [folders]}, why}.
    The first sync is the caller's next step (`stores.sync`) — kept separate so a dry run touches nothing.
    """
    from brain import i18n, stores
    if wanted is None:
        wanted = dict(stores.DOCKER_DEFAULT)
    elif not isinstance(wanted, dict):
        wanted = {r: stores.DOCKER_DEFAULT[r] for r in wanted if r in stores.ROLES}
    wanted = {r: wanted[r] for r in stores.ROLES if r in wanted}
    for role, name in wanted.items():
        if name not in dockerable(role):
            return {"ok": False, "why": "%s cannot run the %s role in Docker" % (name, role), "roles": {}}
    if dry_run:
        for r, name in wanted.items():
            b = stores.spec(name)
            say("  --  would " + i18n.t("install.would_docker_store", role=r, image=b.docker.image,
                                        data=", ".join(data_dirs(b))))
        return {"ok": True, "dry_run": True, "roles": {r: "" for r in wanted}, "backends": dict(wanted)}
    state = available()
    if not state["ok"]:
        return {"ok": False, "why": state["why"], "roles": {}}
    services = services_for(wanted)
    for name in wanted.values():
        for d in data_dirs(stores.spec(name)):
            os.makedirs(d, exist_ok=True)
    os.makedirs(base_dir(), exist_ok=True)
    with open(compose_path(), "w", encoding="utf-8") as fh:
        fh.write(compose_text(services))
    env = dict(os.environ, **environment(services))
    # `--remove-orphans`: a backend that no longer fills its role loses its container — never its folder
    r = _docker(["compose", "-f", compose_path(), "-p", project(), "up", "-d", "--remove-orphans"]
                + list(wanted.values()), env=env)
    if r.returncode != 0:
        return {"ok": False, "why": (r.stderr or r.stdout).strip()[-400:], "roles": {}}
    versions = ready(wanted)
    missing = [x for x, v in versions.items() if not v]
    if missing:
        return {"ok": False, "why": "not answering after %ds: %s" % (READY_TIMEOUT, ", ".join(missing)),
                "roles": versions}
    for role, name in wanted.items():
        # ⛔ starting it again is a repair — the options already chosen for this backend stay (`names`, …)
        prev = stores.choice(role)
        keep = ({k: v for k, v in prev["options"].items() if k not in stores.RESERVED}
                if prev["backend"] == name else {})
        stores.set_choice(role, name, url(stores.spec(name)), dict(keep, managed="docker"))
    return {"ok": True, "roles": versions, "backends": dict(wanted), "compose": compose_path(),
            "data": {r: data_dirs(stores.spec(n)) for r, n in wanted.items()}}


def status() -> List[dict]:
    """The project's containers as Docker sees them: [{service, state, status}]."""
    try:
        r = _docker(["compose", "-p", project(), "ps", "--all", "--format", "json"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    rows = []
    for line in r.stdout.splitlines():                 # one JSON object per line (compose v2+)
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        for x in (o if isinstance(o, list) else [o]):
            rows.append({"service": x.get("Service", ""), "state": x.get("State", ""), "status": x.get("Status", "")})
    return rows


def stop() -> bool:
    """Stop the containers. ⛔ The data stays — this never passes `-v`, and never removes a folder."""
    try:
        return _docker(["compose", "-p", project(), "stop"], timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
