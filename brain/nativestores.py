"""The dedicated databases ★without Docker★ — downloaded, checked, and kept running by this machine's own
service manager (§stores · the Docker way is §dockerstores).

## Why (user instruction, 2026-11-07)

*"The databases must be installed by actually downloading them, not through Docker — EmbeddingGemma is
already done that way."* Measured on the reference Mac the same day: Qdrant and Neo4j ran in Docker, and
Docker Desktop did not start at sign-in, so after every restart both stayed down until someone opened
Docker by hand (the local copy answered meanwhile, but meaning-based search ran on the slow path and the
graph database did nothing). Docker's own virtual machine also held 8 GB of memory for two databases
that need well under one.

*"After the system is shut down and started again, the brain must work by itself."* (same day) — so each
database is a service of the machine's own manager, exactly like the EmbeddingGemma service
(§embedruntime.step_service): launchd on macOS, a systemd user unit on Linux, a sign-in task on Windows.
Started now, at every sign-in, and again after a crash.

## What it does, per backend (what to fetch comes from the backend's own declaration, `BACKEND.native`)

  1. the official release archive for this platform — ★pinned★ to one version and checked against the
     SHA-256 written next to it in the backend file; a mismatch stops before anything is unpacked
  2. a runtime it needs, pinned and checked the same way (Neo4j: a Java 21 runtime, Eclipse Temurin)
  3. unpacked under `<brain home>/runtime/<name>/<version>/` (refusing any path that leaves that folder)
  4. its config written there, bound to 127.0.0.1, usage reporting off, data in
     `<brain home>/stores/native/<name>/` — a folder of its own, apart from the Docker one
  5. registered as a service and waited for, then brain is pointed at it (`managed: native`)

The first sync after that fills it from the local SQLite copy (the caller's next step, as for Docker):
nothing is migrated, because the local copy is canonical (§stores).

## ⛔ What it never does

- It never installs anything system-wide or needs an administrator: everything is under the brain's home.
- It never puts a credential on a command line (`ps` shows command lines): Neo4j's first password is
  read from a 0600 file that is deleted right after (§backends.neo4j.native_layout).
- It never guesses a build: a platform the backend lists no archive for gets a clear answer, and the
  Docker way or an address you run yourself stays available.

Standard library only, like the rest of `brain/`.
"""
from __future__ import annotations

import hashlib
import os
import platform as _platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from typing import Callable, Dict, List, Optional, Sequence, Union

READY_TIMEOUT = float(os.environ.get("BRAIN_NATIVE_READY_TIMEOUT", "180") or 180)
DOWNLOAD_TIMEOUT = 60.0
LABEL_PREFIX = "com.local-brain."

# A runtime a backend can ask for — pinned, with the checksums the upstream release publishes (2026-11-07).
_TEMURIN = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.9%2B10/"
RUNTIMES: Dict[str, dict] = {
    "java21": {
        "version": "21.0.9+10",
        "files": {
            "darwin-arm64": (_TEMURIN + "OpenJDK21U-jre_aarch64_mac_hotspot_21.0.9_10.tar.gz",
                             "1f7f6506b598e85d7d8ff8b36563d98657d2d81b16bfca3cd242d7906cfbd11b"),
            "darwin-x64": (_TEMURIN + "OpenJDK21U-jre_x64_mac_hotspot_21.0.9_10.tar.gz",
                           "945abc49249f1e89a2a6a008d70d63dc42b25bbe5e1711ff97aeec70063008d2"),
            "linux-x64": (_TEMURIN + "OpenJDK21U-jre_x64_linux_hotspot_21.0.9_10.tar.gz",
                          "aeab55d064a1a27a3744b0880b9b414077b4ed2b1790817eea3df60aec946431"),
            "linux-arm64": (_TEMURIN + "OpenJDK21U-jre_aarch64_linux_hotspot_21.0.9_10.tar.gz",
                            "1d041073c65e834bdb4da732485a54ff829859dcd1549e7992f15bd73341be29"),
            "windows-x64": (_TEMURIN + "OpenJDK21U-jre_x64_windows_hotspot_21.0.9_10.zip",
                            "39c5e23f3ce4d420663afba8ffde28034b72e2b3e240943dc2321bc1f912eef9"),
        },
    },
}


# ── where things are ────────────────────────────────────────────────────────
def platform() -> str:
    """`darwin-arm64` · `darwin-x64` · `linux-x64` · `linux-arm64` · `windows-x64` (or what this machine is)."""
    osname = "darwin" if sys.platform == "darwin" else ("windows" if os.name == "nt" else sys.platform)
    m = (_platform.machine() or "").lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(m, m)
    return "%s-%s" % (osname, arch)


def _home() -> str:
    from brain import store
    return store.brain_home()


def runtime_root() -> str:
    return os.path.join(_home(), "runtime")


def base_dir() -> str:
    return os.path.join(_home(), "stores", "native")


def install_dir(name: str, version: str) -> str:
    return os.path.join(runtime_root(), name, version)


def data_dirs(b) -> Dict[str, str]:
    """{subfolder: path} — `""` is the backend's folder itself."""
    root = os.path.join(base_dir(), b.name)
    return {sub: os.path.join(root, sub) if sub else root for sub in b.native.data}


def log_path(b) -> str:
    return os.path.join(base_dir(), b.name + ".log")


def label(b) -> str:
    return LABEL_PREFIX + b.name


def ports(b) -> Dict[str, int]:
    return {p.name: int(os.environ.get(p.env, "") or p.host) for p in b.native.ports}


def url(b) -> str:
    return "%s://127.0.0.1:%d" % (b.native.scheme, ports(b)[b.native.url_port])


def installable(role: str = "") -> Dict[str, object]:
    """Every backend that has a native build for this machine: {name: Backend}."""
    from brain import stores
    here = platform()
    return {n: b for n, b in stores.backends(role).items() if b.native is not None and here in b.native.files}


def declared(role: str = "") -> Dict[str, object]:
    """Every backend that declares a native install at all (whatever the platform)."""
    from brain import stores
    return {n: b for n, b in stores.backends(role).items() if b.native is not None}


def resolve(tokens: Sequence[str]) -> Dict[str, str]:
    """`vector` · `graph=neo4j` · `qdrant` → {role: backend}. A role alone means its default.

    Raises ValueError naming the token that is not accepted.
    """
    from brain import stores
    out: Dict[str, str] = {}
    for tok in [t.strip().lower() for t in tokens if t.strip()]:
        role, _, name = tok.partition("=")
        if name:
            if role not in stores.ROLES:
                raise ValueError(tok)
        elif tok in stores.ROLES:
            role, name = tok, stores.NATIVE_DEFAULT[tok]
        else:
            b = stores.spec(tok)
            if b is None or b.role not in stores.ROLES:
                raise ValueError(tok)
            role, name = b.role, tok
        if name not in declared(role):
            raise ValueError(tok)
        out[role] = name
    return out


def managed() -> Dict[str, str]:
    """{role: backend} for every role whose chosen backend brain runs natively itself."""
    from brain import stores
    out = {}
    for role in stores.ROLES:
        c = stores.choice(role)
        if (c.get("options") or {}).get("managed") == "native" and c["backend"] in declared(role):
            out[role] = c["backend"]
    return out


# ── fetching: pinned, checked, unpacked inside its own folder ───────────────
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: str) -> None:
    """`url` → `dest` (a URL, or a local path — a mirror or a test). Written to `.part` first.

    ⛔ One GET of the archive, nothing sent (§verify_privacy pins this shape: a download-only door).
    """
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(url):
        shutil.copyfile(url, part)
    else:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as r, open(part, "wb") as fh:  # noqa: S310
            shutil.copyfileobj(r, fh, 1 << 20)
    os.replace(part, dest)


def _inside(root: str, path: str) -> bool:
    root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, path))
    return full == root or full.startswith(root + os.sep)


def _extract(archive: str, dest: str) -> None:
    """Unpack, refusing an absolute path, a `..`, or a link that points out of `dest`."""
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if os.path.isabs(n) or not _inside(dest, n):
                    raise ValueError("unsafe path in archive: %s" % n)
            z.extractall(dest)
        return
    with tarfile.open(archive, "r:*") as t:
        members = t.getmembers()
        for m in members:
            if os.path.isabs(m.name) or not _inside(dest, m.name):
                raise ValueError("unsafe path in archive: %s" % m.name)
            if m.issym() or m.islnk():
                target = m.linkname if m.islnk() else os.path.join(os.path.dirname(m.name), m.linkname)
                if os.path.isabs(m.linkname) or not _inside(dest, target):
                    raise ValueError("unsafe link in archive: %s → %s" % (m.name, m.linkname))
            if m.isdev():
                raise ValueError("device file in archive: %s" % m.name)
        if hasattr(tarfile, "data_filter"):
            t.extractall(dest, members=members, filter="data")      # noqa: S202 — checked above
        else:                                                        # Python < 3.12 without the backport
            t.extractall(dest, members=members)                     # noqa: S202 — checked above


def _home_of(root: str) -> str:
    """The folder an archive unpacked into: its single top folder when it has one (macOS bundles: the
    `Contents/Home` inside it), otherwise `root` itself."""
    entries = [e for e in os.listdir(root) if not e.startswith(".")]
    home = root
    if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
        home = os.path.join(root, entries[0])
    bundle = os.path.join(home, "Contents", "Home")
    return bundle if os.path.isdir(bundle) else home


def fetch(name: str, version: str, files: Dict[str, tuple], say: Callable[[str], None] = print,
          plat: str = "") -> dict:
    """The pinned archive for this platform, checked and unpacked once → {ok, home, why}. Idempotent."""
    plat = plat or platform()
    if plat not in files:
        return {"ok": False, "why": "no %s build for %s" % (name, plat)}
    src, want = files[plat]
    root = install_dir(name, version)
    done = os.path.join(root, ".local-brain-ok")
    if os.path.isfile(done):
        with open(done, encoding="utf-8") as fh:
            if fh.read().strip() == want:
                return {"ok": True, "home": _home_of(os.path.join(root, "unpacked")), "reused": True}
    archive = os.path.join(runtime_root(), "downloads", os.path.basename(src.split("?")[0]))
    say("downloading %s %s (%s)" % (name, version, os.path.basename(archive)))
    try:
        if not (os.path.isfile(archive) and _sha256(archive) == want):
            _download(src, archive)
        got = _sha256(archive)
        if got != want:
            os.remove(archive)
            return {"ok": False, "why": "%s: checksum mismatch (expected %s…, got %s…) — nothing was unpacked"
                                        % (os.path.basename(archive), want[:12], got[:12])}
        unpacked = os.path.join(root, "unpacked")
        if os.path.isdir(unpacked):
            shutil.rmtree(unpacked)
        os.makedirs(unpacked)
        _extract(archive, unpacked)
        with open(done, "w", encoding="utf-8") as fh:
            fh.write(want)
        os.remove(archive)                                # the unpacked copy is what runs; keep the disk
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return {"ok": False, "why": "%s: %s" % (name, str(exc)[:200])}
    return {"ok": True, "home": _home_of(unpacked)}


# ── one backend: fetched, configured, registered, waited for ────────────────
def _write_private(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _first_start(data: Dict[str, str]) -> bool:
    """No database has been created in these folders yet."""
    return not any(os.listdir(d) for d in data.values() if os.path.isdir(d))


def _ping(b) -> str:
    from brain import dockerstores, stores
    c = {"role": b.role, "backend": b.name, "url": url(b), "options": {"managed": "native"},
         "secret": dockerstores.generated_secret(b, create=False) if b.native.generate_secret else ""}
    try:
        return b.make(c).ping().get("version", "") or "?"
    except (stores.StoreError, OSError):
        return ""


def prepare(b, say: Callable[[str], None] = print, plat: str = "") -> dict:
    """Fetch, configure and initialise one backend — everything but starting it → {ok, layout, why}."""
    from brain import dockerstores
    n = b.native
    rt_home = ""
    if n.runtime:
        rt = RUNTIMES.get(n.runtime)
        if rt is None:
            return {"ok": False, "why": "%s needs a runtime brain does not know: %s" % (b.name, n.runtime)}
        got = fetch(n.runtime, rt["version"], rt["files"], say, plat)
        if not got["ok"]:
            return got
        rt_home = got["home"]
    got = fetch(b.name, n.version, n.files, say, plat)
    if not got["ok"]:
        return got
    data = data_dirs(b)
    first = _first_start(data)
    for d in data.values():
        os.makedirs(d, exist_ok=True)
    secret_file = ""
    if n.generate_secret:
        secret_file = os.path.join(install_dir(b.name, n.version), ".initial-secret")
        # the same credential the Docker container of this backend uses — one database, one password
        _write_private(secret_file, dockerstores.generated_secret(b) + "\n")
    ctx = {"home": got["home"], "data": data, "ports": ports(b), "secret_file": secret_file,
           "runtime_home": rt_home, "platform": plat or platform()}
    lay = n.layout(ctx)
    try:
        for path, text in (lay.get("files") or {}).items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        if first:
            env = dict(os.environ, **(lay.get("env") or {}))
            for argv in lay.get("init") or []:
                p = subprocess.run(argv, capture_output=True, text=True, errors="replace", env=env,
                                   cwd=lay.get("cwd") or None, timeout=300)
                if p.returncode != 0:
                    return {"ok": False, "why": "%s: %s" % (b.name, (p.stderr or p.stdout).strip()[-300:])}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "why": "%s: %s" % (b.name, str(exc)[:200])}
    finally:
        if secret_file and os.path.exists(secret_file):
            os.remove(secret_file)
    return {"ok": True, "layout": lay, "home": got["home"]}


def up(wanted: Union[None, Sequence[str], Dict[str, str]] = None, dry_run: bool = False,
       say: Callable[[str], None] = print) -> dict:
    """Install (or repair) the chosen databases natively, keep them running, and point brain at them.

    `wanted` = {role: backend}, or roles (each gets its default), or None (every role's default).
    Returns {ok, roles: {role: version}, backends, data: {role: [folders]}, why}. Idempotent: a backend
    already answering on its port is left running. The first sync is the caller's next step.
    """
    from brain import i18n, scheduler, stores
    if wanted is None:
        wanted = dict(stores.NATIVE_DEFAULT)
    elif not isinstance(wanted, dict):
        wanted = {r: stores.NATIVE_DEFAULT[r] for r in wanted if r in stores.ROLES}
    wanted = {r: wanted[r] for r in stores.ROLES if r in wanted}
    here = installable()
    for role, name in wanted.items():
        if name not in here:
            return {"ok": False, "roles": {},
                    "why": "%s has no native build for %s — use --stores docker, or give the address of "
                           "a server you run (--%s-store %s=URL)" % (name, platform(), role, name)}
    if dry_run:
        for role, name in wanted.items():
            b = stores.spec(name)
            say("  --  would " + i18n.t("install.would_native_store", role=role, backend=name,
                                        version=b.native.version, data=", ".join(data_dirs(b).values())))
        return {"ok": True, "dry_run": True, "roles": {r: "" for r in wanted}, "backends": dict(wanted)}
    from brain import dockerstores
    sched = scheduler.active()
    versions: Dict[str, str] = {}
    retired: List[str] = []
    in_docker = set(dockerstores.managed().values())
    for role, name in wanted.items():
        b = stores.spec(name)
        prep = prepare(b, say)
        if not prep["ok"]:
            return {"ok": False, "why": prep["why"], "roles": versions, "retired": retired}
        lay = prep["layout"]
        os.makedirs(base_dir(), exist_ok=True)
        # ⛔ The Docker container of the same backend listens on the same port: it is stopped first
        #    (never removed — its data folder stays), or the answer below would come from it.
        if name in in_docker and dockerstores.stop([name]):
            retired.append(name)
            time.sleep(2)
        if not (_registered(sched, b) and _ping(b)):
            if _ping(b):
                return {"ok": False, "roles": versions, "retired": retired,
                        "why": "something else already answers on %s — stop it, or move the port with %s"
                               % (url(b), " / ".join(p.env for p in b.native.ports))}
            r = sched.install_service(label(b), list(lay["argv"]), log_path(b),
                                      env=lay.get("env") or None, cwd=lay.get("cwd") or "")
            if not r.get("ok"):
                return {"ok": False, "roles": versions, "retired": retired,
                        "why": "%s: could not register the service (%s)" % (name, r.get("why") or "?")}
            say("waiting for %s to answer" % name)
        end = time.time() + READY_TIMEOUT
        v = _ping(b)
        while not v and time.time() < end:
            time.sleep(2)
            v = _ping(b)
        if not v:
            return {"ok": False, "roles": versions, "retired": retired,
                    "why": "%s registered, but not answering after %ds — see %s" % (name, READY_TIMEOUT, log_path(b))}
        versions[role] = v
        prev = stores.choice(role)
        keep = ({k: val for k, val in prev["options"].items() if k not in stores.RESERVED}
                if prev["backend"] == name else {})
        stores.set_choice(role, name, url(b), dict(keep, managed="native"))
    return {"ok": True, "roles": versions, "backends": dict(wanted), "retired": retired,
            "data": {r: list(data_dirs(stores.spec(n)).values()) for r, n in wanted.items()}}


def service_file(b) -> str:
    """Where this machine's service manager keeps brain's service for `b` ("" on Windows: a task)."""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/LaunchAgents/%s.plist" % label(b))
    if os.name == "posix":
        return os.path.expanduser("~/.config/systemd/user/%s.service" % label(b))
    return ""


def _registered(sched, b) -> bool:
    """This machine's service manager already holds brain's service for `b`.

    ⛔ Read from the service file itself, not `sched.query`: on Linux the scheduler is often cron, whose
       query knows crontab lines, not systemd units — a running service of ours would look foreign.
    """
    f = service_file(b)
    if f:
        return os.path.isfile(f)
    try:
        return bool(sched.query(b.name, label(b)).get("known"))
    except Exception:                                     # noqa: BLE001 — unknown means "register it"
        return False


def status(b) -> dict:
    """What `brain stores` shows for a native backend: {label, answers, data, log}."""
    return {"label": label(b), "answers": bool(_ping(b)), "data": list(data_dirs(b).values()), "log": log_path(b)}


def which_native(names: Optional[List[str]] = None) -> List[str]:
    """Backends with a native declaration, sorted (for help texts)."""
    return sorted(names if names is not None else declared())
