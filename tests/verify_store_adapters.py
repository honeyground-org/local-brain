#!/usr/bin/env python3
"""★A storage backend is one file★ — dropped into `brain/backends/`, it is a choice everywhere. (local · budget 0)

## Why (user instruction, 2026-10-10)

*"Any database must be possible — adding an adapter must be all it takes — and every option must be
installable on its own."*

## What this holds down (§brain/stores.py "One file per backend")

  ① every backend that ships loads, meets its role's contract, and — when it runs in Docker — pins its
     image, binds 127.0.0.1 only, keeps its data under the brain's home and names its secrets by name
  ② ★the plug test★ — on a copy of brain, two new files (a vector and a graph database, each in a JSON
     file) and ★nothing else edited★:
       · `brain stores --set`, the new option's flag, `brain stores --docker`, `brain install
         --vector-store/--graph-store` and the status screen all offer them
       · the whole contract (§tests/_store_contract) passes against them — identical answers, only the
         difference sent, refilled after a loss, the local copy answering through an outage
  ③ controls — files that must ★not★ be offered, each with its reason on the screen, the rest unharmed:
       a missing contract method · a file that does not load · a name that is not its file · an image
       pinned to `latest` · an image that does not say how its usage reporting is turned off · an
       option whose kind clashes with another backend's
  ④ each role installs on its own: `--graph-store` alone touches only the graph, `--stores docker` is
     both defaults, `NAME=URL` points at a server that runs already — and starting one role in Docker
     keeps the other role's container in the compose file

How to run:  PYTHONPATH=. python3 tests/verify_store_adapters.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-adapters-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_EMBED_DIM", "BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE",
         "BRAIN_VECTOR_STORE_URL", "BRAIN_GRAPH_STORE_URL", "BRAIN_ADAPTIVE_LEARN", "BRAIN_TRANSLIT_MS", "BRAIN_LANG")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ.update(BRAIN_HOME=os.path.join(_TMP, "home"), BRAIN_CONFIG=os.path.join(_TMP, "config.json"),
                  BRAIN_EMBED_DIM="16", BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0", BRAIN_LANG="en")
for _k in _KEYS[3:7]:
    os.environ.pop(_k, None)

from brain import dockerstores as dk, stores  # noqa: E402
from tests import _store_contract as contract  # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


# ── the files the plug test drops in ────────────────────────────────────────
PLUG_VECTOR = '''"""A vector database in a JSON file — dropped in by tests/verify_store_adapters.py."""
import json, os, urllib.parse, urllib.request
from brain import stores


def _path(url):
    p = urllib.parse.urlsplit(url)
    path = urllib.request.url2pathname(p.path)
    if p.scheme != "file" or p.netloc or not os.path.isdir(os.path.dirname(path)):
        raise stores.StoreError("plugvec: %s is not reachable" % url)
    return path


class PlugVectors:
    backend, remote = "plugvec", True

    def __init__(self, c):
        self.c, self.url, self.depth = c, c["url"], int(stores.opt(c, "plug_depth"))

    def _load(self):
        try:
            with open(_path(self.url)) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        with open(_path(self.url), "w") as fh:
            json.dump(d, fh)

    def _col_name(self, model, dim):
        return "%s%s_%d" % (stores.opt(self.c, "namespace") or "", model, dim)

    def _held(self, model, dim):
        col = self._load().get(self._col_name(model, dim))
        if col is None:
            raise stores.StoreError("plugvec: HTTP 404 no such collection")
        return col

    def target(self, model, dim):
        return "plugvec:%s/%s%s_%d" % (self.url, stores.opt(self.c, "namespace") or "", model, dim)

    def ping(self):
        _path(self.url)
        return {"ok": True, "version": "plug depth %d" % self.depth}

    def ensure(self, model, dim):
        d = self._load()
        d.setdefault(self._col_name(model, dim), {})
        self._save(d)

    def drop(self, model, dim):
        d = self._load()
        d.pop(self._col_name(model, dim), None)
        self._save(d)

    def reset(self, model, dim):
        self.drop(model, dim)
        self.ensure(model, dim)

    def upsert(self, model, dim, rows):
        d = self._load()
        col = d.setdefault(self._col_name(model, dim), {})
        for doc, chunk, v in rows:
            col["%d:%d" % (doc, chunk)] = [float(x) for x in v]
        self._save(d)

    def delete_docs(self, model, dim, ids):
        d = self._load()
        col, gone = d.setdefault(self._col_name(model, dim), {}), {int(i) for i in ids}
        for k in [k for k in col if int(k.split(":")[0]) in gone]:
            del col[k]
        self._save(d)

    def search(self, db, q, model, dim, n):
        best = {}
        for k, v in self._held(model, dim).items():
            doc, s = int(k.split(":")[0]), sum(a * b for a, b in zip(q, v))
            if s > best.get(doc, -2.0):
                best[doc] = s
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def count(self, db, model, dim):
        return len(self._held(model, dim))


BACKEND = stores.Backend(
    name="plugvec", role="vector", title="Plug test vectors", make=PlugVectors, url="",
    options=(stores.Option("plug_depth", 2, "how deep the plug test digs"),),
    docker=stores.Docker(image="example.invalid/plugvec:1.0",
                         ports=(stores.Port("api", 7001, "BRAIN_PLUGVEC_PORT"),), data=(("", "/data"),),
                         env={"PLUGVEC_KEY": "{secret}"}, generate_secret=True, telemetry_off=()),
    secret=stores.Secret(env="PLUGVEC_KEY", field="plugvec_key"),
)
'''

PLUG_GRAPH = '''"""A graph database in a JSON file — dropped in by tests/verify_store_adapters.py."""
import json, os, urllib.parse, urllib.request
from brain import stores


class PlugGraph:
    backend, remote = "pluggraph", True

    def __init__(self, c):
        self.url = c["url"]
        self.ns = str(stores.opt(c, "namespace") or stores.namespace())

    def _path(self):
        p = urllib.parse.urlsplit(self.url)
        path = urllib.request.url2pathname(p.path)
        if p.scheme != "file" or p.netloc or not os.path.isdir(os.path.dirname(path)):
            raise stores.StoreError("pluggraph: %s is not reachable" % self.url)
        return path

    def _load(self):
        path = self._path()
        try:
            with open(path) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}
        g = d.get(self.ns) or {"nodes": {}, "edges": []}
        return d, g

    def _save(self, d, g):
        d[self.ns] = g
        with open(self._path(), "w") as fh:
            json.dump(d, fh)

    def _E(self):
        return {tuple(e) for e in self._load()[1]["edges"]}

    def target(self):
        return "pluggraph:%s#%s" % (self.url, self.ns)

    def ping(self):
        self._path()
        return {"ok": True, "version": "plug"}

    def ensure(self):
        self._save(*self._load())

    def drop(self):
        d, _ = self._load()
        d.pop(self.ns, None)
        with open(self._path(), "w") as fh:
            json.dump(d, fh)

    def reset(self):
        self.drop()
        self.ensure()

    def upsert_nodes(self, nodes):
        d, g = self._load()
        for i, name in nodes:
            g["nodes"][str(i)] = name
        self._save(d, g)

    def delete_nodes(self, ids):
        d, g = self._load()
        gone = {int(i) for i in ids}
        for i in gone:
            g["nodes"].pop(str(i), None)
        g["edges"] = [e for e in g["edges"] if not (set(e) & gone)]
        self._save(d, g)

    def add_edges(self, pairs):
        d, g = self._load()
        E = {tuple(e) for e in g["edges"]}
        E |= {(int(a), int(b)) for a, b in pairs if str(a) in g["nodes"] and str(b) in g["nodes"]}
        g["edges"] = sorted(E)
        self._save(d, g)

    def remove_edges(self, pairs):
        d, g = self._load()
        g["edges"] = [e for e in g["edges"] if tuple(e) not in {(int(a), int(b)) for a, b in pairs}]
        self._save(d, g)

    def counts(self):
        g = self._load()[1]
        return len(g["nodes"]), len(g["edges"])

    def neighbors(self, db, ids):
        E = self._E()
        return {int(i): {b for a, b in E if a == i} | {a for a, b in E if b == i} for i in ids}

    def incoming(self, db, did):
        return {a for a, b in self._E() if b == did}

    def linked(self, db, a, b):
        E = self._E()
        return (a, b) in E or (b, a) in E

    def targets(self, db):
        return {b for _, b in self._E()}

    def edges(self, db):
        return sorted(self._E())


BACKEND = stores.Backend(name="pluggraph", role="graph", title="Plug test graph", make=PlugGraph, url="")
'''

# ⛔ controls — each must be refused with its reason, and must not take any other backend down
CONTROLS = {
    "plugbroken_missing": ("missing count", PLUG_VECTOR.replace("plugvec", "plugbroken_missing")
                           .replace("    def count(self, db, model, dim):\n        return len(self._held(model, dim))\n",
                                    "").replace("BRAIN_PLUGVEC_PORT", "BRAIN_PLUGBROKEN_PORT")),
    "plugbroken_import": ("does not load", 'raise RuntimeError("this file cannot load")\n'),
    "plugbroken_name": ("must match", PLUG_GRAPH.replace('name="pluggraph"', 'name="some_other_name"')),
    "plugbroken_latest": ("not pinned", PLUG_VECTOR.replace("plugvec", "plugbroken_latest")
                          .replace("plugbroken_latest:1.0", "plugbroken_latest:latest")
                          .replace("BRAIN_PLUGVEC_PORT", "BRAIN_PLUGLATEST_PORT")),
    "plugbroken_telemetry": ("usage reporting", PLUG_VECTOR.replace("plugvec", "plugbroken_telemetry")
                             .replace("BRAIN_PLUGVEC_PORT", "BRAIN_PLUGTELEMETRY_PORT")
                             .replace(", telemetry_off=()", "")),
    # sorts after qdrant on purpose: the shipped backend that declared `exact` first keeps it (§stores.problems)
    "zplug_kind": ("one flag cannot be both", PLUG_GRAPH.replace("pluggraph", "zplug_kind").replace(
        'make=PlugGraph, url=""', 'make=PlugGraph, url="", options=(stores.Option("exact", "yes", "clashes"),)')),
}

DRIVER = r'''
import io, json, os, subprocess, sys
from contextlib import redirect_stdout
FAILS = []
def check(label, ok, detail=""):
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)

from brain import cli, dockerstores as dk, install, stores
from tests import _store_contract as contract

def run(*args):
    r = subprocess.run([sys.executable, "-m"] + list(args), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=os.environ)
    return r.returncode, r.stdout + r.stderr

print("② the plug test — two new files, nothing else edited")
reg = stores.backends()
check("both new backends are discovered", "plugvec" in stores.names("vector") and "pluggraph" in stores.names("graph"),
      "vector: %s · graph: %s" % (stores.names("vector"), stores.names("graph")))
check("…and the shipped ones are unharmed", {"qdrant", "neo4j"} <= set(reg))
rc, help_text = run("brain.cli", "stores", "--help")
check("its option became a flag: brain stores --plug-depth", rc == 0 and "--plug-depth" in help_text
      and "plugvec: how deep the plug test digs" in " ".join(help_text.split()))
check("`--set` and `--docker` offer it", "plugvec" in help_text and "pluggraph" in help_text)
rc, ihelp = run("brain.install", "--help")
flat = " ".join(ihelp.split())
check("the installer offers it per role: --vector-store … plugvec, --graph-store … pluggraph",
      rc == 0 and "vector: sqlite · plugvec" in flat and "pluggraph" in flat, flat[flat.find("--vector-store"):][:140])
db_dir = os.path.join(os.environ["BRAIN_HOME"], "plugdb")
os.makedirs(db_dir, exist_ok=True)
import pathlib
vurl, gurl = pathlib.Path(db_dir, "vec.json").as_uri(), pathlib.Path(db_dir, "graph.json").as_uri()
db, mem = contract.prepare(os.environ["BRAIN_PLUG_ROOT"])
rc, out = run("brain.cli", "stores", "--set", "vector=plugvec", "--url", vurl, "--plug-depth", "3")
cfg = json.load(open(os.environ["BRAIN_CONFIG"]))
check("brain stores --set vector=plugvec --plug-depth 3 → config.json", rc == 0
      and cfg["stores"]["vector"] == {"backend": "plugvec", "url": vurl, "plug_depth": 3}, json.dumps(cfg.get("stores")))
check("…and the first sync filled it", "sent" in out and os.path.exists(os.path.join(db_dir, "vec.json")), out.strip().splitlines()[-1][:120] if out.strip() else "")
rc, out = run("brain.cli", "stores", "--set", "graph=pluggraph", "--url", gurl)
check("brain stores --set graph=pluggraph", rc == 0 and "pluggraph" in json.dumps(json.load(open(os.environ["BRAIN_CONFIG"]))["stores"]))
rc, out = run("brain.cli", "stores", "--set", "graph=plugvec")
check("refused for the wrong role", rc != 0 and "cannot serve the graph role" in out, out.strip()[:120])
rc, out = run("brain.cli", "stores", "--set", "vector=plugvec", "--database", "x")
check("refused: an option it does not declare", rc != 0 and "takes no option 'database'" in out, out.strip()[:120])
rc, screen = run("brain.cli", "stores")
check("the status screen shows it answering and in sync", rc == 0 and "plugvec @ " in screen
      and "plug depth 3" in screen and screen.count("in sync, answering") == 2, screen[:600])
check("the choices line lists it", "vector = sqlite · plugvec · qdrant" in screen and "pluggraph" in screen)
text = dk.compose_text({"vector": "plugvec"})
items = contract.compose_items(text)
check("`--docker plugvec` would run its pinned image on 127.0.0.1, data under the brain's home, secret by name",
      "example.invalid/plugvec:1.0" in text and items.get("ports") == ["127.0.0.1:7001:7001"]
      and [contract.mount_host(m) for m in items.get("volumes", [])] == [os.path.join(dk.base_dir(), "plugvec")]
      and items.get("environment") == ["PLUGVEC_KEY"] and "PLUGVEC_KEY=" not in text, json.dumps(items))
check("`--docker plugvec` resolves to the vector role", dk.resolve(["plugvec"]) == {"vector": "plugvec"})

print("\n   the whole contract, against the two new files")
contract.run(db, mem, ("vector", "graph"), check)

print("\n③ controls — files that must not be offered")
bad = dict(stores.broken())
for name, why in json.loads(os.environ["BRAIN_PLUG_CONTROLS"]).items():
    check("%s is not offered — %s" % (name, why), name in bad and why in bad[name] and name not in reg,
          bad.get(name, "(offered!)")[:110])
check("…the reasons are on the status screen", all(("not offered  %s:" % n) in screen for n in bad), ", ".join(bad))
rc, out = run("brain.cli", "stores", "--set", "vector=plugbroken_missing")
check("choosing one says why it is not available", rc != 0 and "not available" in out and "missing count" in out,
      out.strip()[:140])
os.environ["BRAIN_VECTOR_STORE"] = "plugbroken_missing"
c = stores.choice("vector")
check("a choice of one (by the environment) is marked broken, and the local copy answers",
      c["error"] == "broken" and type(stores.vector_backend(c)).__name__ == "SqliteVectors")
os.environ.pop("BRAIN_VECTOR_STORE")
check("the shipped backend that declared `exact` first keeps it", "qdrant" in reg and "zplug_kind" in bad)
sys.exit(1 if FAILS else 0)
'''


def shipped() -> None:
    print("① every backend that ships")
    reg = {n: b for n, b in stores.backends().items() if n != "sqlite"}
    check("each file in brain/backends/ loads and meets its role's contract", reg and not stores.broken(),
          "; ".join("%s: %s" % x for x in stores.broken()) or ", ".join(sorted(reg)))
    for role in stores.ROLES:
        check("%s has a default for `--stores docker` that runs in Docker" % role,
              stores.DOCKER_DEFAULT[role] in dk.dockerable(role))
    home = os.path.realpath(dk.base_dir())
    for name, b in sorted(dk.dockerable().items()):
        text = dk.compose_text({b.role: name})
        lines, items = text.splitlines(), contract.compose_items(text)
        mounts, published = items.get("volumes", []), items.get("ports", [])
        check("%s: pinned image, 127.0.0.1 only, data in plain folders under the brain's home, no Docker volume"
              % name, mounts and published and all(p.startswith("127.0.0.1:") for p in published)
              and all(os.path.realpath(contract.mount_host(m)).startswith(home) for m in mounts)
              and not any(l.startswith("volumes:") for l in lines) and "restart: unless-stopped" in text
              and not b.docker.image.endswith(":latest"), b.docker.image)
        secret_env = [k for k, v in b.docker.env.items() if "{secret}" in v]
        check("%s: every credential Docker reads is named, never written" % name,
              all(("- %s " % k) in text and ("%s=" % k) not in text and ("%s:" % k) not in text for k in secret_env))
        env, args = b.docker.settings()
        off = b.docker.telemetry_off
        check("%s: its own usage reporting is turned off in the compose file" % name, off is not None and all(
            (s in text) if s.startswith("--") else (s in items.get("environment", [])) for s in off),
            ", ".join(off or ()) or "(reports nothing)")


def installer() -> None:
    from brain import install
    print("\n④ each role installs on its own")
    plan = install.store_plan
    check("--graph-store neo4j → only the graph", plan("", "", "neo4j") == {"graph": ("neo4j", "")})
    check("--vector-store qdrant → only the vector role", plan("", "qdrant") == {"vector": ("qdrant", "")})
    check("--stores docker → every role's default", plan("docker") == {r: (n, "") for r, n in stores.DOCKER_DEFAULT.items()})
    check("--stores docker --graph-store sqlite → the vector role only", plan("docker", "", "sqlite") == {
        "vector": (stores.DOCKER_DEFAULT["vector"], "")})
    check("NAME=URL → a server that runs already, nothing started",
          plan("", "qdrant=http://db.example.com:6333") == {"vector": ("qdrant", "http://db.example.com:6333")})
    for bad in (("", "neo4j"), ("", "", "qdrant"), ("", "no_such_db")):
        try:
            plan(*bad)
            check("refused: %s" % " ".join(x for x in bad if x), False)
        except ValueError:
            check("refused: %s" % " ".join(x for x in bad if x), True)
    install.DRY = True
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            install.step_stores(plan("", "", "neo4j"))
    finally:
        install.DRY = False
    said = buf.getvalue()
    check("a dry run of --graph-store neo4j would start the graph and nothing else",
          "would start graph" in said and "vector" not in said.replace("vector search", ""), said.strip()[:160])
    check("…and touched nothing", not os.path.exists(dk.compose_path()))

    print("\n   starting one role in Docker keeps the other one's container")
    from brain import store
    store.save_config({"sources": [], "stores": {"graph": {"backend": "neo4j", "url": "http://127.0.0.1:7474",
                                                           "managed": "docker"}}})
    services = dk.services_for({"vector": "qdrant"})
    check("--docker vector, with the graph already in Docker → both stay in the compose file",
          services == {"vector": "qdrant", "graph": "neo4j"}, json.dumps(services))
    text = dk.compose_text(services)
    check("…so `--remove-orphans` cannot remove the graph's container", "  neo4j:" in text and "  qdrant:" in text)


def plug() -> None:
    code, drv = os.path.join(_TMP, "code"), os.path.join(_TMP, "driver")
    shutil.copytree(os.path.join(ROOT, "brain"), os.path.join(code, "brain"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(code, "tests"))
    for f in ("__init__.py", "_stores_fixture.py", "_store_contract.py", "_needs.py"):
        shutil.copy(os.path.join(ROOT, "tests", f), os.path.join(code, "tests", f))
    be = os.path.join(code, "brain", "backends")
    with open(os.path.join(be, "plugvec.py"), "w", encoding="utf-8") as fh:
        fh.write(PLUG_VECTOR)
    with open(os.path.join(be, "pluggraph.py"), "w", encoding="utf-8") as fh:
        fh.write(PLUG_GRAPH)
    for name, (_, body) in CONTROLS.items():
        with open(os.path.join(be, name + ".py"), "w", encoding="utf-8") as fh:
            fh.write(body)
    edited = [f for f in os.listdir(be) if f not in os.listdir(os.path.join(ROOT, "brain", "backends"))]
    check("the copy differs from brain only by files added to brain/backends/",
          sorted(edited) == sorted(["plugvec.py", "pluggraph.py"] + [n + ".py" for n in CONTROLS]),
          ", ".join(sorted(edited)))
    os.makedirs(drv)
    with open(os.path.join(drv, "plug_driver.py"), "w", encoding="utf-8") as fh:
        fh.write(DRIVER)
    root = os.path.join(_TMP, "plug")
    os.makedirs(root)
    env = dict(os.environ, PYTHONPATH=code, PYTHONIOENCODING="utf-8", BRAIN_HOME=os.path.join(root, "home"),
               BRAIN_CONFIG=os.path.join(root, "config.json"), BRAIN_PLUG_ROOT=root,
               BRAIN_PLUG_CONTROLS=json.dumps({n: w for n, (w, _) in CONTROLS.items()}))
    print()
    r = subprocess.run([sys.executable, os.path.join(drv, "plug_driver.py")], cwd=root, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    print(r.stdout.rstrip())
    reds = [l for l in r.stdout.splitlines() if l.startswith("❌")]
    check("the plug test ran to the end, all green", r.returncode == 0 and not reds and "③" in r.stdout,
          (r.stderr.strip().splitlines() or [""])[-1][:200] if r.returncode else "%d red" % len(reds))
    FAILS.extend("plug · " + l[2:] for l in reds)


def main() -> int:
    shipped()
    installer()
    plug()
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ a storage backend is one file — and each role installs on its own")
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
