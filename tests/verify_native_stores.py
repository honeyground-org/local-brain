#!/usr/bin/env python3
"""The databases without Docker — what `brain stores --native` and `--stores native` promise (2026-11-07).

Measured on the reference Mac the day this was written: Qdrant and Neo4j ran in Docker, Docker Desktop did
not start at sign-in, so after every restart both stayed down until someone opened Docker by hand. The
native install downloads the official builds and makes them services of the machine itself. This holds:

  ① every native declaration is pinned (a version, an https URL, a SHA-256) and turns usage reporting off
  ② a download whose checksum differs is refused before anything is unpacked (★control★: the right one unpacks)
  ③ an archive that reaches outside its folder (`..`, an absolute path, a link out) is refused
  ④ the unpacked home: one top folder, or a macOS bundle's Contents/Home
  ⑤ the configs bind 127.0.0.1 and turn reporting off; ★the password never reaches a command line★
  ⑥ `--stores native` (and `--recommended`) choose native; a backend with no native build stays on Docker
  ⑦ the service carries its environment and folder — launchd and systemd (a reboot starts it again)
  ⑧ the Docker container on the same port is stopped first; something foreign on the port is refused

Nothing here reaches the network, Docker or a real service manager.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-native-")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as _fh:
    json.dump({"sources": []}, _fh)

from brain import dockerstores, install, nativestores, scheduler, stores  # noqa: E402

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:200]) if detail else ""))
    if not ok:
        FAILS.append(label)


def _tar(path: str, members: dict, links: dict = None) -> str:
    """A .tar.gz with {name: bytes} files and {name: target} symlinks; returns its sha256."""
    with tarfile.open(path, "w:gz") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            t.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            t.addfile(info)
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def pinned() -> None:
    print("=" * 72 + "\n① every native declaration is pinned and quiet\n" + "=" * 72)
    decl = nativestores.declared()
    check("Qdrant and Neo4j declare a native install", {"qdrant", "neo4j"} <= set(decl), sorted(decl))
    for name, b in sorted(decl.items()):
        n = b.native
        bad = [p for p, (u, h) in n.files.items() if not (u.startswith("https://") and re.fullmatch(r"[0-9a-f]{64}", h))]
        check("%s: every archive is https with a SHA-256" % name, not bad, bad)
        check("%s: a version, never latest" % name, bool(n.version) and "latest" not in n.version, n.version)
        check("%s: says how usage reporting is off" % name, bool(n.telemetry))
        if n.runtime:
            rt = nativestores.RUNTIMES.get(n.runtime)
            check("%s: its runtime %s is pinned for the same platforms" % (name, n.runtime),
                  rt is not None and set(n.files) <= set(rt["files"]), n.runtime)
    check("the platform names this machine", re.fullmatch(r"[a-z]+-[a-z0-9_]+", nativestores.platform()) is not None,
          nativestores.platform())


def checksum() -> None:
    print("\n" + "=" * 72 + "\n② a wrong checksum is refused before unpacking\n" + "=" * 72)
    src = os.path.join(_TMP, "thing.tar.gz")
    good = _tar(src, {"thing/bin/run": b"#!/bin/sh\n"})
    r = nativestores.fetch("thing", "1.0", {"test-x": (src, "0" * 64)}, say=lambda m: None, plat="test-x")
    check("a mismatch fails", not r["ok"] and "checksum" in r.get("why", ""), r.get("why"))
    check("…and nothing was unpacked", not os.path.isdir(os.path.join(nativestores.install_dir("thing", "1.0"), "unpacked"))
          or not os.listdir(os.path.join(nativestores.install_dir("thing", "1.0"), "unpacked")))
    r = nativestores.fetch("thing", "1.0", {"test-x": (src, good)}, say=lambda m: None, plat="test-x")
    check("★control★ — the right checksum unpacks", r["ok"] and os.path.isfile(os.path.join(r["home"], "bin", "run")),
          r)
    r2 = nativestores.fetch("thing", "1.0", {"test-x": (src, good)}, say=lambda m: None, plat="test-x")
    check("a second call reuses it", r2.get("reused") is True, r2)
    r3 = nativestores.fetch("thing", "1.0", {"other": (src, good)}, say=lambda m: None, plat="test-x")
    check("a platform without a build says so", not r3["ok"] and "no thing build" in r3["why"], r3)


def unsafe() -> None:
    print("\n" + "=" * 72 + "\n③ an archive that leaves its folder is refused\n" + "=" * 72)
    for label, members, links in (("a `..` path", {"../escape": b"x"}, None),
                                  ("an absolute path", {"/tmp/escape": b"x"}, None),
                                  ("a link out of the folder", {"a/ok": b"x"}, {"a/out": "../../../etc"})):
        p = os.path.join(_TMP, "bad.tar.gz")
        _tar(p, members, links)
        dest = tempfile.mkdtemp(dir=_TMP)
        try:
            nativestores._extract(p, dest)
            refused = False
        except ValueError:
            refused = True
        check("%s is refused" % label, refused)
    p = os.path.join(_TMP, "fine.tar.gz")
    _tar(p, {"a/lib/x": b"x"}, {"a/lib/y": "x"})
    dest = tempfile.mkdtemp(dir=_TMP)
    nativestores._extract(p, dest)
    check("★control★ — a link inside the folder unpacks", os.path.islink(os.path.join(dest, "a", "lib", "y")))


def homes() -> None:
    print("\n" + "=" * 72 + "\n④ the unpacked home\n" + "=" * 72)
    a = tempfile.mkdtemp(dir=_TMP)
    os.makedirs(os.path.join(a, "neo4j-community-x", "bin"))
    check("one top folder → that folder", nativestores._home_of(a) == os.path.join(a, "neo4j-community-x"))
    b = tempfile.mkdtemp(dir=_TMP)
    os.makedirs(os.path.join(b, "jdk-21-jre", "Contents", "Home", "bin"))
    check("a macOS bundle → Contents/Home", nativestores._home_of(b) == os.path.join(b, "jdk-21-jre", "Contents", "Home"))
    c = tempfile.mkdtemp(dir=_TMP)
    open(os.path.join(c, "qdrant"), "w", encoding="utf-8").close()
    check("★control★ — a bare binary → the folder itself", nativestores._home_of(c) == c)


def layouts() -> None:
    print("\n" + "=" * 72 + "\n⑤ configs bind 127.0.0.1, reporting off, no password on a command line\n" + "=" * 72)
    secret = "s3cret-never-on-argv"
    sfile = os.path.join(_TMP, "sec")
    with open(sfile, "w", encoding="utf-8") as fh:
        fh.write(secret)
    for name in ("qdrant", "neo4j"):
        b = stores.spec(name)
        ctx = {"home": os.path.join(_TMP, name + "-home"), "data": nativestores.data_dirs(b),
               "ports": nativestores.ports(b), "secret_file": sfile, "runtime_home": "/jre",
               "platform": "linux-x64"}
        lay = b.native.layout(ctx)
        text = "\n".join(lay.get("files", {}).values())
        check("%s: listens on 127.0.0.1 only" % name, "127.0.0.1" in text and "0.0.0.0" not in text)
        check("%s: usage reporting off in its config" % name,
              "telemetry_disabled: true" in text or "dbms.usage_report.enabled=false" in text)
        argvs = [lay["argv"]] + list(lay.get("init") or [])
        check("%s: the password is on no command line" % name, not any(secret in " ".join(a) for a in argvs))
        if name == "neo4j":
            check("neo4j: the first password comes from the 0600 file (`@file`)",
                  any(("@" + sfile) in a for a in lay["init"]), lay["init"])
            check("neo4j: runs on the Java brain fetched", lay["env"].get("JAVA_HOME") == "/jre")
            check("neo4j: a small heap and page cache", "heap.max_size=512m" in text and "pagecache.size=128m" in text)
        win = b.native.layout(dict(ctx, platform="windows-x64"))
        check("%s: Windows starts the Windows build" % name,
              win["argv"][0].endswith(".exe") or win["argv"][0].endswith(".bat"), win["argv"][0])


def plans() -> None:
    print("\n" + "=" * 72 + "\n⑥ --stores native is the recommended way; a backend without one stays on Docker\n" + "=" * 72)
    p = install.store_plan("native")
    check("--stores native → both roles native", p == {"vector": ("qdrant", "", "native"), "graph": ("neo4j", "", "native")}, p)
    p = install.store_plan("docker")
    check("★control★ — --stores docker → both in Docker", all(v[2] == "docker" for v in p.values()) and len(p) == 2, p)
    p = install.store_plan("", vector="chroma")
    check("--vector-store chroma (no native build) → Docker, as before", p == {"vector": ("chroma", "", "docker")}, p)
    p = install.store_plan("", vector="qdrant")
    check("--vector-store qdrant → native", p == {"vector": ("qdrant", "", "native")}, p)
    p = install.store_plan("", graph="neo4j=http://db:7474")
    check("an address is used as given", p == {"graph": ("neo4j", "http://db:7474", "native")}, p)
    src = open(os.path.join(ROOT, "brain", "install.py"), encoding="utf-8").read()
    check("--recommended chooses native", 'a.stores = a.stores or "native"' in src)


def services() -> None:
    print("\n" + "=" * 72 + "\n⑦ the service keeps its environment — and starts again after a restart\n" + "=" * 72)
    real_home, real_replace = os.environ.get("HOME"), scheduler._launchd_replace
    os.environ["HOME"] = os.path.join(_TMP, "fakehome")

    class _P:
        returncode, stderr, stdout = 0, "", ""
    scheduler._launchd_replace = lambda label, plist, **k: _P()
    try:
        r = scheduler._launchd_service("com.local-brain.test", ["/x/neo4j", "console"], "/x/log",
                                       env={"JAVA_HOME": "/jre"}, cwd="/x")
        body = open(r["path"], encoding="utf-8").read()
        check("launchd: starts at sign-in (RunAtLoad) and after a crash (KeepAlive)",
              "<key>RunAtLoad</key><true/>" in body and "<key>KeepAlive</key>" in body)
        check("launchd: carries JAVA_HOME and its folder",
              "<key>JAVA_HOME</key><string>/jre</string>" in body and "<key>WorkingDirectory</key><string>/x</string>" in body)
        r = scheduler._launchd_service("com.local-brain.test2", ["/x/run"], "/x/log")
        body = open(r["path"], encoding="utf-8").read()
        check("★control★ — no env asked, none written", "EnvironmentVariables" not in body)
        real_which, real_run = scheduler._which, scheduler.subprocess.run
        scheduler._which = lambda n: True
        scheduler.subprocess.run = lambda *a, **k: _P()
        try:
            r = scheduler._systemd_user_service("com.local-brain.test", ["/x/neo4j", "console"], "/x/log",
                                                env={"JAVA_HOME": "/jre"}, cwd="/x")
        finally:
            scheduler._which, scheduler.subprocess.run = real_which, real_run
        unit = open(r["path"], encoding="utf-8").read()
        check("systemd: Environment and WorkingDirectory, enabled for sign-in",
              'Environment="JAVA_HOME=/jre"' in unit and "WorkingDirectory=/x" in unit
              and "WantedBy=default.target" in unit and "Restart=on-failure" in unit)
    finally:
        scheduler._launchd_replace = real_replace
        if real_home is not None:
            os.environ["HOME"] = real_home


def ports_first() -> None:
    print("\n" + "=" * 72 + "\n⑧ the Docker container on the same port goes first; a stranger is refused\n" + "=" * 72)
    calls = {"stopped": [], "installed": []}
    answering = {"v": True}

    class _S:
        def install_service(self, label, argv, log, env=None, cwd=""):
            calls["installed"].append(label)
            answering["v"] = True
            return {"ok": True}
    saved = (nativestores.prepare, nativestores._ping, nativestores._registered, dockerstores.managed,
             dockerstores.stop, scheduler.active, nativestores.installable, nativestores.time.sleep)
    nativestores.prepare = lambda b, say=print, plat="": {"ok": True, "layout": {"argv": ["/x"]}}
    nativestores._ping = lambda b: "1.0" if answering["v"] else ""
    nativestores._registered = lambda s, b: False
    nativestores.installable = lambda role="": {"qdrant": stores.spec("qdrant")}
    nativestores.time.sleep = lambda s: None
    scheduler.active = lambda refresh=False: _S()

    def stop(services=()):
        calls["stopped"].extend(services)
        answering["v"] = False                    # the container is gone: the port is free
        return True
    dockerstores.stop = stop
    try:
        dockerstores.managed = lambda: {"vector": "qdrant"}
        r = nativestores.up({"vector": "qdrant"}, say=lambda m: None)
        check("the Docker qdrant is stopped before the native one starts",
              calls["stopped"] == ["qdrant"] and calls["installed"] == ["com.local-brain.qdrant"] and r["ok"],
              (calls, r.get("why")))
        check("…and the role is now brain's native one", stores.choice("vector")["options"].get("managed") == "native")
        calls["stopped"].clear()
        calls["installed"].clear()
        answering["v"] = True
        dockerstores.managed = lambda: {}
        r = nativestores.up({"vector": "qdrant"}, say=lambda m: None)
        check("something foreign answering on the port is refused, nothing registered",
              not r["ok"] and "already answers" in r["why"] and not calls["installed"], r.get("why"))
    finally:
        (nativestores.prepare, nativestores._ping, nativestores._registered, dockerstores.managed,
         dockerstores.stop, scheduler.active, nativestores.installable, nativestores.time.sleep) = saved


def main() -> int:
    pinned()
    checksum()
    unsafe()
    homes()
    layouts()
    plans()
    services()
    ports_first()
    print("\n" + ("❌ %d failed" % len(FAILS) if FAILS else "✅ all passed"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
