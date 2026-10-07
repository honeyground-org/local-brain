#!/usr/bin/env python3
"""★The Docker databases keep their data through a restart, a removal and a reinstall★ (local · budget 0)

## What this holds down (§brain/dockerstores.py)

Always (no Docker needed):
  ① the compose file keeps the data in ★plain folders under the brain's home★ (no Docker volume — a Docker
     reset deletes those), binds ports to 127.0.0.1, restarts unless stopped, pins its images, and names the
     Neo4j password ★without its value★
  ② the password is generated once (0600), reused, and every other key in secrets.json is kept
  ③ a dry run starts nothing, writes nothing, and says what it would do

Only with `BRAIN_TEST_DOCKER=1` and a running Docker (it pulls and starts two containers, on other ports,
under its own project name, and removes them at the end):
  ④ after `restart`, the same answers — without sending anything again
  ⑤ after the containers are ★removed★ and created again (what a Docker reinstall does), the same answers
     — still without sending anything: the data was in the folders all along
  ⑥ with the data folders emptied, the next sync notices and refills both from the local copy

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
         "BRAIN_ADAPTIVE_LEARN", "BRAIN_TRANSLIT_MS", "BRAIN_DOCKER_PROJECT", "BRAIN_QDRANT_PORT",
         "BRAIN_NEO4J_HTTP_PORT", "BRAIN_NEO4J_BOLT_PORT", "NEO4J_PASSWORD")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_EMBED_DIM="24", BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0",
                  BRAIN_DOCKER_PROJECT="lbverify%s" % uuid.uuid4().hex[:6],
                  BRAIN_QDRANT_PORT="16333", BRAIN_NEO4J_HTTP_PORT="17474", BRAIN_NEO4J_BOLT_PORT="17687")
for _k in ("BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE", "NEO4J_PASSWORD"):
    os.environ.pop(_k, None)

from brain import dockerstores as dk, engines, graphstore, store, stores  # noqa: E402
from tests import _stores_fixture as fx  # noqa: E402

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
    print("① the compose file")
    text = dk.compose_text(("vector", "graph"))
    home = os.path.realpath(dk.base_dir())
    mounts = [l.split('"')[1] for l in text.splitlines() if l.strip().startswith('- "') and ":/" in l
              and not l.strip().startswith('- "127.')]
    check("every mount is a plain folder under the brain's home", mounts
          and all(os.path.realpath(m.split(":/")[0]).startswith(home) for m in mounts), "%d mounts" % len(mounts))
    check("★no Docker volume★ (a Docker reset deletes those)",
          not any(l.startswith("volumes:") for l in text.splitlines()))
    check("ports bound to 127.0.0.1 only", all("127.0.0.1:" in l for l in text.splitlines()
                                               if l.strip().startswith('- "') and l.count(":") == 2))
    check("restarts unless stopped (back after a reboot)", text.count("restart: unless-stopped") == 2)
    check("pinned images, never :latest", all(":" in v and "latest" not in v and any(c.isdigit() for c in v)
                                             for v in dk.IMAGES.values()), json.dumps(dk.IMAGES))
    check("the Neo4j password is named, never assigned", "NEO4J_AUTH" in text
          and "NEO4J_AUTH=" not in text and "NEO4J_AUTH:" not in text)

    print("\n② the password")
    with open(_SECRETS, "w", encoding="utf-8") as fh:
        json.dump({"gemini_api_key": "keep-me"}, fh)
    pw = dk.password()
    mode = stat.S_IMODE(os.stat(_SECRETS).st_mode)
    data = json.load(open(_SECRETS))
    check("generated once, private (0600)", len(pw) >= 24 and mode == 0o600, oct(mode))
    check("every other key is kept", data.get("gemini_api_key") == "keep-me")
    check("reused, not replaced", dk.password() == pw)
    check("and the compose file never holds it", pw not in dk.compose_text(("vector", "graph")))

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


def same(db, label: str) -> None:
    c = stores.check(db)
    v, g = c.get("vector", {}), c.get("graph", {})
    check("%s: identical answers" % label,
          v.get("queries") and v.get("top1_agree") == v.get("queries") and v.get("overlap_at_10") == 1.0
          and g.get("neighbors_same") == g.get("docs") and g.get("edges_same"),
          json.dumps({"vector": {k: v.get(k) for k in ("queries", "top1_agree", "chunks_store", "error")},
                      "graph": {k: g.get(k) for k in ("docs", "neighbors_same", "edges_store", "error")}}))


def compose(*args: str) -> bool:
    env = dict(os.environ, NEO4J_AUTH="neo4j/" + dk.password())
    return subprocess.run(["docker", "compose", "-f", dk.compose_path(), "-p", dk.project()] + list(args),
                          capture_output=True, text=True, env=env, timeout=600).returncode == 0


def live() -> None:
    if os.environ.get("BRAIN_TEST_DOCKER") != "1":
        print("\n(④–⑥ skipped — set BRAIN_TEST_DOCKER=1 to start real containers)")
        return
    state = dk.available()
    if not state["ok"]:
        print("\n(④–⑥ skipped — %s)" % state["why"])
        return
    fx.write_corpus(_TMP)
    db = store.connect()
    store.reindex(db, recalibrate=False, refresh_history=False)
    fx.fill_vectors(db)
    fx.fill_questions(db)
    try:
        print("\n④ up, sync, restart")
        r = dk.up()
        check("both databases start and answer", r["ok"], json.dumps(r.get("roles") or r.get("why")))
        if not r["ok"]:
            return
        s = stores.sync(db)
        check("the first sync sends the local copy", not any(x.get("error") for x in s.values()))
        same(db, "after the first sync")
        check("restart", compose("restart") and dk._wait_ready(("vector", "graph"), dk.password()) and True)
        s = stores.sync(db)
        check("after a restart nothing needs sending", all(x.get("unchanged") for x in s.values()),
              json.dumps({k: (x.get("unchanged"), x.get("healed")) for k, x in s.items()}))
        same(db, "after a restart")

        print("\n⑤ the containers removed and created again (what a Docker reinstall does)")
        check("containers removed", compose("down") and not dk.status())
        r = dk.up()
        check("created again from the same folders", r["ok"])
        s = stores.sync(db)
        check("★nothing needs sending — the data survived the containers★",
              all(x.get("unchanged") for x in s.values()),
              json.dumps({k: (x.get("unchanged"), x.get("healed")) for k, x in s.items()}))
        same(db, "after the containers were recreated")

        print("\n⑥ the data folders emptied")
        compose("down")
        for dirs in dk.data_dirs().values():
            for d in dirs:
                shutil.rmtree(d, ignore_errors=True)
        r = dk.up()
        s = stores.sync(db)
        check("the next sync notices and refills both", r["ok"] and all(x.get("healed") for x in s.values()),
              json.dumps({k: x.get("healed") for k, x in s.items()}))
        same(db, "after refilling")
    finally:
        compose("down")


def main() -> int:
    offline()
    live()
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the Docker databases keep their data in a folder of your own")
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
