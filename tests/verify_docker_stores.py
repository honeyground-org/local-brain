#!/usr/bin/env python3
"""★The Docker databases keep their data through a restart, a removal and a reinstall★ (local · budget 0)

## What this holds down (§brain/dockerstores.py) — for ★every backend that declares a Docker image★

Always (no Docker needed):
  ① the compose file keeps the data in ★plain folders under the brain's home★ (no Docker volume — a Docker
     reset deletes those), binds ports to 127.0.0.1, restarts unless stopped, pins its images, and names
     every credential ★without its value★
  ② a generated credential is made once (0600), reused, and every other key in secrets.json is kept
  ③ a dry run starts nothing, writes nothing, and says what it would do

Only with `BRAIN_TEST_DOCKER=1` (or `=name,name` for some) and a running Docker — it pulls and starts the
containers on other ports, under its own project name, and removes them at the end. Each vector backend is
paired with each graph backend (round-robin); for every pair:
  ④ after `restart`, the same answers — without sending anything again; starting one role again leaves
     the other one running and in sync
  ⑤ after the containers are ★removed★ and created again (what a Docker reinstall does), the same answers
     — still without sending anything: the data was in the folders all along
  ⑥ with the data folders emptied, the next sync notices and refills both from the local copy
  ⑦ the whole contract against the containers (§tests/_store_contract): an edit and a removal through
     indexing, a full rebuild, a lost copy refilled, an outage answered by the local copy

How to run:  PYTHONPATH=. python3 tests/verify_docker_stores.py
             BRAIN_TEST_DOCKER=1 PYTHONPATH=. python3 tests/verify_docker_stores.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-docker-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_EMBED_DIM", "BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE",
         "BRAIN_ADAPTIVE_LEARN", "BRAIN_TRANSLIT_MS", "BRAIN_DOCKER_PROJECT", "BRAIN_LANG")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_EMBED_DIM="24", BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0", BRAIN_LANG="en",
                  BRAIN_DOCKER_PROJECT="lbverify%s" % uuid.uuid4().hex[:6])
for _k in ("BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE"):
    os.environ.pop(_k, None)

from brain import dockerstores as dk, engines, stores  # noqa: E402
from tests import _store_contract as contract  # noqa: E402

# Every declared port moved by 10000, and every credential variable cleared — the person's own containers
# (on the usual ports) and passwords are never touched.
DOCKERABLE = dk.dockerable()
for _b in DOCKERABLE.values():
    for _p in _b.docker.ports:
        _SAVED.setdefault(_p.env, os.environ.get(_p.env))
        os.environ[_p.env] = str(_p.host + 10000)
    if _b.secret:
        _SAVED.setdefault(_b.secret.env, os.environ.get(_b.secret.env))
        os.environ.pop(_b.secret.env, None)

# ⛔ secrets.json's old location wins while it exists (§engines.secrets_path) — this check must never
#    write a real person's secrets file, so the path is pinned inside the temp folder.
_SECRETS = os.path.join(_TMP, "secrets.json")
engines.secrets_path = lambda: _SECRETS

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def offline() -> None:
    print("① the compose file — every backend that runs in Docker")
    rounds = [dict(stores.DOCKER_DEFAULT)] + [{b.role: n} for n, b in sorted(DOCKERABLE.items())]
    home = os.path.realpath(dk.base_dir())
    for services in rounds:
        text = dk.compose_text(services)
        lines, items = text.splitlines(), contract.compose_items(text)
        what = " + ".join(services.values())
        mounts, published = items.get("volumes", []), items.get("ports", [])
        check("%s: every mount is a plain folder under the brain's home" % what, mounts
              and all(os.path.realpath(contract.mount_host(m)).startswith(home) for m in mounts),
              "%d mounts" % len(mounts))
        check("%s: ★no Docker volume★ (a Docker reset deletes those)" % what,
              not any(l.startswith("volumes:") for l in lines))
        check("%s: ports bound to 127.0.0.1 only" % what,
              published and all(p.startswith("127.0.0.1:") for p in published), ", ".join(published))
        check("%s: restarts unless stopped (back after a reboot)" % what,
              text.count("restart: unless-stopped") == len(services))
        envs = [k for n in services.values() for k, v in stores.spec(n).docker.env.items() if "{secret}" in v]
        check("%s: credentials named, never assigned" % what,
              all(k in text and ("%s=" % k) not in text and ("%s:" % k) not in text for k in envs), ", ".join(envs))
    for name, b in sorted(DOCKERABLE.items()):
        text = dk.compose_text({b.role: name})
        items = contract.compose_items(text)
        off = b.docker.telemetry_off or ()
        check("%s: its own usage reporting is turned off (%s)" % (name, ", ".join(off) or "reports nothing"),
              b.docker.telemetry_off is not None and all(
                  (s in text) if s.startswith("--") else (s in items.get("environment", [])) for s in off))
    images = {n: b.docker.image for n, b in DOCKERABLE.items()}
    check("pinned images, never :latest", all(":" in v and "latest" not in v and any(c.isdigit() for c in v)
                                             for v in images.values()), json.dumps(images))

    print("\n② a generated credential")
    with open(_SECRETS, "w", encoding="utf-8") as fh:
        json.dump({"gemini_api_key": "keep-me"}, fh)
    gen = [b for b in DOCKERABLE.values() if b.docker.generate_secret]
    check("there is a backend whose credential brain generates", bool(gen), ", ".join(b.name for b in gen))
    for b in gen:
        pw = dk.generated_secret(b)
        mode = stat.S_IMODE(os.stat(_SECRETS).st_mode)
        data = json.load(open(_SECRETS, encoding="utf-8"))
        # ⛔ POSIX modes only: Windows keeps secrets.json private through the profile folder's ACL, and
        #    reports 0o666 for every file whatever its ACL says (issue #3)
        check("%s: generated once, private (0600%s)" % (b.name, ", POSIX" if os.name != "nt" else " — n/a on Windows"),
              len(pw) >= 24 and (mode == 0o600 or os.name == "nt"), oct(mode))
        check("%s: every other key is kept" % b.name, data.get("gemini_api_key") == "keep-me")
        check("%s: reused, not replaced" % b.name, dk.generated_secret(b) == pw)
        check("%s: and the compose file never holds it" % b.name, pw not in dk.compose_text({b.role: b.name}))

    print("\n③ a dry run")
    real_run, calls = subprocess.run, []
    subprocess.run = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(RuntimeError("no"))
    try:
        said = []
        r = dk.up(dry_run=True, say=said.append)
    finally:
        subprocess.run = real_run
    check("starts nothing", not calls and r.get("dry_run"))
    check("writes nothing", not os.path.exists(dk.compose_path()) and not os.path.exists(os.environ["BRAIN_CONFIG"]))
    check("says what it would do", len(said) == 2 and all("would" in s for s in said), said[0][:90] if said else "")


def same(db, roles, label: str) -> None:
    contract.same(db, roles, check, label)


def compose(services, *args: str) -> bool:
    env = dict(os.environ, **dk.environment(services, create=False))
    return subprocess.run(["docker", "compose", "-f", dk.compose_path(), "-p", dk.project()] + list(args),
                          capture_output=True, text=True, env=env, timeout=600, encoding="utf-8", errors="replace").returncode == 0


def empty_folder(d: str, image: str) -> None:
    """Empty a folder the containers wrote into.

    ⛔ On Linux those files belong to the container's user (root, or the database's own uid), so
       `shutil.rmtree(d, ignore_errors=True)` removed nothing and said nothing — and the refill row then
       measured a database that had never been emptied (CI, 2026-10-07; macOS maps ownership, so it
       passed there). Delete from inside a container, which may, then remove what is left as ourselves.
    """
    if not os.path.isdir(d):
        return
    subprocess.run(["docker", "run", "--rm", "--user", "0:0", "--entrypoint", "sh", "-v", "%s:/wipe" % d, image,
                    "-c", "rm -rf /wipe/* /wipe/.[!.]* 2>/dev/null; true"], capture_output=True, timeout=300)
    shutil.rmtree(d, ignore_errors=True)


def _is_empty(d: str) -> bool:
    try:
        return not os.path.exists(d) or not os.listdir(d)
    except OSError:
        return False                                    # unreadable is not empty — say so


def chosen_rounds() -> list:
    """[{role: backend}] — each vector backend paired with each graph backend, round-robin."""
    want = os.environ.get("BRAIN_TEST_DOCKER", "")
    pick = None if want == "1" else {x.strip() for x in want.split(",") if x.strip()}
    per = {r: sorted(n for n, b in DOCKERABLE.items() if b.role == r and (pick is None or n in pick))
           for r in stores.ROLES}
    roles = [r for r in stores.ROLES if per[r]]
    if not roles:
        return []
    return [{r: per[r][i % len(per[r])] for r in roles} for i in range(max(len(per[r]) for r in roles))]


def live_round(services: dict) -> None:
    roles = tuple(services)
    what = " + ".join("%s %s" % kv for kv in services.items())
    db, mem = contract.prepare(os.path.join(_TMP, "-".join(services.values())), {})
    try:
        print("\n④ [%s] up, sync, restart" % what)
        r = dk.up(services)
        check("the databases start and answer", r["ok"], json.dumps(r.get("roles") or r.get("why")))
        if not r["ok"]:
            return
        for name in services.values():
            off = stores.spec(name).docker.telemetry_off or ()
            got = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Env}} {{json .Args}}",
                                  "%s-%s" % (dk.project(), name)], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
            check("%s: the running container has its usage reporting turned off" % name,
                  all(json.dumps(s) in got for s in off), ", ".join(off) or "(reports nothing)")
        s = stores.sync(db, roles)
        check("the first sync sends the local copy", not any(x.get("error") for x in s.values()), json.dumps(s)[:200])
        same(db, roles, "after the first sync")
        check("restart", compose(services, "restart") and all(dk.ready(services).values()))
        s = stores.sync(db, roles)
        check("after a restart nothing needs sending", all(x.get("unchanged") for x in s.values()),
              json.dumps({k: (x.get("unchanged"), x.get("healed")) for k, x in s.items()}))
        same(db, roles, "after a restart")
        if len(roles) == 2:
            last = roles[-1]
            c = stores.choice(last)
            stores.set_choice(last, c["backend"], c["url"], {"managed": "docker", "namespace": "kept"})
            stores.sync(db, roles)
            r = dk.up({last: services[last]})
            check("starting it again keeps the options chosen for it",
                  stores.choice(last)["options"].get("namespace") == "kept", json.dumps(stores.choice(last)["options"]))
            running = {x["service"]: x["state"] for x in dk.status()}
            check("starting the %s role again leaves the %s role's container running" % (last, roles[0]),
                  r["ok"] and running.get(services[roles[0]]) == "running", json.dumps(running))
            s = stores.sync(db, roles)
            check("…and nothing needs sending to either", all(x.get("unchanged") for x in s.values()),
                  json.dumps({k: x.get("unchanged") for k, x in s.items()}))

        print("\n⑤ [%s] the containers removed and created again (what a Docker reinstall does)" % what)
        check("containers removed", compose(services, "down") and not dk.status())
        r = dk.up(services)
        check("created again from the same folders", r["ok"], json.dumps(r.get("why", "")))
        s = stores.sync(db, roles)
        check("★nothing needs sending — the data survived the containers★",
              all(x.get("unchanged") for x in s.values()),
              json.dumps({k: (x.get("unchanged"), x.get("healed")) for k, x in s.items()}))
        same(db, roles, "after the containers were recreated")

        print("\n⑥ [%s] the data folders emptied" % what)
        compose(services, "down")
        folders = []
        for name in services.values():
            b = stores.spec(name)
            for d in dk.data_dirs(b):
                empty_folder(d, b.docker.image)
                folders.append(d)
        left = [d for d in folders if not _is_empty(d)]
        check("the data folders are really empty", not left, ", ".join(left))
        r = dk.up(services)
        s = stores.sync(db, roles)
        check("the next sync notices and refills both", r["ok"] and all(x.get("healed") for x in s.values()),
              json.dumps({k: x.get("healed") for k, x in s.items()}))
        same(db, roles, "after refilling")

        print("\n⑦ [%s] the whole contract, against the containers" % what)
        contract.run(db, mem, roles, check)
    finally:
        compose(services, "down", "--remove-orphans")
        db.close()


def live() -> str:
    """Run the live rounds; → what was measured, for the last line."""
    if not os.environ.get("BRAIN_TEST_DOCKER") or os.environ.get("BRAIN_TEST_DOCKER") == "0":
        print("\n(④–⑦ skipped — set BRAIN_TEST_DOCKER=1 to start real containers)")
        return "offline only (BRAIN_TEST_DOCKER is not set)"
    state = dk.available()
    # ⛔ asked for and not possible is a failure, not a skip — a green that measured nothing looks like one that did
    check("Docker is there to run the containers asked for (BRAIN_TEST_DOCKER=%s)" % os.environ["BRAIN_TEST_DOCKER"],
          state["ok"], state.get("why", ""))
    if not state["ok"]:
        return "not measured (%s)" % state["why"]
    rounds = chosen_rounds()
    check("there is something to run", bool(rounds), os.environ.get("BRAIN_TEST_DOCKER", ""))
    for services in rounds:
        live_round(services)
    return "measured live: " + ", ".join(" + ".join(s.values()) for s in rounds)


def main() -> int:
    offline()
    said = live()
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the Docker databases keep their data in a folder of your own — %s" % said)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
        for k, v in _SAVED.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    sys.exit(code)
