#!/usr/bin/env python3
"""★The real databases give the same answers as the local copy★ — every backend that answers, measured live. (local · budget 0)

Runs against whichever databases you have running and skips (exit 77) when none answers. ★Nothing here
names a backend★: every file in `brain/backends/` is tried, at

    BRAIN_TEST_<NAME>_URL      (default: the address the backend declares)
    BRAIN_TEST_<NAME>_SECRET   or the backend's own variable (e.g. NEO4J_PASSWORD) — never secrets.json

Start them with `brain stores --docker`, or by hand, e.g.

    docker run -d -p 127.0.0.1:6333:6333 qdrant/qdrant
    NEO4J_AUTH=neo4j/<password> docker run -d -p 127.0.0.1:7474:7474 -e NEO4J_AUTH neo4j:5
    NEO4J_PASSWORD=<password> PYTHONPATH=. python3 tests/verify_stores_live.py

Everything is written under a ★fresh namespace★ and removed at the end — a database that also holds real
data is not touched. Each vector backend that answers is paired with each graph backend that answers
(round-robin), and every pair runs the whole contract (§tests/_store_contract):

  ① a sync, then the same questions to both: identical · ② an edit and a removal travel through
  indexing alone · ③ a full rebuild · ④ a database that lost its data is refilled · ⑤ a database that is
  not there: indexing still works, the local copy answers, the error is recorded, one sync catches up
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-stores-live-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_EMBED_DIM", "BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE",
         "BRAIN_VECTOR_STORE_URL", "BRAIN_GRAPH_STORE_URL", "BRAIN_ADAPTIVE_LEARN", "BRAIN_TRANSLIT_MS")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_EMBED_DIM="32", BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0")
for _k in _KEYS[3:7]:
    os.environ.pop(_k, None)

from brain import engines, stores  # noqa: E402
from tests import _needs, _store_contract as contract  # noqa: E402

# ⛔ a person's secrets.json must not hand this check the password of their own database
engines.secrets_path = lambda: os.path.join(_TMP, "secrets.json")

RUN = "verify%s" % uuid.uuid4().hex[:8]
FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def reachable() -> tuple:
    """({role: [(name, url)]}, {name: why not}) — every registered backend that answers here."""
    found = {r: [] for r in stores.ROLES}
    why = {}
    for name, b in sorted(stores.backends().items()):
        if name == "sqlite":
            continue
        url = (os.environ.get("BRAIN_TEST_%s_URL" % name.upper()) or b.url).rstrip("/")
        given = os.environ.get("BRAIN_TEST_%s_SECRET" % name.upper(), "")
        if given and b.secret:
            os.environ[b.secret.env] = given          # through the backend's own variable — the path people use
        c = {"role": b.role, "backend": name, "url": url, "options": {"namespace": RUN}}
        if b.secret and b.secret.required and not stores.secret(c):
            why[name] = "%s is not set" % b.secret.env
            continue
        try:
            b.make(c).ping()
            found[b.role].append((name, url))
        except (stores.StoreError, OSError) as exc:
            why[name] = "%s → %s" % (url, str(exc)[:80])
    return found, why


def main() -> int:
    found, why = reachable()
    for name, w in sorted(why.items()):
        print("  not tried: %s (%s)" % (name, w))
    if not any(found.values()):
        _needs.skip("no storage backend answers (%s)" % "; ".join("%s: %s" % kv for kv in sorted(why.items())),
                    "start them with `brain stores --docker`, or as shown at the top of this file")
    roles = tuple(r for r in stores.ROLES if found[r])
    rounds = max(len(found[r]) for r in roles)
    for i in range(rounds):
        chosen = {r: found[r][i % len(found[r])] for r in roles}
        print("\n" + "─" * 78 + "\nlive: %s" % ", ".join("%s → %s @ %s" % (r, n, u) for r, (n, u) in chosen.items()))
        db, mem = contract.prepare(os.path.join(_TMP, "round%d" % i),
                                   {r: {"backend": n, "url": u, "namespace": RUN} for r, (n, u) in chosen.items()})
        try:
            contract.run(db, mem, roles, check)
        finally:
            contract.drop_all(roles)
            db.close()
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ the real databases give the same answers as the local copy (%s)"
          % ", ".join(n for r in roles for n, _ in found[r]))
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
