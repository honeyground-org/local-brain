#!/usr/bin/env python3
"""★A long-running MCP server answers with the code on disk★ (local · budget 0)

## The incident (2026-10-07)

A host keeps the brain's MCP server for the whole session. On 2026-10-07, 21 of them were alive on one
machine, the oldest started 9/28 — sixteen code generations behind. Each recall they served wrote the old
code's answers into the shared index, and no fix reached a session until a human restarted it.

## What this holds down — on a copy of the package in a temp folder, never the real install

  ① after a module changes on disk, the next request is answered ★by the new code★, in ★the same process★
     (the host's pipes stay connected)
  ② requests the host sent together are all answered — nothing read before the switch is lost
  ③ (control) a half-saved file that does not import does ★not★ take the server down — it keeps answering
     with the code in hand, and switches once the file is whole again

How to run:  PYTHONPATH=. python3 tests/verify_server_refresh.py
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def main() -> int:
    if os.name == "nt":
        print("skipped — the server does not switch code on Windows (exec there ends the process)")
        return 77
    tmp = tempfile.mkdtemp(prefix="brain-refresh-")
    pkg = os.path.join(tmp, "pkg")
    shutil.copytree(os.path.join(ROOT, "brain"), os.path.join(pkg, "brain"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"sources": []}, fh)
    env = dict(os.environ, BRAIN_HOME=os.path.join(tmp, "home"), BRAIN_CONFIG=os.path.join(tmp, "config.json"),
               PYTHONPATH=pkg, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen([sys.executable, "-m", "brain.server"], cwd=pkg, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buf = b""

    def send(*msgs):
        proc.stdin.write(b"".join((json.dumps(m) + "\n").encode() for m in msgs))
        proc.stdin.flush()

    def recv(n=1, timeout=90.0):
        nonlocal buf
        out, end = [], time.time() + timeout
        while len(out) < n and time.time() < end:
            while b"\n" in buf and len(out) < n:
                line, buf = buf.split(b"\n", 1)
                out.append(json.loads(line))
            if len(out) >= n:
                break
            r, _, _ = select.select([proc.stdout], [], [], max(0.0, end - time.time()))
            if r:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                buf += chunk
        return out

    def version(resp):
        return ((resp.get("result") or {}).get("serverInfo") or {}).get("version")

    def edit(path, old, new):
        p = os.path.join(pkg, "brain", path)
        with open(p, encoding="utf-8") as fh:
            s = fh.read()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(s.replace(old, new, 1))
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))   # a clock-coarse disk still sees it

    try:
        init = {"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
        send(dict(init, id=1))
        first = recv()
        v0 = version(first[0]) if first else None
        check("the server answers", bool(v0), str(v0))
        pid = proc.pid

        print("\n① a module changed on disk → the next answer comes from the new code, same process")
        edit("server.py", 'SERVER_VERSION = "%s"' % v0, 'SERVER_VERSION = "%s-new"' % v0)
        send(dict(init, id=2), {"jsonrpc": "2.0", "method": "ping", "id": 3})
        got = {r.get("id"): r for r in recv(2)}
        check("★answered by the new code★", version(got.get(2, {})) == "%s-new" % v0,
              "version %s" % version(got.get(2, {})))
        check("in the same process — the host's pipes never closed", proc.poll() is None and proc.pid == pid)
        print("\n② nothing read before the switch is lost")
        check("both requests sent together were answered", 3 in got and "result" in got[3], str(sorted(got)))

        print("\n③ (control) a half-saved file does not take the server down")
        edit("health.py", "import ", "import (")                         # no longer imports
        send(dict(init, id=4))
        r4 = recv()
        check("it keeps answering with the code in hand", bool(r4) and version(r4[0]) == "%s-new" % v0
              and proc.poll() is None, str(version(r4[0]) if r4 else "no answer"))
        edit("health.py", "import (", "import ")
        edit("server.py", 'SERVER_VERSION = "%s-new"' % v0, 'SERVER_VERSION = "%s-fixed"' % v0)
        send(dict(init, id=5))
        r5 = recv()
        check("and switches once the file is whole again", bool(r5) and version(r5[0]) == "%s-fixed" % v0,
              str(version(r5[0]) if r5 else "no answer"))
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ a long-running server answers with the code on disk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
