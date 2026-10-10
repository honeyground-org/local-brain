"""Scheduler adapter — ★answers the same question on macOS·Windows·Linux.★

## What it asks

brain's reliability axis asks one thing only: ★did the scheduled job run on its own at the scheduled time.★
(This judgement was fixed on 2026-09-01 — before that, "run by hand" also turned green.)

## ⛔ ★The strength of the evidence differs by platform — that isn't hidden★

| | tool | evidence available | strength |
|---|---|---|---|
| macOS | `launchctl print` | ★run count (`runs`)★ · exit code · scheduled time | strongest |
| Windows | `schtasks /query /v` | last-run time · last result · next run | middle |
| Linux | cron | ★none★ — crontab keeps no run history | falls back to logs |

⛔ A weak-evidence platform must not show ★the same green★. So each adapter states what it knows
via `evidence`, and the judging side answers according to that strength.
★Saying you don't know what you don't know is this repository's rule.★

## ⚠️ What no platform can tell apart

`launchctl kickstart` and `schtasks /run` are ★a person running it★, but the OS keeps no separate
record of that fact. So the last piece of "it ran on its own" is judged by ★whether it fired after
the scheduled time★ (§health).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

TIMEOUT = 5


class Scheduler:
    """A single platform's scheduler. ⛔ What it can't do, it answers ★empty-handed★ (it doesn't make it up)."""

    name = "none"
    label = "no scheduler"
    evidence = "none"                    # runs | last_run | none

    def available(self) -> bool:
        return False

    def query(self, job: str, label: str) -> dict:
        """★How many times · when · with what result★ did this job run. An unknown field is None."""
        return {"known": False, "runs": None, "stdout": None,
                "last_exit": None, "at": None, "last_run": None}

    def install(self, label: str, command: List[str], at: Tuple[int, int],
                log: str) -> dict:
        return {"ok": False, "why": "this platform has no scheduler"}

    def info(self) -> dict:
        return {"name": self.name, "label": self.label,
                "evidence": self.evidence, "available": self.available()}


class Launchd(Scheduler):
    """macOS. ★Gives a run count — the strongest evidence.★"""

    name = "launchd"
    label = "macOS launchd"
    evidence = "runs"

    def available(self) -> bool:
        return sys.platform == "darwin" and _which("launchctl")

    def query(self, job: str, label: str) -> dict:
        out = Scheduler.query(self, job, label)
        try:
            p = subprocess.run(["launchctl", "print", "gui/%d/%s" % (os.getuid(), label)],
                               capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError, AttributeError):
            return out
        if p.returncode != 0:
            return out                                   # not registered
        txt, out["known"] = p.stdout, True
        m = re.search(r"^\s*runs = (\d+)", txt, re.M)
        if m:
            out["runs"] = int(m.group(1))
        m = re.search(r"^\s*stdout path = (.+)$", txt, re.M)
        if m:
            out["stdout"] = m.group(1).strip()
        m = re.search(r"^\s*last exit code = (.+)$", txt, re.M)
        if m:
            out["last_exit"] = m.group(1).strip()
        # ★doesn't hard-code the scheduled time★ — reads the value launchd knows
        h = re.search(r'"Hour"\s*=>\s*(\d+)', txt)
        mi = re.search(r'"Minute"\s*=>\s*(\d+)', txt)
        if h:
            out["at"] = (int(h.group(1)), int(mi.group(1)) if mi else 0)
        return out

    def install(self, label: str, command: List[str], at: Tuple[int, int],
                log: str) -> dict:
        plist = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % label)
        args = "".join("    <string>%s</string>\n" % _xml(a) for a in command)
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                '  <key>Label</key><string>%s</string>\n'
                '  <key>ProgramArguments</key><array>\n%s  </array>\n'
                '  <key>StartCalendarInterval</key><dict>\n'
                '    <key>Hour</key><integer>%d</integer>\n'
                '    <key>Minute</key><integer>%d</integer>\n'
                '  </dict>\n'
                '  <key>RunAtLoad</key><false/>\n'
                '  <key>StandardOutPath</key><string>%s</string>\n'
                '  <key>StandardErrorPath</key><string>%s</string>\n'
                '</dict></plist>\n' % (_xml(label), args, at[0], at[1],
                                       _xml(log), _xml(log)))
        try:
            os.makedirs(os.path.dirname(plist), exist_ok=True)
            with open(plist, "w", encoding="utf-8") as fh:
                fh.write(body)
            subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), label)],
                           capture_output=True, timeout=TIMEOUT)
            p = subprocess.run(["launchctl", "bootstrap", "gui/%d" % os.getuid(), plist],
                               capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:      # noqa: BLE001
            return {"ok": False, "why": str(exc)[:120]}
        return {"ok": p.returncode == 0, "path": plist,
                "why": (p.stderr or "").strip()[:120]}


class Schtasks(Scheduler):
    """Windows Task Scheduler. ★Gives the last-run time and result — no count.★"""

    name = "schtasks"
    label = "Windows Task Scheduler"
    evidence = "last_run"

    def available(self) -> bool:
        return os.name == "nt" and _which("schtasks")

    def query(self, job: str, label: str) -> dict:
        out = Scheduler.query(self, job, label)
        try:
            p = subprocess.run(["schtasks", "/query", "/tn", label, "/fo", "LIST", "/v"],
                               capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return out
        if p.returncode != 0:
            return out
        txt, out["known"] = p.stdout, True
        # ⛔ the label gets translated per locale — ★this looks for both English and Korean★.
        #    (if it still can't find one, it's None, and the judging side falls back to logs.)
        out["last_run"] = _field(txt, ("Last Run Time", "마지막 실행 시간"))
        res = _field(txt, ("Last Result", "마지막 결과"))
        if res is not None:
            out["last_exit"] = res.strip()
        nxt = _field(txt, ("Start Time", "시작 시간", "Next Run Time", "다음 실행 시간"))
        if nxt:
            m = re.search(r"(\d{1,2}):(\d{2})", nxt)
            if m:
                out["at"] = (int(m.group(1)), int(m.group(2)))
        return out

    def install(self, label: str, command: List[str], at: Tuple[int, int],
                log: str) -> dict:
        # ⛔ the scheduler doesn't do log redirection for you — wrap it with `cmd /c ... >> log 2>&1`.
        quoted = " ".join('"%s"' % a if " " in a else a for a in command)
        wrapped = 'cmd /c %s >> "%s" 2>&1' % (quoted, log)
        try:
            p = subprocess.run(
                ["schtasks", "/create", "/tn", label, "/tr", wrapped,
                 "/sc", "daily", "/st", "%02d:%02d" % at, "/f"],
                capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:      # noqa: BLE001
            return {"ok": False, "why": str(exc)[:120]}
        return {"ok": p.returncode == 0, "why": (p.stderr or p.stdout or "").strip()[:120]}


class Cron(Scheduler):
    """Linux etc. ⛔ ★crontab keeps no run history★ — it only knows whether it's registered."""

    name = "cron"
    label = "cron"
    evidence = "none"

    def available(self) -> bool:
        return os.name == "posix" and sys.platform != "darwin" and _which("crontab")

    def query(self, job: str, label: str) -> dict:
        out = Scheduler.query(self, job, label)
        try:
            p = subprocess.run(["crontab", "-l"], capture_output=True, text=True, errors="replace",
                               timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return out
        if p.returncode != 0:
            return out
        for line in p.stdout.splitlines():
            if label in line or job in line:
                out["known"] = True                      # ⛔ registered ≠ ran
                m = re.match(r"\s*(\d+)\s+(\d+)\s", line)
                if m:
                    out["at"] = (int(m.group(2)), int(m.group(1)))
                break
        return out


def _which(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def _xml(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _field(txt: str, names) -> Optional[str]:
    for n in names:
        m = re.search(r"^\s*%s\s*:\s*(.+)$" % re.escape(n), txt, re.M | re.I)
        if m:
            return m.group(1).strip()
    return None


_ALL = (Launchd(), Schtasks(), Cron(), Scheduler())
_active: Optional[Scheduler] = None


def all_schedulers() -> tuple:
    return _ALL


def active(refresh: bool = False) -> Scheduler:
    """This machine's scheduler. Can be pinned with `BRAIN_SCHEDULER` (used by the check)."""
    global _active
    if _active is not None and not refresh:
        return _active
    want = (os.environ.get("BRAIN_SCHEDULER") or "").strip().lower()
    if want:
        for s in _ALL:
            if s.name == want:
                _active = s
                return _active
    _active = next((s for s in _ALL if s.available()), _ALL[-1])
    return _active
