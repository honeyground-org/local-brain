"""The dedicated databases in Docker — ★started for you, with the data in a folder of your own★ (§stores).

## Why (user instruction, 2026-10-07)

*"Let people choose to install the databases with Docker — and a reboot or a Docker reinstall must not
lose the data."*

## Where the data lives — a plain folder in the brain's home, never a Docker volume

    <brain home>/stores/qdrant         Qdrant's storage
    <brain home>/stores/neo4j/data     Neo4j's database
    <brain home>/stores/neo4j/logs

⛔ A named volume lives inside Docker's own disk image, and a Docker Desktop reset or reinstall deletes it
   with everything else. A bind-mounted folder is an ordinary directory: removing the containers, the
   images or Docker itself leaves it where it is, and the next `brain stores --docker` picks it up again.
⛔ And under that sits the canonical copy: the local SQLite file. A database that comes back empty anyway
   is noticed by the next sync and refilled from it (§vecstore.sync · §graphstore.sync — "healed").

## What it runs

One compose project, the ★pinned★ images the checks ran against, ports bound to 127.0.0.1 only, and
`restart: unless-stopped` — the containers come back after a reboot as soon as Docker itself starts.
The Neo4j password is generated once into `secrets.json` (0600) and handed to Docker ★by name★ through the
environment, never on a command line (a value on a command line is visible to `ps`).
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import shutil
import subprocess
import time
from typing import Dict, List, Optional, Sequence

IMAGES = {"qdrant": "qdrant/qdrant:v1.19.2", "neo4j": "neo4j:5.26.31-community"}
SERVICE = {"vector": "qdrant", "graph": "neo4j"}
READY_TIMEOUT = float(os.environ.get("BRAIN_DOCKER_READY_TIMEOUT", "180") or 180)


def project() -> str:
    return os.environ.get("BRAIN_DOCKER_PROJECT", "local-brain").strip() or "local-brain"


def ports() -> Dict[str, int]:
    return {"qdrant": int(os.environ.get("BRAIN_QDRANT_PORT", "6333") or 6333),
            "neo4j_http": int(os.environ.get("BRAIN_NEO4J_HTTP_PORT", "7474") or 7474),
            "neo4j_bolt": int(os.environ.get("BRAIN_NEO4J_BOLT_PORT", "7687") or 7687)}


def base_dir() -> str:
    from brain import store
    return os.path.join(store.brain_home(), "stores")


def compose_path() -> str:
    return os.path.join(base_dir(), "compose.yml")


def data_dirs() -> Dict[str, List[str]]:
    b = base_dir()
    return {"qdrant": [os.path.join(b, "qdrant")],
            "neo4j": [os.path.join(b, "neo4j", "data"), os.path.join(b, "neo4j", "logs")]}


def urls() -> Dict[str, str]:
    p = ports()
    return {"vector": "http://127.0.0.1:%d" % p["qdrant"], "graph": "http://127.0.0.1:%d" % p["neo4j_http"]}


def compose_text(roles: Sequence[str]) -> str:
    """The compose file — written next to the data it describes, so the folder explains itself."""
    p, d, name = ports(), data_dirs(), project()
    out = ["# Written by local-brain (`brain stores --docker`). The data lives in the folders next to this file;",
           "# removing the containers, the images or Docker itself does not touch them.",
           "name: %s" % json.dumps(name), "services:"]
    if "vector" in roles:
        out += ["  qdrant:",
                "    image: %s" % IMAGES["qdrant"],
                "    container_name: %s" % json.dumps(name + "-qdrant"),
                "    restart: unless-stopped",
                "    ports:",
                "      - %s" % json.dumps("127.0.0.1:%d:6333" % p["qdrant"]),
                "    volumes:",
                "      - %s" % json.dumps(d["qdrant"][0] + ":/qdrant/storage")]
    if "graph" in roles:
        out += ["  neo4j:",
                "    image: %s" % IMAGES["neo4j"],
                "    container_name: %s" % json.dumps(name + "-neo4j"),
                "    restart: unless-stopped",
                "    ports:",
                "      - %s" % json.dumps("127.0.0.1:%d:7474" % p["neo4j_http"]),
                "      - %s" % json.dumps("127.0.0.1:%d:7687" % p["neo4j_bolt"]),
                "    environment:",
                "      - NEO4J_AUTH          # passed by name: the value comes from the environment, never the file",
                "    volumes:",
                "      - %s" % json.dumps(d["neo4j"][0] + ":/data"),
                "      - %s" % json.dumps(d["neo4j"][1] + ":/logs")]
    return "\n".join(out) + "\n"


# ── the Neo4j password: generated once, kept in secrets.json (0600) ──────────
def password(create: bool = True) -> str:
    from brain import engines
    path = engines.secrets_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    pw = (data.get("neo4j_password") or "").strip()
    if pw or not create:
        return pw
    pw = _secrets.token_urlsafe(24)
    data["neo4j_password"] = pw
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)                             # every other key in the file is kept as it was
    return pw


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


def _wait_ready(roles: Sequence[str], pw: str) -> Dict[str, str]:
    from brain import graphstore, stores
    left, out = set(roles), {}
    end = time.time() + READY_TIMEOUT
    while left and time.time() < end:
        for role in sorted(left):
            try:
                if role == "vector":
                    out[role] = stores.http_json("GET", urls()["vector"] + "/", timeout=3).get("version", "")
                else:
                    g = graphstore.Neo4jGraph({"url": urls()["graph"], "options": {}})
                    g.headers = stores.basic_auth("neo4j", pw)      # the password just written, not the env
                    out[role] = g.ping()["version"]
                left.discard(role)
            except stores.StoreError:
                pass
        if left:
            time.sleep(2)
    for role in left:
        out[role] = ""
    return out


def up(roles: Sequence[str] = ("vector", "graph"), dry_run: bool = False, say=print) -> dict:
    """Start (or repair) the chosen databases in Docker and point brain at them. Idempotent.

    Returns {ok, roles: {role: version}, compose, data, why}. Choosing them and the first sync are the
    caller's next step (`stores.sync`) — kept separate so a dry run touches nothing at all.
    """
    from brain import i18n, stores
    roles = [r for r in ("vector", "graph") if r in roles]
    if dry_run:
        for r in roles:
            say("  --  would " + i18n.t("install.would_docker_store", role=r, image=IMAGES[SERVICE[r]],
                                        data=", ".join(data_dirs()[SERVICE[r]])))
        return {"ok": True, "dry_run": True, "roles": {r: "" for r in roles}}
    state = available()
    if not state["ok"]:
        return {"ok": False, "why": state["why"], "roles": {}}
    for r in roles:
        for d in data_dirs()[SERVICE[r]]:
            os.makedirs(d, exist_ok=True)
    os.makedirs(base_dir(), exist_ok=True)
    with open(compose_path(), "w", encoding="utf-8") as fh:
        fh.write(compose_text(roles))
    env = dict(os.environ)
    pw = ""
    if "graph" in roles:
        pw = password(create=True)
        env["NEO4J_AUTH"] = "neo4j/" + pw
    r = _docker(["compose", "-f", compose_path(), "-p", project(), "up", "-d", "--remove-orphans"]
                + [SERVICE[x] for x in roles], env=env)
    if r.returncode != 0:
        return {"ok": False, "why": (r.stderr or r.stdout).strip()[-400:], "roles": {}}
    versions = _wait_ready(roles, pw)
    missing = [x for x, v in versions.items() if not v]
    if missing:
        return {"ok": False, "why": "not answering after %ds: %s" % (READY_TIMEOUT, ", ".join(missing)),
                "roles": versions}
    for x in roles:
        stores.set_choice(x, SERVICE[x], urls()[x], {"managed": "docker"})
    return {"ok": True, "roles": versions, "compose": compose_path(),
            "data": {x: data_dirs()[SERVICE[x]] for x in roles}}


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
