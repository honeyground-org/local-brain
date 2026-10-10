#!/usr/bin/env python3
"""★brain reads and writes UTF-8 whatever the platform's default★ — Windows included. (local · budget 0)

## Why (2026-10-10)

Windows' default text encoding is the ANSI code page (cp1252 and the like), not UTF-8. Two ways that
broke brain there, both quietly:

  · the recall hook printed a Korean memory into the host's pipe, raised UnicodeEncodeError, and sent
    ★nothing★ — exit 0, no error (reproduced with PYTHONIOENCODING=cp1252: 3,786 bytes → 0)
  · transcripts, pending records and git logs were opened without an encoding: a Korean line was written
    as an error or read back as garbage

## What this holds down

  ① every text file opened in brain/, its scripts and its checks names its encoding, and every
     subprocess read as text says how to decode it — checked in the source, with controls that must be
     caught
  ② ★every entry point as Windows runs it★ — a cp1252 pipe, and files whose default encoding is not
     UTF-8 (a Latin-1 locale where the machine has one) — fed Korean: the recall hook, the behaviour
     hook, the session hooks (a Korean transcript → a pending record → the next session's notice), the
     MCP server and the CLI answer in UTF-8, with the Korean intact, the same bytes as under UTF-8

How to run:  PYTHONPATH=. python3 tests/verify_utf8_io.py
"""
from __future__ import annotations

import ast
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


# ── ① in the source ─────────────────────────────────────────────────────────
def unnamed(source: str, path: str = "<text>") -> list:
    """[(path, line, what)] — text I/O that leaves the encoding to the platform."""
    out = []
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        kws = {k.arg for k in n.keywords}
        is_open = (isinstance(fn, ast.Name) and fn.id == "open") or (
            isinstance(fn, ast.Attribute) and fn.attr == "open" and isinstance(fn.value, ast.Name)
            and fn.value.id in ("io", "codecs"))
        if is_open:
            mode = ""
            if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                mode = str(n.args[1].value)
            for k in n.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    mode = str(k.value.value)
            if "b" not in mode and "encoding" not in kws:
                out.append((path, n.lineno, "open() as text without an encoding"))
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name in ("run", "check_output", "Popen", "call", "check_call"):
            text = any(k.arg in ("text", "universal_newlines") and not (
                isinstance(k.value, ast.Constant) and k.value.value is False) for k in n.keywords)
            if text and not ({"encoding", "errors"} & kws):
                out.append((path, n.lineno, "%s(text=True) without encoding= or errors=" % name))
        if name in ("read_text", "write_text") and "encoding" not in kws:
            out.append((path, n.lineno, "%s() without an encoding" % name))
    return out


def source() -> None:
    print("① in the source")
    files = sorted(glob.glob(os.path.join(ROOT, "brain", "**", "*.py"), recursive=True)
                   + glob.glob(os.path.join(ROOT, ".github", "scripts", "*.py"))
                   + glob.glob(os.path.join(ROOT, "tests", "*.py")))
    found = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            found += unnamed(fh.read(), os.path.relpath(f, ROOT))
    check("every text file and text subprocess in %d files says how it is encoded" % len(files), not found,
          "; ".join("%s:%d %s" % x for x in found[:6]) + (" …+%d" % (len(found) - 6) if len(found) > 6 else ""))
    for label, code in [
            ("open() of a text file", "f = open(p)\n"),
            ("open() for writing text", "f = open(p, 'w')\n"),
            ("open() with only errors=", "f = open(p, errors='replace')\n"),
            ("io.open()", "import io\nf = io.open(p, 'r')\n"),
            ("subprocess text=True", "subprocess.run(['git', 'log'], capture_output=True, text=True)\n"),
            ("check_output universal_newlines", "subprocess.check_output(c, universal_newlines=True)\n"),
            ("Path.read_text()", "p.read_text()\n")]:
        check("control: %s is caught" % label, bool(unnamed(code)))
    fine = ("f = open(p, 'rb')\nf = open(p, encoding='utf-8')\nwebbrowser.open(u)\nos.open(p, 1)\n"
            "subprocess.run(c, text=True, encoding='utf-8')\nsubprocess.run(c, text=True, errors='replace')\n"
            "subprocess.run(c, capture_output=True)\np.read_text(encoding='utf-8')\n")
    check("…and the named forms pass (binary, encoding=, errors=, webbrowser.open, os.open)", not unnamed(fine),
          str(unnamed(fine)))


# ── ② behind a cp1252 pipe ──────────────────────────────────────────────────
# Korean on purpose, written as escapes so the source stays English (§verify_english_only):
NOTE = "\ub85c\uadf8\uc778_\ubc84\uadf8_\uc815\ub9ac"                # "login bug notes"
PHRASE = "\ub85c\uadf8\uc778 \ubc84\uadf8 \uc815\ub9ac \uc774\uc5b4\uac00\uc790"   # "pick up the login bug notes"


def brain_home(tmp: str) -> dict:
    """A brain with one Korean note that declares a trigger phrase — answered by lookup, not by a score."""
    mem = os.path.join(tmp, "memory")
    os.makedirs(mem)
    with open(os.path.join(mem, NOTE + ".md"), "w", encoding="utf-8") as fh:
        fh.write('---\nname: %s\ndescription: \ub85c\uadf8\uc778 \ubc84\uadf8\ub97c \uace0\uce5c \uc21c\uc11c — \uc138\uc158 \ucfe0\ud0a4\ub97c \uba3c\uc800 \ubcf8\ub2e4\ntriggers: ["%s"]\n'
                 'metadata:\n  type: project\n---\n\n\uc138\uc158 \ucfe0\ud0a4 \ub9cc\ub8cc\uac00 \uc6d0\uc778\uc774\uc5c8\ub2e4. \uc7ac\ud604 → \uc6d0\uc778 → \uace0\uce68.\n' % (NOTE, PHRASE))
    with open(os.path.join(mem, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("# \uae30\uc5b5\n\n- [%s](%s.md)\n" % (NOTE, NOTE))
    cfg = os.path.join(tmp, "config.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": mem, "include": ["*.md"], "max_depth": 1}]}, fh)
    env = {k: v for k, v in os.environ.items() if not k.startswith("BRAIN_") and k != "PYTHONIOENCODING"}
    env.update(BRAIN_HOME=os.path.join(tmp, "home"), BRAIN_CONFIG=cfg, PYTHONPATH=ROOT, BRAIN_LANG="ko",
               BRAIN_ADAPTIVE_LEARN="0", BRAIN_TRANSLIT_MS="0", HOME=tmp, USERPROFILE=tmp)
    r = subprocess.run([sys.executable, "-m", "brain.cli", "index", "--full"], env=env, capture_output=True,
                       timeout=300)
    check("a brain with one Korean note is indexed", r.returncode == 0, r.stderr.decode("utf-8", "replace")[-200:])
    # one rule of their own, in Korean, so the behaviour hook has something to say (§ruledisc)
    r = subprocess.run([sys.executable, "-c", "\n".join([
        "from brain import ruledisc",
        "ruledisc.save({'version': 1, 'disabled': [], 'learned': [{'id': 'mine', 'why': '\ubc30\ud3ec \uc804\uc5d0 \ud655\uc778',",
        "  'tools': ['run_shell'], 'match': ['\ud55c\uae00 \uba85\ub839'], 'memories': [%r], 'once': 'session', 'enabled': True}]})" % NOTE,
        "ruledisc.sync_shell()"])], env=dict(env, PYTHONIOENCODING="utf-8"), capture_output=True, timeout=120)
    check("…with one Korean rule of its own", r.returncode == 0, r.stderr.decode("utf-8", "replace")[-200:])
    return env


def windows_like(env: dict) -> dict:
    """The environment a Windows host gives a hook: a cp1252 pipe, and — where this machine has a Latin-1
    locale — files whose default encoding is not UTF-8 either."""
    w = dict(env, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    for lc in ("en_US.ISO8859-1", "en_US.ISO-8859-1", "de_DE.ISO8859-1"):
        probe = subprocess.run([sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
                               env=dict(w, LC_ALL=lc), capture_output=True, text=True, encoding="utf-8")
        if probe.stdout.strip().lower().replace("-", "").replace("_", "") not in ("utf8", ""):
            w["LC_ALL"] = lc
            break
    return w


def run(env: dict, enc: str, args: list, stdin: bytes) -> subprocess.CompletedProcess:
    e = windows_like(env) if enc != "utf-8" else dict(env, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m"] + args, input=stdin, capture_output=True, timeout=120, env=e)


def pipes(env: dict) -> None:
    w = windows_like(env)
    files = subprocess.run([sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
                           env=w, capture_output=True, text=True, encoding="utf-8").stdout.strip()
    print("\n② every entry point as Windows runs it — pipes cp1252, files %s — fed Korean" % files)
    if os.name != "nt" and files.lower().replace("-", "") in ("utf8",):
        print("   (no Latin-1 locale here: files keep UTF-8 — ① still holds them, and Windows CI runs this natively)")
    prompt = json.dumps({"prompt": PHRASE, "session_id": "utf8-check", "hook_event_name": "UserPromptSubmit"},
                        ensure_ascii=False).encode("utf-8")
    mcp = b"\n".join(json.dumps(x, ensure_ascii=False).encode("utf-8") for x in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "neighbors", "arguments": {"name": NOTE}}}]) + b"\n"
    # a rule speaks once per session — each run is its own session, or the second one is silent by design
    guard = lambda enc: json.dumps({"tool_name": "Bash", "session_id": "utf8-check-" + enc,  # noqa: E731
                                    "tool_input": {"command": "echo '\ud55c\uae00 \uba85\ub839'"}}, ensure_ascii=False).encode("utf-8")
    cases = [
        ("the recall hook (brain-hook)", ["brain.hook", "--host", "claude-code"], prompt, lambda t: NOTE in t),
        ("the MCP server (brain-mcp) — a Korean note asked for by name", ["brain.server"], mcp,
         lambda t: ('\\"name\\": \\"%s\\"' % NOTE) in t and "not found" not in t),   # the Korean name was read, found, answered
        ("the CLI in Korean (brain i18n)", ["brain.cli", "i18n"], b"", lambda t: "\ubc88\uc5ed \uce74\ud0c8\ub85c\uadf8" in t),
        ("the behaviour hook (brain-guard) — a Korean command meets a Korean rule", ["brain.guard", "--host", "claude-code"], guard,
         lambda t: NOTE in t and "\ubc30\ud3ec \uc804\uc5d0 \ud655\uc778" in t),
    ]
    for label, args, stdin, wanted in cases:
        feed = stdin if callable(stdin) else (lambda enc, x=stdin: x)
        a, b = run(env, "utf-8", args, feed("utf-8")), run(env, "cp1252", args, feed("cp1252"))
        try:
            text = b.stdout.decode("utf-8")
            valid = True
        except UnicodeDecodeError:
            text, valid = b.stdout.decode("utf-8", "replace"), False
        check("%s: UTF-8 out, the Korean intact, the same bytes as behind a UTF-8 pipe" % label,
              b.returncode == 0 and valid and wanted(text) and b.stdout == a.stdout and b"Traceback" not in b.stderr,
              "exit %d · %d bytes (UTF-8 pipe: %d)%s" % (b.returncode, len(b.stdout), len(a.stdout),
                                                       " · " + b.stderr.decode("utf-8", "replace")[-120:].strip()
                                                       if b.stderr.strip() else ""))
    sessions(env)


def transcript(path: str) -> None:
    """A Claude Code transcript: a Korean request, then three edits to files with Korean names."""
    lines = [{"type": "user", "message": {"role": "user", "content": "\ub85c\uadf8\uc778 \ubc84\uadf8\ub97c \uace0\uccd0 \uc918 — \uc138\uc158 \ucfe0\ud0a4 \ub9cc\ub8cc"}}]
    for i in range(3):
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t%d" % i, "name": "Edit",
             "input": {"file_path": "/\uc791\uc5c5/\ub85c\uadf8\uc778_%d.py" % i, "old_string": "a", "new_string": "b"}}]}})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))


def sessions(env: dict) -> None:
    """Stop → end → the next session's notice: a Korean transcript is read, kept as a pending record and
    announced — every step under the Windows-like environment, so each file it writes and reads counts."""
    out = {}
    for enc in ("utf-8", "cp1252"):
        home = env["BRAIN_HOME"] + "-" + enc
        e = dict(env, BRAIN_HOME=home)
        tp = os.path.join(os.path.dirname(env["BRAIN_HOME"]), "transcript-%s.jsonl" % enc)
        transcript(tp)
        sid = "utf8-session-%s" % enc
        stop = run(e, enc, ["brain.tail", "stop", "--host", "claude-code"],
                   json.dumps({"session_id": sid, "transcript_path": tp, "cwd": "/\uc791\uc5c5"}).encode("utf-8"))
        end = run(e, enc, ["brain.tail", "end", "--host", "claude-code"],
                  json.dumps({"session_id": sid, "transcript_path": tp, "cwd": "/\uc791\uc5c5"}).encode("utf-8"))
        note = run(e, enc, ["brain.tail", "notice", "--host", "claude-code"],
                   json.dumps({"session_id": "next-" + enc, "source": "startup"}).encode("utf-8"))
        out[enc] = (stop, end, note)
    stop, end, note = out["cp1252"]
    try:
        text = note.stdout.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    check("the session hooks: a Korean transcript kept as a pending record, announced next session in UTF-8",
          stop.returncode == end.returncode == note.returncode == 0 and "\ub85c\uadf8\uc778 \ubc84\uadf8\ub97c \uace0\uccd0 \uc918" in text
          and "/\uc791\uc5c5/\ub85c\uadf8\uc778_2.py" in text and "Traceback" not in (stop.stderr + end.stderr + note.stderr).decode(
              "utf-8", "replace"),
          "%d bytes (UTF-8: %d)%s" % (len(note.stdout), len(out["utf-8"][2].stdout),
                                     "" if text else " · " + note.stderr.decode("utf-8", "replace")[-120:]))
    check("…the same notice as under UTF-8", text.split("last activity")[0] ==
          out["utf-8"][2].stdout.decode("utf-8", "replace").split("last activity")[0])


def main() -> int:
    source()
    tmp = tempfile.mkdtemp(prefix="brain-utf8-")
    try:
        pipes(brain_home(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 78)
    if FAILS:
        print("❌ %d failure(s)" % len(FAILS))
        for f in FAILS:
            print("  · " + f)
        return 1
    print("✅ brain reads and writes UTF-8, whatever the platform's default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
