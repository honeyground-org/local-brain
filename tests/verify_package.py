"""The installable-package check — ★does it actually run once installed on someone else's machine★. (local · budget 0 · builds a venv)

## Why (user requirement 2026-09-02: an installable Mac·Windows package)

Right now the entry points are POSIX shell scripts under `bin/*`. ★Windows has no `sh`, so not a single line runs.★
Make them `console_scripts` in `pyproject.toml` and pip plants a shell shim on Mac and a
`brain.exe` shim on Windows on its own — one set covers both platforms.

## ⛔ Three things this check protects (all three were actually wrong on 2026-09-02)

① ★is the dependency count really 0★ — this repo's pride. If something rides along while
   making `pyproject.toml`, that pride quietly becomes a lie.
② ★does it avoid polluting someone else's namespace★ — the first draft wrote `package-data` as
   `../i18n/*.json`, and pip unpacked that at ★the top of site-packages★. That spot belongs to
   everyone, so it collides with another package's own `i18n`. ★Breaking someone else's environment is the worst possible failure.★
③ ★does it run with no repo at all★ — an installed copy has no repo. If the catalog·config path
   points at `<repo>/…`, it only works on the dev machine and breaks the moment it's handed to someone else.

⚠️ This check builds a venv and runs `pip install .` — it's slow (tens of seconds). So it isn't
   dropped into the regression suite every time — it runs only ★when packaging is touched★.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\ninstallable package — does it run once installed on someone else's machine\n" + "=" * 72)

    # ⛔ ★clears old build artifacts first★ — leave them and old package-data mixes into this
    #    install, making a fix look unfixed (or the reverse).
    for d in ("build", "local_brain.egg-info"):
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)

    tmp = tempfile.mkdtemp()
    try:
        venv = os.path.join(tmp, "venv")
        r = subprocess.run([sys.executable, "-m", "venv", venv],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("⚠️ couldn't build a venv — skipping this check on this machine")
            return 0
        pybin = os.path.join(venv, "Scripts" if os.name == "nt" else "bin")
        pip = os.path.join(pybin, "pip")
        py = os.path.join(pybin, "python")
        r = subprocess.run([pip, "install", "-q", ROOT], capture_output=True, text=True)
        check("`pip install .` succeeds", r.returncode == 0,
              (r.stderr or "")[-200:] if r.returncode else "")
        if r.returncode != 0:
            return 1

        # ── ① is the dependency count really 0 ────────────────────────────────────────
        r = subprocess.run([pip, "list", "--format=freeze"], capture_output=True, text=True)
        pkgs = [l.split("==")[0].lower() for l in r.stdout.splitlines() if "==" in l]
        extra = [p for p in pkgs if p not in
                 ("pip", "setuptools", "wheel", "local-brain", "pkg-resources")]
        check("★dependency count is 0★ (so this repo's pride never becomes a lie)",
              not extra, ", ".join(extra) or "only our own")

        # ── ② did every entry point get created ────────────────────────────────────
        want = ["brain", "brain-hook", "brain-guard", "brain-mcp",
                "brain-stop", "brain-end", "brain-session"]
        have = os.listdir(pybin)
        missing = [w for w in want
                   if w not in have and (w + ".exe") not in have]
        check("all %d entry point(s) get created (with no shell script)" % len(want),
              not missing, ", ".join(missing) or "all")

        # ── ③ ★does it avoid polluting someone else's namespace★ ──────────────────────
        # ⛔ without PYTHONPATH — run as documented (`PYTHONPATH=. …`) it imported the ★repository★, called
        #    the repository root "site-packages" and reported every top-level file as pollution (2026-10-07)
        clean_env = dict(os.environ)
        clean_env.pop("PYTHONPATH", None)
        sp = subprocess.run(
            [py, "-c", "import brain,os;print(os.path.dirname(os.path.dirname(brain.__file__)))"],
            capture_output=True, text=True, cwd=tmp, env=clean_env).stdout.strip()
        allowed = ("brain", "pip", "setuptools", "wheel", "pkg_resources",
                   "_distutils_hack", "distutils-precedence.pth", "__pycache__")
        junk = [n for n in os.listdir(sp)
                if not n.startswith(allowed) and not n.endswith(".dist-info")
                and not n.endswith(".egg-info")]
        check("★never pollutes the top of site-packages★ (someone else's environment comes first)",
              not junk, ", ".join(junk) or "clean")

        # ── ④ does it actually run ★outside the repo★ ────────────────────────────
        env = dict(os.environ, BRAIN_LANG="en")
        env.pop("PYTHONPATH", None)
        r = subprocess.run([os.path.join(pybin, "brain"), "i18n"],
                           capture_output=True, text=True, cwd=tmp, env=env)
        check("`brain` runs outside the repo", r.returncode == 0, (r.stderr or "")[-150:])
        check("★the English catalog reads correctly from inside the installed package★",
              "Translation catalogues" in r.stdout, (r.stdout.splitlines() or [""])[0])

        env["BRAIN_LANG"] = "ko"
        r2 = subprocess.run([os.path.join(pybin, "brain"), "i18n"],
                            capture_output=True, text=True, cwd=tmp, env=env)
        check("★another language also reads from inside the installed package★ (the catalog lives inside the package)",
              "번역 카탈로그" in r2.stdout, (r2.stdout.splitlines() or [""])[0])

        # ── ⑤ ★does the installed package avoid writing a person's config into site-packages★ ────
        out = subprocess.run(
            [py, "-c", "from brain import store; print(store.default_config_path())"],
            capture_output=True, text=True, cwd=tmp, env=env).stdout.strip()
        check("config lives ★outside site-packages★ (deleting the package leaves the config behind)",
              sp not in out, out)

        # ── ⑥ ★do the storage backends ship★ — they are a subpackage, which `packages` must list ──────
        # ⛔ with `packages = ["brain"]` alone, brain/backends/ is left out of the install: every database
        #    but the local copy would be gone, and `brain stores` would not say why
        out = subprocess.run(
            [py, "-c", "from brain import stores; print(sorted(stores.backends()), stores.broken())"],
            capture_output=True, text=True, cwd=tmp, env=env)
        check("★the storage backends ship inside the package★ (one file each in brain/backends/)",
              out.returncode == 0 and "'qdrant'" in out.stdout and "'neo4j'" in out.stdout
              and out.stdout.strip().endswith("[]"), (out.stdout.strip() or out.stderr.strip()[-150:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for d in ("build", "local_brain.egg-info"):
            shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
