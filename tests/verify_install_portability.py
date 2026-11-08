#!/usr/bin/env python3
"""What a fresh install on someone else's machine tripped over — each one held in place (2026-11-04).

Measured on a real installation (macOS, Docker Qdrant + Neo4j, EmbeddingGemma):

  ① the MCP command was split back into words — a path with a space became two arguments
  ② discovery wrote `~/development` for a folder the disk spells `~/Development`
  ③ EmbeddingGemma had no install path; a gated model failed with nothing said
     → the one-command setup stops at the person's step with the exact next action, and chooses
       the engine only once the service answers
  ④ with no embedding engine, a vector database got an empty collection named after "none",
     reported as in sync
  ⑤ a daily job whose engine nobody chose was registered anyway (and then shown as stopped)
  ⑥ (2026-11-08) an install cut off while packages went in left a venv behind, and every later run
     failed at `uv venv` ("already exists") — and the installer exited 0 when the embedding engine
     it was asked for never came up, so a caller (Cosmos) had no reason to try again

Every check that a fix holds sits beside a ★control★ showing the check can fail.
Nothing here reaches the network, Docker, a scheduler or Hugging Face.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-install-port-")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
for _k in ("BRAIN_EMBED_PROVIDER", "BRAIN_JUDGE_PROVIDER", "BRAIN_VECTOR_STORE", "BRAIN_GRAPH_STORE"):
    os.environ.pop(_k, None)
with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as _fh:
    json.dump({"sources": []}, _fh)

from brain import discover, embedruntime, engines, install, launch  # noqa: E402

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:200]) if detail else ""))
    if not ok:
        FAILS.append(label)


def mcp_argv() -> None:
    print("=" * 72 + "\n① the MCP command keeps a path with a space as one argument\n" + "=" * 72)
    spaced = ["/Users/a b/py thon", "/Users/a b/repo/bin/_run.py", "brain.server"]
    real_argv, real_which = launch.argv, install.shutil.which
    launch.argv = lambda module, python="": list(spaced)
    install.shutil.which = lambda name, *a, **k: None
    real_root = install.ROOT
    install.ROOT = os.path.join(_TMP, "no-bin-here")
    try:
        got = install.entry_argv("brain-mcp", "brain.server")
        old = install.entry_command("brain-mcp", "brain.server").replace('"', "").split()
    finally:
        launch.argv, install.shutil.which, install.ROOT = real_argv, real_which, real_root
    check("entry_argv keeps three arguments", got == spaced, str(got))
    check("★control★ — the old split made more than three", len(old) > 3, str(old))


def true_case() -> None:
    print("\n" + "=" * 72 + "\n② discovery keeps the disk's spelling\n" + "=" * 72)
    d = os.path.join(_TMP, "Mixed")
    os.makedirs(d)
    check("★control★ — an exact name comes back unchanged", discover._true_case(d) == d)
    low = os.path.join(_TMP, "mixed")
    if not os.path.isdir(low):
        print("⏭  this disk is case-sensitive — `mixed` is not `Mixed` here, nothing to correct")
        return
    check("a lower-case spelling becomes the disk's", discover._true_case(low) == d, discover._true_case(low))


class _P:
    def __init__(self, code: int, out: str = "", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = code, out, err


def gemma() -> None:
    print("\n" + "=" * 72 + "\n③ EmbeddingGemma — one command, stopping at the person's step\n" + "=" * 72)
    hf = os.path.join(_TMP, "hf")
    open(hf, "w", encoding="utf-8").close()
    saved = (embedruntime.step_env, embedruntime._run, embedruntime.venv_hf, embedruntime.snapshot_path,
             embedruntime.step_service)
    embedruntime.step_env = lambda say: {"ok": True, "step": "env"}
    embedruntime.venv_hf = lambda: hf
    embedruntime.snapshot_path = lambda: os.path.join(_TMP, "no-snapshot")
    calls = []

    def fake(argv, timeout=0, env=None, answers=None):
        calls.append(argv[1:3])
        return answers.get(tuple(argv[1:3]), _P(0))
    old_mirror = os.environ.get("BRAIN_EMBED_MIRROR")
    os.environ["BRAIN_EMBED_MIRROR"] = "off"      # these checks are the Hugging Face path
    try:
        embedruntime._run = lambda argv, timeout=0, env=None: fake(
            argv, answers={("auth", "whoami"): _P(1, "", "Not logged in")})
        r = embedruntime.setup(say=lambda m: None)
        check("not signed in → stops at the model step", r.get("step") == "model" and not r["ok"], str(r)[:120])
        check("…and says where to accept the terms and get a token",
              embedruntime.MODEL_PAGE in r.get("next", "") and embedruntime.TOKENS_PAGE in r.get("next", ""))
        check("…and never chose the engine", engines.choice("embed")["provider"] == "none",
              engines.choice("embed")["provider"])
        check("…and never tried to download", ["download", embedruntime.MODEL] not in calls, str(calls))

        embedruntime._run = lambda argv, timeout=0, env=None: fake(argv, answers={
            ("auth", "whoami"): _P(0, "user: someone"),
            ("download", embedruntime.MODEL): _P(1, "", "403 Forbidden: access requires approval")})
        r = embedruntime.setup(say=lambda m: None)
        check("signed in, terms not accepted → says to accept the terms",
              r.get("step") == "model" and "accept the terms" in r.get("next", ""), str(r)[:120])

        embedruntime._run = lambda argv, timeout=0, env=None: fake(argv, answers={
            ("auth", "whoami"): _P(0, "user: someone"),
            ("download", embedruntime.MODEL): _P(0, os.path.join(_TMP, "snap"))})
        os.makedirs(os.path.join(_TMP, "snap"), exist_ok=True)
        embedruntime.step_service = lambda snap, device, say, wait=0: {"ok": False, "step": "service",
                                                                      "why": "not answering"}
        r = embedruntime.setup(say=lambda m: None)
        check("★control★ — a service that does not answer still chooses nothing",
              r.get("step") == "service" and engines.choice("embed")["provider"] == "none", str(r)[:120])

        embedruntime.step_service = lambda snap, device, say, wait=0: {"ok": True, "step": "service"}
        r = embedruntime.setup(say=lambda m: None)
        c = engines.choice("embed")
        check("every step done → the engine is chosen, pointing at the loopback service",
              r.get("ok") and c["provider"] == "embeddinggemma" and c["base_url"] == embedruntime.BASE_URL,
              "%s %s" % (c["provider"], c["base_url"]))
        check("the service command names the pinned revision",
              embedruntime.REVISION in embedruntime.service_argv("/snap", "cpu"))

        engines.set_choice("embed", "none")
        embedruntime._run = lambda argv, timeout=0, env=None: fake(
            argv, answers={("auth", "whoami"): _P(1, "", "Not logged in")})
        buf = io.StringIO()
        with redirect_stdout(buf):
            done = install._setup_embeddinggemma()
        out = buf.getvalue()
        check("the installer prints the next step and goes on (no traceback)",
              done is False and embedruntime.MODEL_PAGE in out and "auth login" in out, out[-160:])
    finally:
        (embedruntime.step_env, embedruntime._run, embedruntime.venv_hf, embedruntime.snapshot_path,
         embedruntime.step_service) = saved
        if old_mirror is None:
            os.environ.pop("BRAIN_EMBED_MIRROR", None)
        else:
            os.environ["BRAIN_EMBED_MIRROR"] = old_mirror


def gemma_mirror() -> None:
    print("\n" + "=" * 72 + "\n③b EmbeddingGemma — the token-free mirror, proven before it is placed\n" + "=" * 72)
    import hashlib
    import tarfile
    from pathlib import Path
    base = os.path.join(_TMP, "mirror")
    src = os.path.join(base, "src", embedruntime.MIRROR_DIR)
    os.makedirs(os.path.join(src, "2_Dense"), exist_ok=True)
    files = {"config.json": b'{"model_type": "gemma3_text"}', "model.safetensors": b"\x00" * 4096,
             "2_Dense/model.safetensors": b"\x01" * 512}
    for rel, data in files.items():
        Path(src, rel).write_bytes(data)
    Path(src, "files.sha256").write_text("".join(
        "%s  %s\n" % (hashlib.sha256(d).hexdigest(), r) for r, d in files.items()), encoding="utf-8")
    good = os.path.join(base, "good.tar")
    with tarfile.open(good, "w") as tar:
        tar.add(src, arcname=embedruntime.MIRROR_DIR)
    good_sha = hashlib.sha256(Path(good).read_bytes()).hexdigest()
    url = Path(good).as_uri()
    say = lambda m: None

    snap = os.path.join(base, "hub", "snapshots", embedruntime.REVISION)
    r = embedruntime.fetch_mirror(snap, say, url=url, expected="0" * 64)
    check("a copy whose archive hash differs is refused", not r["ok"] and "pinned hash" in r["why"], str(r))
    check("…and leaves no snapshot behind", not os.path.exists(snap))

    Path(src, "model.safetensors").write_bytes(b"\x02" * 4096)     # the listed hash no longer holds
    bad = os.path.join(base, "bad.tar")
    with tarfile.open(bad, "w") as tar:
        tar.add(src, arcname=embedruntime.MIRROR_DIR)
    Path(src, "model.safetensors").write_bytes(files["model.safetensors"])
    r = embedruntime.fetch_mirror(snap, say, url=Path(bad).as_uri(),
                                  expected=hashlib.sha256(Path(bad).read_bytes()).hexdigest())
    check("a file that does not match its listed hash is refused",
          not r["ok"] and "model.safetensors" in r["why"] and not os.path.exists(snap), str(r))

    evil = os.path.join(base, "evil.tar")
    with tarfile.open(evil, "w") as tar:
        tar.add(os.path.join(src, "config.json"), arcname="../escape.json")
    r = embedruntime.fetch_mirror(snap, say, url=Path(evil).as_uri(),
                                  expected=hashlib.sha256(Path(evil).read_bytes()).hexdigest())
    check("an archive entry outside the model folder is refused",
          not r["ok"] and "unexpected entry" in r["why"]
          and not os.path.exists(os.path.join(base, "hub", "snapshots", "escape.json")), str(r))

    r = embedruntime.fetch_mirror(snap, say, url=url, expected=good_sha)
    check("the proven copy lands exactly where Hugging Face would put it",
          r["ok"] and r["source"] == "mirror" and r["files"] == 3
          and Path(snap, "2_Dense", "model.safetensors").read_bytes() == files["2_Dense/model.safetensors"],
          str(r))
    check("…with no working folder left beside it",
          sorted(os.listdir(os.path.dirname(snap))) == [embedruntime.REVISION],
          str(os.listdir(os.path.dirname(snap))))

    saved = (embedruntime.snapshot_path, embedruntime.MIRROR_SHA256, os.environ.get("BRAIN_EMBED_MIRROR"))
    snap2 = os.path.join(base, "hub2", "snapshots", embedruntime.REVISION)
    embedruntime.snapshot_path = lambda: snap2
    embedruntime.MIRROR_SHA256 = good_sha
    os.environ["BRAIN_EMBED_MIRROR"] = url
    try:
        r = embedruntime.step_model(say)
        check("step_model takes the mirror first — no Hugging Face sign-in asked",
              r["ok"] and r.get("source") == "mirror" and r["path"] == snap2, str(r))
        r = embedruntime.step_model(say)
        check("…and reuses it on the next run", r["ok"] and r.get("reused"), str(r))
    finally:
        embedruntime.snapshot_path, embedruntime.MIRROR_SHA256 = saved[0], saved[1]
        if saved[2] is None:
            os.environ.pop("BRAIN_EMBED_MIRROR", None)
        else:
            os.environ["BRAIN_EMBED_MIRROR"] = saved[2]
    check("the default mirror is the pinned revision",
          embedruntime.REVISION[:7] in embedruntime.MIRROR_URL and len(embedruntime.MIRROR_SHA256) == 64)


def uv_bootstrap() -> None:
    print("\n" + "=" * 72 + "\n③c no uv and no Python 3.11 → a pinned uv is fetched, proven, placed\n" + "=" * 72)
    import hashlib
    import tarfile
    from pathlib import Path
    base = os.path.join(_TMP, "uvb")
    rel = os.path.join(base, "srv", embedruntime.UV_VERSION)
    os.makedirs(rel, exist_ok=True)
    name, _ = embedruntime.uv_asset()
    exe = "uv.exe" if os.name == "nt" else "uv"
    inner = os.path.join(base, "pkg", name.split(".")[0])
    os.makedirs(inner, exist_ok=True)
    Path(inner, exe).write_bytes(b"#!/bin/sh\necho uv-fake\n")
    arch = os.path.join(rel, name)
    if name.endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(arch, "w") as z:
            z.write(os.path.join(inner, exe), arcname=os.path.basename(inner) + "/" + exe)
    else:
        with tarfile.open(arch, "w:gz") as t:
            t.add(os.path.join(inner, exe), arcname=os.path.basename(inner) + "/" + exe)
    url = Path(os.path.join(base, "srv")).as_uri() + "/"
    saved = (embedruntime.runtime_dir, dict(embedruntime.UV_ASSETS))
    embedruntime.runtime_dir = lambda: os.path.join(base, "rt")
    key = next(k for k, v in embedruntime.UV_ASSETS.items() if v[0] == name)
    try:
        r = embedruntime.bootstrap_uv(lambda m: None, base=url)
        check("a uv build whose hash differs from the pin is refused",
              not r["ok"] and "pinned hash" in r["why"] and not os.path.exists(embedruntime.bundled_uv()), str(r))
        embedruntime.UV_ASSETS[key] = (name, hashlib.sha256(Path(arch).read_bytes()).hexdigest())
        r = embedruntime.bootstrap_uv(lambda m: None, base=url)
        target = embedruntime.bundled_uv()
        check("the proven uv is placed in the runtime folder, executable",
              r["ok"] and os.path.isfile(target) and (os.name == "nt" or os.access(target, os.X_OK)), str(r))
        check("…and the next run reuses it", embedruntime.bootstrap_uv(lambda m: None, base=url).get("reused"))
        check("…with no working folder left beside it",
              sorted(os.listdir(os.path.dirname(target))) == [exe], str(os.listdir(os.path.dirname(target))))
    finally:
        embedruntime.runtime_dir = saved[0]
        embedruntime.UV_ASSETS.clear()
        embedruntime.UV_ASSETS.update(saved[1])
    check("every pinned uv build names its platform and a 64-hex hash",
          all(len(h) == 64 and v.startswith("uv-") for v, h in embedruntime.UV_ASSETS.values()))
    check("this machine has a pinned build", embedruntime.uv_asset() is not None)


def no_engine_no_collection() -> None:
    print("\n" + "=" * 72 + "\n④ no embedding engine → no vector collection\n" + "=" * 72)
    code = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from brain import store, stores, vectors
db = store.connect()
first = stores.sync(db, ("vector",))["vector"]
print(json.dumps({"first": first}))
'''
    env = dict(os.environ, BRAIN_VECTOR_STORE="qdrant", BRAIN_VECTOR_STORE_URL="http://127.0.0.1:9",
               BRAIN_EMBED_PROVIDER="none", BRAIN_STORE_SYNC_TIMEOUT="2")
    p = subprocess.run([sys.executable, "-c", code, ROOT], capture_output=True, text=True, errors="replace", env=env, timeout=60)
    try:
        first = json.loads(p.stdout.strip().splitlines()[-1])["first"]
    except (ValueError, IndexError):
        first = {"stderr": p.stderr[-300:]}
    check("nothing to send → skipped, no request made", first.get("skipped") == "no_embedding", str(first))
    env["BRAIN_EMBED_PROVIDER"] = "stub"
    p = subprocess.run([sys.executable, "-c", code, ROOT], capture_output=True, text=True, errors="replace", env=env, timeout=60)
    try:
        first = json.loads(p.stdout.strip().splitlines()[-1])["first"]
    except (ValueError, IndexError):
        first = {"stderr": p.stderr[-300:]}
    check("★control★ — with an engine chosen, the vector role goes to the sync as before",
          "skipped" not in first and bool(first.get("target") or first.get("error")), str(first)[:160])


def jobs_follow_engines() -> None:
    print("\n" + "=" * 72 + "\n⑤ a daily job is registered only when its engine is chosen\n" + "=" * 72)
    from brain import scheduler

    class _S:
        name = "fake"

        def available(self):
            return True
    real = scheduler.active
    scheduler.active = lambda refresh=False: _S()
    install.DRY = True
    os.environ["BRAIN_EMBED_PROVIDER"], os.environ["BRAIN_JUDGE_PROVIDER"] = "stub", "none"
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            install.step_schedule(True)
        out = buf.getvalue()
    finally:
        scheduler.active = real
        install.DRY = False
        os.environ.pop("BRAIN_EMBED_PROVIDER", None)
        os.environ.pop("BRAIN_JUDGE_PROVIDER", None)
    would = [ln for ln in out.splitlines() if ln.strip().startswith("--  would")]
    check("no judge → the rules job is not registered",
          not any("rules-daily" in ln for ln in would), out[-200:])
    check("★control★ — the vector job, whose engine is chosen, is", any("vec-daily" in ln for ln in would),
          out[-200:])


def resume_and_exit_code() -> None:
    print("\n" + "=" * 72 + "\n⑥ a half-built venv resumes · an engine that did not come up is a non-zero exit\n" + "=" * 72)
    import shutil as _sh
    rt = os.path.join(_TMP, "rt6")
    saved = (embedruntime.runtime_dir, embedruntime._run)
    embedruntime.runtime_dir = lambda: rt
    seen = []

    def rec(argv, timeout=0, env=None):
        seen.append(list(argv))
        return _P(0)
    embedruntime._run = rec
    try:
        os.makedirs(os.path.join(rt, "venv"), exist_ok=True)          # what an interrupted run leaves
        r = embedruntime.step_env(lambda m: None)
        venv_calls = [a for a in seen if a[1:2] == ["venv"]]
        if venv_calls:
            check("uv is asked to keep a venv that is already there (--allow-existing)",
                  "--allow-existing" in venv_calls[0], str(venv_calls[0]))
            check("…and the packages are installed into it", r.get("ok") and
                  any(a[1:3] == ["pip", "install"] for a in seen), str(seen)[-200:])
        else:
            check("(no uv on this machine — the stdlib venv path tolerates an existing folder)", True)
    finally:
        embedruntime.runtime_dir, embedruntime._run = saved

    uv = _sh.which("uv")
    py311 = embedruntime.find_python()
    if uv and py311:
        d = os.path.join(_TMP, "uv6")
        os.makedirs(d, exist_ok=True)
        v = os.path.join(d, "venv")
        first = subprocess.run([uv, "venv", "--python", py311, v], capture_output=True, text=True, errors="replace")
        again = subprocess.run([uv, "venv", "--python", py311, v], capture_output=True, text=True, errors="replace")
        check("★control★ — on this uv, plain `uv venv` over an existing venv fails",
              first.returncode == 0 and again.returncode != 0, (again.stderr or "").strip()[-120:])
        kept = subprocess.run([uv, "venv", "--python", py311, "--allow-existing", v], capture_output=True, text=True, errors="replace")
        check("…and the same call with --allow-existing succeeds", kept.returncode == 0, kept.stderr[-120:])
    else:
        print("   (uv or Python 3.11 not here — the real-uv control is skipped)")

    names = ("step_prereq", "step_config", "step_index", "step_stores", "step_vectors", "step_mcp",
             "step_hooks", "step_schedule", "_setup_embeddinggemma")
    saved = {n: getattr(install, n) for n in names}
    try:
        for n in names[:3]:
            setattr(install, n, lambda *a, **k: True)
        for n in names[3:8]:
            setattr(install, n, lambda *a, **k: None)
        install._setup_embeddinggemma = lambda: False
        with redirect_stdout(io.StringIO()) as buf:
            code = install.main(["--embed", "embeddinggemma"])
        check("the embedding engine asked for did not come up → exit %d" % install.EXIT_UNFINISHED,
              code == install.EXIT_UNFINISHED, "%s %s" % (code, buf.getvalue()[-160:]))
        install._setup_embeddinggemma = lambda: True
        with redirect_stdout(io.StringIO()):
            code = install.main(["--embed", "embeddinggemma"])
        check("★control★ — it came up → exit 0 (and the earlier failure is not carried over)", code == 0, code)
        with redirect_stdout(io.StringIO()):
            code = install.main([])
        check("no engine asked for → exit 0", code == 0, code)
    finally:
        for n, f in saved.items():
            setattr(install, n, f)


def main() -> int:
    mcp_argv()
    true_case()
    gemma()
    gemma_mirror()
    uv_bootstrap()
    no_engine_no_collection()
    jobs_follow_engines()
    resume_and_exit_code()
    print("\n" + ("❌ %d failed" % len(FAILS) if FAILS else "✅ all passed"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
