"""Set up the local EmbeddingGemma service — ★one command, on any machine★ (2026-11-04).

## Why this exists

The local embedding engine (`engines.embeddinggemma`, §brain/localembed.py) needs a model service that
the core cannot import: a separate Python 3.11+ environment with `torch` and `sentence-transformers`,
a gated model that a person must accept on Hugging Face, a download pinned to one commit, and a process
that keeps running. Until now that was six manual steps in `runtime/embeddinggemma/README.md`, and the
installer did not mention the engine at all — measured on a fresh install (2026-11-04): Qdrant and Neo4j
came up, the vector store held 0 vectors, and nothing said why.

`./install.sh --embed embeddinggemma` (or `--recommended`) now calls `setup()`, which does every step
it can and ★stops with the exact next command★ at the one step only a person can take (accepting the
model's terms and signing in to Hugging Face). Run it again afterwards; every step is idempotent.

## ⛔ What it never does

- It never installs model libraries into the core's Python. The runtime lives in its own environment
  under the brain's home (`<home>/runtime/embeddinggemma/venv`), outside the checkout.
- It never accepts a licence or handles a token for the person. The token-free mirror is the same
  revision redistributed under the Gemma Terms of Use, which travel with it (2026-11-06).
- It never falls back to a paid or remote engine when a step fails. It says which step failed.

Standard library only, like the rest of `brain/`; the model libraries are only ever imported by
`runtime/embeddinggemma/service.py`, inside that environment.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "google/embeddinggemma-300m"
# ⛔ Pinned: the device, library versions and model files are part of the vector space's fingerprint,
#    so a silently newer model would be a different space. Change it on purpose, with `vec build`.
REVISION = "57c266a740f537b4dc058e1b0cda161fd15afa75"
PORT = int(os.environ.get("BRAIN_EMBED_PORT", "18081") or 18081)
BASE_URL = "http://127.0.0.1:%d/v1" % PORT
LABEL = "com.local-brain.embeddinggemma"
MODEL_PAGE = "https://huggingface.co/" + MODEL
# ★A copy that needs no token★ (2026-11-06). The gated model stopped every install at a step only a
#  person could take. Honeyground redistributes the same revision under the Gemma Terms of Use (which
#  allow it, with the terms and the notice attached). ⛔ Byte-identical, not "a model of the same size":
#  the files are part of the vector space's fingerprint, so a different copy would be a different space.
MIRROR_URL = ("https://github.com/honeyground-org/local-brain/releases/download/"
              "model-embeddinggemma-300m-57c266a/embeddinggemma-300m-57c266a.tar")
MIRROR_SHA256 = "d885d927dcbb3c9ee460c75cf4b495771954753651313fdd3533a157cdde14e8"
MIRROR_DIR = "embeddinggemma-300m-57c266a"          # the one top-level folder inside the archive
TERMS_PAGE = "https://ai.google.dev/gemma/terms"
TOKENS_PAGE = "https://huggingface.co/settings/tokens"
MIN_PY = (3, 11)


def home() -> str:
    from brain import store
    return store.brain_home()


def runtime_dir() -> str:
    return os.path.join(home(), "runtime", "embeddinggemma")


def venv_python() -> str:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(runtime_dir(), "venv", *sub)


def venv_hf() -> str:
    sub = ("Scripts", "hf.exe") if os.name == "nt" else ("bin", "hf")
    return os.path.join(runtime_dir(), "venv", *sub)


def manifest_path() -> str:
    return os.environ.get("BRAIN_EMBED_MANIFEST") or os.path.join(home(), "embeddinggemma.json")


def _run(argv: List[str], timeout: float = 600, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout,
                          env=dict(os.environ, **(env or {})))


def _py_version(exe: str) -> Optional[tuple]:
    try:
        p = _run([exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], timeout=20)
        if p.returncode == 0:
            return tuple(int(x) for x in p.stdout.strip().split("."))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def find_python() -> Optional[str]:
    """A Python ≥ 3.11 already on this machine — the newest first."""
    for name in ("python3.13", "python3.12", "python3.11", "python3", "python"):
        exe = shutil.which(name)
        if exe and (_py_version(exe) or (0, 0)) >= MIN_PY:
            return exe
    return None


def python_hint() -> str:
    if sys.platform == "darwin":
        return "brew install uv   (or: brew install python@3.12)"
    if os.name == "nt":
        return "winget install astral-sh.uv   (or: winget install Python.Python.3.12)"
    return "curl -LsSf https://astral.sh/uv/install.sh | sh   (or your package manager's python3.12)"


# ── the steps ───────────────────────────────────────────────────────────────
# Each returns {"ok": bool, "step": name, ...}. `setup` stops at the first that is not ok and returns
# it, so the installer can print exactly one next action.

def step_env(say: Callable[[str], None]) -> dict:
    """The runtime's own environment, with its declared requirements."""
    req = os.path.join(ROOT, "runtime", "embeddinggemma", "requirements.txt")
    if os.path.isfile(venv_python()) and (_py_version(venv_python()) or (0, 0)) >= MIN_PY:
        p = _run([venv_python(), "-c", "import sentence_transformers, torch"], timeout=120)
        if p.returncode == 0:
            return {"ok": True, "step": "env", "python": venv_python(), "reused": True}
    os.makedirs(runtime_dir(), exist_ok=True)
    venv = os.path.join(runtime_dir(), "venv")
    uv = shutil.which("uv")
    say("creating the runtime environment (Python 3.11+, torch, sentence-transformers — a few minutes, once)")
    if uv:
        p = _run([uv, "venv", "--python", "3.11", venv], timeout=900)
        if p.returncode == 0:
            p = _run([uv, "pip", "install", "--python", venv_python(), "-r", req], timeout=3600)
    else:
        py = find_python()
        if not py:
            return {"ok": False, "step": "env", "why": "no Python 3.11+ and no uv", "next": python_hint()}
        p = _run([py, "-m", "venv", venv], timeout=300)
        if p.returncode == 0:
            p = _run([venv_python(), "-m", "pip", "install", "--disable-pip-version-check", "-r", req],
                     timeout=3600)
    if p.returncode != 0:
        return {"ok": False, "step": "env", "why": (p.stderr or p.stdout).strip()[-300:]}
    return {"ok": True, "step": "env", "python": venv_python()}


def snapshot_path() -> str:
    from_env = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
    return os.path.join(from_env, "models--" + MODEL.replace("/", "--"), "snapshots", REVISION)


def mirror_url() -> str:
    """Where the token-free copy comes from. `BRAIN_EMBED_MIRROR=off` keeps Hugging Face only."""
    return (os.environ.get("BRAIN_EMBED_MIRROR") or MIRROR_URL).strip()


def _sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_mirror(snap: str, say: Callable[[str], None], url: str = "",
                 expected: str = "") -> dict:
    """Download the archived snapshot, prove it, and put it exactly where Hugging Face would.

    ⛔ Nothing is placed until both proofs pass: the archive's SHA-256 (pinned here) and every model
    file's SHA-256 (listed inside it). A half-written or tampered copy leaves no snapshot behind, so
    the next run starts clean and the Hugging Face path stays available.
    """
    import tarfile
    import tempfile
    import urllib.request
    url = url or mirror_url()
    expected = expected or MIRROR_SHA256
    parent = os.path.dirname(snap)
    os.makedirs(parent, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".mirror-", dir=parent)
    try:
        archive = os.path.join(work, "snapshot.tar")
        say("downloading %s @ %s from the Honeyground mirror (about 1.27 GB, once — no token needed)"
            % (MODEL, REVISION[:12]))
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(archive, "wb") as out:
                shutil.copyfileobj(resp, out, 1 << 20)
        except Exception as e:
            return {"ok": False, "step": "model", "why": "mirror download failed: %s" % e}
        got = _sha256_file(archive)
        if got != expected:
            return {"ok": False, "step": "model",
                    "why": "mirror archive does not match the pinned hash (%s…)" % got[:12]}
        dest = os.path.join(work, "x")
        with tarfile.open(archive) as tar:
            for m in tar.getmembers():          # ⛔ only plain files and folders under the one folder
                name = m.name.replace("\\", "/")
                parts = name.split("/")
                if (not (m.isfile() or m.isdir()) or name.startswith("/") or ".." in parts
                        or parts[0] != MIRROR_DIR):
                    return {"ok": False, "step": "model", "why": "unexpected entry in the archive: %s" % name}
            tar.extractall(dest)
        root = os.path.join(dest, MIRROR_DIR)
        listed = os.path.join(root, "files.sha256")
        if not os.path.isfile(listed):
            return {"ok": False, "step": "model", "why": "the archive has no files.sha256"}
        count = 0
        with open(listed, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                digest, rel = line.strip().split(None, 1)
                path = os.path.join(root, rel.lstrip("*"))
                if not os.path.isfile(path) or _sha256_file(path) != digest:
                    return {"ok": False, "step": "model", "why": "file does not match its hash: %s" % rel}
                count += 1
        if not os.path.isfile(os.path.join(root, "config.json")) or not count:
            return {"ok": False, "step": "model", "why": "the archive holds no model"}
        if os.path.isdir(snap):
            shutil.rmtree(snap)                 # an incomplete folder (no config.json) from an old try
        os.replace(root, snap)
        say("verified %d files; the model is covered by the Gemma Terms of Use (%s), "
            "a copy is in %s" % (count, TERMS_PAGE, os.path.join(snap, "GEMMA_TERMS_OF_USE.txt")))
        return {"ok": True, "step": "model", "path": snap, "source": "mirror", "files": count}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def step_model(say: Callable[[str], None]) -> dict:
    """The pinned model, downloaded once — from the token-free mirror first, Hugging Face second.

    ⛔ The Hugging Face copy is gated: there the person accepts its terms and signs in."""
    snap = snapshot_path()
    if os.path.isfile(os.path.join(snap, "config.json")):
        return {"ok": True, "step": "model", "path": snap, "reused": True}
    if mirror_url().lower() not in ("off", "0", "none", ""):
        got = fetch_mirror(snap, say)
        if got.get("ok"):
            return got
        say("the mirror did not work (%s); trying Hugging Face" % got.get("why"))
    hf = venv_hf() if os.path.isfile(venv_hf()) else (shutil.which("hf") or "")
    if not hf:
        return {"ok": False, "step": "model", "why": "the `hf` command is missing from the runtime environment"}
    who = _run([hf, "auth", "whoami"], timeout=60)
    if who.returncode != 0 or "Not logged in" in (who.stdout + who.stderr):
        return {"ok": False, "step": "model", "why": "not signed in to Hugging Face",
                "next": "1) accept the model's terms: %s\n2) create a read token: %s\n3) %s auth login"
                        % (MODEL_PAGE, TOKENS_PAGE, hf)}
    say("downloading %s @ %s (about 1.2 GB, once)" % (MODEL, REVISION[:12]))
    p = _run([hf, "download", MODEL, "--revision", REVISION], timeout=7200)
    text = p.stdout + p.stderr
    if p.returncode != 0:
        if "requires approval" in text or "gated" in text.lower() or "403" in text:
            return {"ok": False, "step": "model", "why": "the model's terms are not accepted for this account",
                    "next": "accept the terms at %s, then run the installer again" % MODEL_PAGE}
        return {"ok": False, "step": "model", "why": text.strip()[-300:]}
    for line in text.splitlines():                       # the CLI prints where it put the snapshot
        if line.startswith("path="):
            snap = line[5:].strip()
        elif os.path.isdir(line.strip()):
            snap = line.strip()
    return {"ok": True, "step": "model", "path": snap}


def service_argv(snap: str, device: str) -> List[str]:
    return [venv_python(), os.path.join(ROOT, "runtime", "embeddinggemma", "service.py"),
            "--model-path", snap, "--revision", REVISION,
            "--manifest", manifest_path(),
            "--cache", os.path.join(home(), "embeddinggemma-cache.sqlite"),
            "--device", device, "--port", str(PORT), "--batch", "4"]


def ready(timeout: float = 2.0) -> bool:
    """Is the service up: its manifest published (it is written only after the port is bound) and the
    loopback port accepting connections. ⛔ The service has no GET route, so the port is the probe."""
    if not os.path.isfile(manifest_path()):
        return False
    import socket
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except OSError:
        return False


def step_service(snap: str, device: str, say: Callable[[str], None], wait: float = 240) -> dict:
    """Kept running by this platform's service manager — started now, at sign-in, and after a crash."""
    from brain import scheduler
    if ready():
        return {"ok": True, "step": "service", "reused": True}
    log = os.path.join(home(), "embeddinggemma.log")
    r = scheduler.active().install_service(LABEL, service_argv(snap, device), log)
    if not r.get("ok"):
        return {"ok": False, "step": "service", "why": r.get("why") or "could not register the service",
                "next": " ".join(service_argv(snap, device))}
    say("waiting for the model service to load")
    t0 = time.time()
    while time.time() - t0 < wait:
        if ready():
            return {"ok": True, "step": "service", "registered": r.get("path") or LABEL}
        time.sleep(2)
    return {"ok": False, "step": "service", "why": "registered, but not answering after %ds — see %s"
                                                   % (int(wait), log)}


def default_device() -> str:
    """`cpu` everywhere: the portable reference the runtime is measured on. `mps`/`cuda` are a choice."""
    return os.environ.get("BRAIN_EMBED_DEVICE", "cpu")


def setup(say: Callable[[str], None] = print, device: str = "") -> dict:
    """Every step in order; stops at the first one that needs a person, and says what to do."""
    device = device or default_device()
    env = step_env(say)
    if not env["ok"]:
        return env
    model = step_model(say)
    if not model["ok"]:
        return model
    svc = step_service(model["path"], device, say)
    if not svc["ok"]:
        return svc
    from brain import engines
    engines.set_choice("embed", "embeddinggemma", model=MODEL, base_url=BASE_URL)
    return {"ok": True, "step": "done", "base_url": BASE_URL, "device": device,
            "manifest": manifest_path(), "service": svc}


def status() -> dict:
    """What `brain status` and the installer show about this runtime."""
    return {"environment": os.path.isfile(venv_python()), "model": os.path.isfile(
        os.path.join(snapshot_path(), "config.json")), "service": ready(), "port": PORT,
        "manifest": manifest_path()}


if __name__ == "__main__":
    out = setup()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    sys.exit(0 if out.get("ok") else 1)
