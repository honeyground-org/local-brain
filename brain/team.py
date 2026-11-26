"""Team reports and work ledgers — what a team of agents says and keeps, made searchable.

## Why

A person working with a team of agents does not write the team's state down as memories. The state
already lives somewhere else, and the brain never read it:

- **reports** — a lead session receives its teammates' messages (`<agent-message from="...">`, or another
  session's `<teammate-message>`) and its background agents' results (`<task-notification>`) as prompts. They
  are not the person's question; the host adapter knows each envelope (§hosts.Host.report_of).
  Searching with them attached unrelated memories (measured 2026-11-25 on one lead session: 18 attachments,
  11 unrelated, 9 of those from teammate reports — a 600-character report matches dozens of ordinary words,
  so the longest memory scored 5.8 times the threshold while covering 31% of the query).
- **ledgers** — a JSON file the team keeps up to date: work items with an id, a title, a status, steps and
  links, and optionally a list of drafts shown to the person. The ledger is the state; reports are events.

So the hook does not search with a report: it records it (`capture`). And `materialize` turns the ledger and
the recorded reports into small Markdown documents inside a folder the brain owns. That folder is an ordinary
source named `team`, so the indexer, recall and the hook need nothing new.

## Rules this module keeps

- ⛔ **A ledger is only read.** Nothing is ever written next to it; output goes to the `team` source folder,
  and only files this module generated there are replaced or removed.
- ⛔ **Every document is bounded** (`DOC_CHARS`). A memory that kept growing reached 49,956 characters and
  matched any long question; a ledger item or a busy sender must not become that. The most useful lines come
  first (status, next step, what it waits on, drafts, recent reports), and lower sections are cut first.
- ⛔ **Secrets are masked before anything is stored** (§privacy.scrub) — an agent's result was measured
  quoting the API key it had just created.
- **A report keeps its core in the item it talks about**: the segments that mention the item's id or one of
  its aliases, not the whole report. The full (masked) text stays in the event log, which is not indexed.
- **Off unless configured.** `capture` needs `team.capture: true`; documents need a source named `team`;
  the hook attaches them on its own only with `team.hook: true` (recall finds them either way).

## Config

    "sources": [..., {"name": "team", "path": "~/.brain/team/docs", "include": ["*.md"], "embed": false}],
    "team": {
      "capture": true,
      "hook": false,
      "ledgers": [{"name": "app", "items": "/path/plan/work.json", "drafts": "/path/drafts.json",
                   "alias_fields": ["links.tracker"]}]
    }

Where a ledger answers — and which reports belong to it — is the project its `items` file lives in plus the
projects its work is learned in from the host's session logs (§learn); `folders` is optional and only adds to them.
`alias_fields` name item fields whose values carry other ids for the same item (`"task:D7"` → `D7`).
Run by hand: `python3 -m brain.team [--force]`.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
from typing import Dict, Iterable, List, Optional, Tuple

DOC_CHARS = 4000            # one generated document, at most (plus its frontmatter)
EVENT_CHARS = 20000         # one recorded report, at most
EXCERPT_CHARS = 400         # the part of a report kept inside an item it mentions
SENDER_LINE_CHARS = 600     # one report inside its sender's document
ITEM_REPORTS = 5            # newest reports kept per item
SENDER_REPORTS = 10         # newest reports kept per sender
EVENT_MONTHS = 2            # months of the event log read back
EXCERPT_MIN = 160           # a hit shorter than this takes the segments after it (a heading alone says nothing)
FORMAT = 2                  # bump when the generated documents change shape (forces a rebuild)
MARKER = "generated: team-ledger"

_ID = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,3}\d{1,5})(?![A-Za-z0-9])")
_SEGMENT = re.compile(r"(?:\n+|(?=[①-⑳])|(?<=[.!?])\s+)")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


# ── reports ─────────────────────────────────────────────────────────────────────────────────────

def report_of(prompt: str, host=None) -> Optional[dict]:
    """A teammate's message or a background agent's result, or None for anything a person typed.

    ★The wrapper is the host's fact★ (§hosts.Host.report_of): each host delivers another agent's words in its
    own envelope, so the host adapter that knows the envelope decides. A host whose envelope was never
    measured answers None, and its prompts are searched exactly as before."""
    if not prompt or "<" not in prompt[:64]:
        return None
    if host is None:
        from brain import hosts
        host = hosts.active()
    try:
        return host.report_of(prompt)
    except Exception:                                    # noqa: BLE001
        return None


def settings(cfg: Optional[dict] = None) -> dict:
    if cfg is None:
        from brain import store
        try:
            cfg = store.load_config(tolerant=True)
        except Exception:                                # noqa: BLE001
            cfg = {}
    t = (cfg or {}).get("team") or {}
    return t if isinstance(t, dict) else {}


def hook_enabled(cfg: Optional[dict] = None) -> bool:
    """May the hook attach generated documents on its own (recall finds them regardless)."""
    return bool(settings(cfg).get("hook"))


def reference_enabled(cfg: Optional[dict] = None) -> bool:
    """Is the reference lane on — the documents searched in an index of their own and offered after the
    hook's answer, never ranked with memories (§brain/lane.py)."""
    return bool(settings(cfg).get("reference"))


def lane_path() -> str:
    from brain import store
    return os.path.join(store.brain_home(), "team", "lane.db")


def events_dir() -> str:
    from brain import store
    return os.path.join(store.brain_home(), "team", "events")


def capture(report: dict, session: str = "", cwd: str = "", cfg: Optional[dict] = None,
            now: Optional[float] = None) -> Optional[str]:
    """Append one report, secrets masked, to this month's event log. Returns the file, or None when off."""
    if not settings(cfg).get("capture"):
        return None
    from brain import privacy
    text, masked = privacy.scrub(report.get("text") or "")
    now = time.time() if now is None else now
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "ts": round(now, 3),
           "kind": report.get("kind", ""), "from": (report.get("from") or "")[:120],
           "status": report.get("status", ""), "session": session or "", "cwd": cwd or "",
           "text": text[:EVENT_CHARS], "masked": masked}
    d = events_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, "events-%s.jsonl" % time.strftime("%Y-%m", time.localtime(now)))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _event_files() -> List[str]:
    return sorted(glob.glob(os.path.join(events_dir(), "events-*.jsonl")))[-EVENT_MONTHS:]


def load_events() -> List[dict]:
    out = []
    for path in _event_files():
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("text"):
                        out.append(row)
        except OSError:
            continue
    out.sort(key=lambda r: r.get("ts") or 0)
    return out


# ── documents ───────────────────────────────────────────────────────────────────────────────────

def _clip(text, n: int) -> str:
    s = " ".join(str(text if text is not None else "").split())
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def _short_time(at: str) -> str:
    """'2026-11-25T10:51:03' or '2026-11-25 10:51' → '11-25 10:51'."""
    m = re.match(r"\d{4}-(\d{2}-\d{2})[T ](\d{2}:\d{2})", at or "")
    return "%s %s" % (m.group(1), m.group(2)) if m else (at or "")


def _get(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _strings(v) -> Iterable[str]:
    if isinstance(v, str):
        yield v
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)


def _ids_in(text: str) -> List[str]:
    return _ID.findall(text or "")


def _text(v, n: int) -> str:
    return _clip(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False), n)


def _excerpt(text: str, token: str) -> str:
    """The segments of a report that mention one id — its core for that item, not the whole report.

    ★A hit that is only a heading takes the segments after it★ (2026-11-25): a report that opens with
    "fixer-17 D297 ①② done" is cut before each circled number, and the hit alone was "fixer-17 D297" — the
    item got the name of the report and none of what it said. So a short hit runs on into the following
    segments until it says something, and stops before a segment that names another id (that one belongs to
    another item's excerpt)."""
    parts = [p.strip() for p in _SEGMENT.split(text or "") if p and p.strip()]
    pat = re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(token))
    picked, used = [], set()
    for i, p in enumerate(parts):
        if i in used or not pat.search(p):
            continue
        seg, j = [p], i + 1
        used.add(i)
        while sum(len(s) + 1 for s in seg) < EXCERPT_MIN and j < len(parts):
            if any(t != token for t in _ids_in(parts[j])):
                break
            seg.append(parts[j])
            used.add(j)
            j += 1
        picked.append(" ".join(seg))
    return _clip(" · ".join(picked) if picked else text, EXCERPT_CHARS)


def _slug(name: str) -> str:
    return _SAFE.sub("-", name).strip("-")[:80] or "x"


def _front(kind: str, description: str) -> str:
    return ("---\ndescription: %s\ntype: %s\n%s\n---\n"
            % (json.dumps(_clip(description, 240), ensure_ascii=False), kind, MARKER))


def _bounded(head: List[str], sections: List[Tuple[str, List[str]]]) -> str:
    """Head lines always; then each section in order while the budget lasts — lower sections are cut first."""
    out = list(head)
    used = sum(len(x) + 1 for x in out)
    for title, lines in sections:
        if not lines:
            continue
        block = ["", "## " + title]
        cost = sum(len(x) + 1 for x in block)
        if used + cost >= DOC_CHARS:
            break
        out += block
        used += cost
        for line in lines:
            if used + len(line) + 1 > DOC_CHARS - 4:
                out.append("- …")
                used += 4
                break
            out.append(line)
            used += len(line) + 1
    return "\n".join(out)[:DOC_CHARS]


def _read_json(path: str):
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        return json.load(fh)


def _items_of(data, key: str = "items") -> List[dict]:
    rows = data.get(key) if isinstance(data, dict) else data
    return [r for r in (rows or []) if isinstance(r, dict)]


def _item_doc(item: dict, aliases: List[str], drafts: List[dict], reports: List[dict]) -> str:
    iid = str(item.get("id"))
    title = _clip(item.get("title") or "", 300)
    status = _clip(item.get("status") or "", 40)
    head = ["# %s %s" % (iid, title), ""]
    for label, key, n in (("status", "status", 40), ("next", "next", 400), ("waiting on", "waiting_on", 300),
                          ("done when", "done_when", 500), ("result", "result", 300)):
        if item.get(key):
            head.append("- %s: %s" % (label, _text(item[key], n)))
    asked = item.get("asked")
    if isinstance(asked, dict) and (asked.get("words") or asked.get("at")):
        head.append("- asked: %s %s" % (_short_time(str(asked.get("at") or "")), _clip(asked.get("words") or "", 300)))
    elif isinstance(asked, str) and asked:
        head.append("- asked: %s" % _clip(asked, 300))
    if aliases:
        head.append("- also known as: %s" % ", ".join(aliases[:30]))
    related = [x for x in dict.fromkeys(_ids_in(" ".join(_strings(item.get("links")))))
               if x != iid and x not in aliases]
    if related:
        head.append("- related: %s" % ", ".join(related[:20]))
    if item.get("closed"):
        head.append("- closed: %s" % _text(item["closed"], 120))
    draft_lines = ["- %s %s" % (_short_time(str(d.get("modified") or "")), _clip(d.get("title") or "", 200))
                   for d in sorted(drafts, key=lambda d: str(d.get("modified") or ""), reverse=True)]
    report_lines = ["- %s %s: %s" % (_short_time(r.get("at", "")), _clip(r.get("from", ""), 60), r["_excerpt"])
                    for r in reports]
    steps = []
    for s in item.get("steps") or []:
        if isinstance(s, dict):
            steps.append("- [%s] %s%s" % (_clip(s.get("status") or "", 20), _clip(s.get("what") or "", 160),
                                         (" — " + _text(s["result"], 240)) if s.get("result") else ""))
    more = []
    asked_more = item.get("asked_more")
    for a in (asked_more if isinstance(asked_more, list) else [asked_more] if asked_more else []):
        if isinstance(a, dict):
            more.append("- %s %s" % (_short_time(str(a.get("at") or "")),
                                     _clip(a.get("words") or a.get("what") or "", 300)))
        elif isinstance(a, str):
            more.append("- " + _clip(a, 300))
    body = _bounded(head, [("drafts", draft_lines), ("recent reports", report_lines),
                           ("steps", steps), ("asked again", more)])
    return _front("ledger", "%s · %s · %s" % (iid, status or "-", title)) + body + "\n"


def _drafts_doc(drafts: List[dict]) -> str:
    rows = sorted(drafts, key=lambda d: str(d.get("modified") or ""), reverse=True)
    lines = []
    for d in rows:
        where = os.path.basename(str(d.get("file") or ""))
        lines.append("- %s %s%s" % (_short_time(str(d.get("modified") or "")), _clip(d.get("title") or "", 200),
                                    (" (%s)" % where) if where else ""))
    latest = rows[0] if rows else {}
    when = _short_time(str(latest.get("modified") or ""))
    head = ["# drafts shown, newest first", "",
            ("- latest: %s %s" % (when, _clip(latest.get("title") or "", 200))) if latest else "- none yet"]
    desc = "drafts timeline · latest %s %s" % (when, _clip(latest.get("title") or "", 120))
    return _front("ledger", desc) + _bounded(head, [("all drafts", lines)]) + "\n"


def _sender_doc(sender: str, rows: List[dict]) -> str:
    rows = list(reversed(rows[-SENDER_REPORTS:]))
    lines = ["- %s%s: %s" % (_short_time(r.get("at", "")), (" [%s]" % r["status"]) if r.get("status") else "",
                             _clip(r.get("text", ""), SENDER_LINE_CHARS)) for r in rows]
    last = rows[0] if rows else {}
    head = ["# reports from %s" % sender, "", "- last: %s" % _short_time(last.get("at", ""))]
    desc = "reports from %s · last %s · %s" % (sender, _short_time(last.get("at", "")),
                                               _clip(last.get("text", ""), 120))
    return _front("team", desc) + _bounded(head, [("newest first", lines)]) + "\n"


def _above(path: str, folders: List[str]) -> bool:
    """Is `path` a folder that holds one of `folders` — a parent workspace."""
    if not path:
        return False
    p = os.path.normpath(os.path.expanduser(path)).rstrip(os.sep) + os.sep
    return any(os.path.normpath(os.path.expanduser(f)).startswith(p) for f in folders or [])


def _under(path: str, folders: List[str]) -> bool:
    if not path:
        return False
    p = os.path.normpath(os.path.expanduser(path))
    for f in folders or []:
        f = os.path.normpath(os.path.expanduser(f))
        if p == f or p.startswith(f + os.sep):
            return True
    return False


def _ledgers(cfg: dict) -> List[dict]:
    return [l for l in (settings(cfg).get("ledgers") or []) if isinstance(l, dict) and l.get("name")]


def _ledger_of(ev: dict, ledgers: List[dict]) -> str:
    for led in ledgers:
        if _under(ev.get("cwd", ""), led.get("folders") or []):
            return _slug(str(led.get("name")))
    return ""


def _groups(events: List[dict], ledgers: List[dict]) -> Dict[str, List[dict]]:
    """Which ledger each report belongs to.

    A report's own folder decides first. ★A report from a folder outside every ledger takes the ledger its
    session was seen under most★ (2026-11-25): a lead session's shell moves between folders, and the host
    reports the folder it is in at that moment — measured on one lead session, 4 of its 12 reports arrived
    from other folders and missed their items. A session never seen under a ledger keeps its own folder."""
    own = [_ledger_of(ev, ledgers) for ev in events]
    seen: Dict[str, Dict[str, int]] = {}
    for ev, g in zip(events, own):
        if g and ev.get("session"):
            counts = seen.setdefault(ev["session"], {})
            counts[g] = counts.get(g, 0) + 1
    out: Dict[str, List[dict]] = {}
    for ev, g in zip(events, own):
        if not g:
            counts = seen.get(ev.get("session") or "")
            g = (max(counts, key=lambda k: counts[k]) if counts else
                 _slug(os.path.basename(os.path.normpath(ev.get("cwd") or "")) or "other"))
        out.setdefault(g, []).append(ev)
    return out



# ── where a ledger's work happens ──────────────────────────────────────────────────────────────────

LEARN_FILES = 300           # newest session logs read: 3.0 s for 298 here (2026-11-26)
LEARN_MIN = 3               # sessions in one folder that named the ledger, at least
LEARN_SHARE = 0.5           # ... and at least this share of that folder's sessions
LEARN_HOURS = 1             # how long what was learned is kept before the logs are read again (a new clone is known within the hour)
FOLDERS_FILE = "folders.json"


def _is_workspace(path: str) -> bool:
    """A folder that holds other repositories (a workspace checkout of many projects), not a project itself."""
    try:
        with os.scandir(path) as it:
            return any(e.is_dir() and os.path.exists(os.path.join(e.path, ".git")) for e in it)
    except OSError:
        return False


def _project_of(path: str) -> str:
    """The project a folder belongs to: its repository root — or, when that root is a workspace holding other
    repositories, the workspace's child the folder sits in. ★A clone is a project the moment it exists★; what
    it is about is learned from the work done in it (§learn), never from a list someone keeps."""
    p = os.path.normpath(os.path.expanduser(path or ""))
    if not p or p == os.sep:
        return ""
    q = p
    while True:
        if os.path.exists(os.path.join(q, ".git")):
            break
        up = os.path.dirname(q)
        if up == q:
            return p
        q = up
    if q != p and _is_workspace(q):
        return os.path.join(q, os.path.relpath(p, q).split(os.sep)[0])
    return q


def _home(led: dict) -> str:
    """The project the ledger file lives in — a ledger always answers there."""
    items = led.get("items") or ""
    return _project_of(os.path.dirname(os.path.expanduser(items))) if items else ""


def _needles(led: dict) -> List[str]:
    """How a session log names the ledger's files: the full path, and the path from the project's folder name
    (`app/plan/work.json`) — a session working inside that project names it relatively."""
    out: List[str] = []
    for key in ("items", "drafts"):
        p = led.get(key)
        if not p:
            continue
        p = os.path.normpath(os.path.expanduser(p))
        out.append(p)
        home = _project_of(os.path.dirname(p))
        if home:
            out.append(os.path.join(os.path.basename(home), os.path.relpath(p, home)))
    # A log line is JSON: a path is there as the system writes it (escaped, so `\\` doubles), and a tool or a
    # person may also write it with `/` (Windows accepts both).
    forms: List[str] = []
    for x in out:
        forms += [x, x.replace("\\", "/"), json.dumps(x)[1:-1]]
    return list(dict.fromkeys(f for f in forms if f))


def _folders_path(cfg: dict) -> str:
    d = team_dir(cfg)
    return os.path.join(d, FOLDERS_FILE) if d else ""


def learned(cfg: dict) -> Dict[str, List[str]]:
    """What `learn` found last time, per ledger — read only (the hook calls this on every prompt)."""
    path = _folders_path(cfg)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: [str(f) for f in v] for k, v in (data.get("learned") or {}).items() if isinstance(v, list)}
    except (OSError, ValueError, AttributeError):
        return {}


def learn(cfg: dict, force: bool = False) -> dict:
    """Which projects each ledger's work happens in, read off the host's session logs.

    ★Nobody has to say where the work is★ (2026-11-26): the ledger's `folders` were written by hand, the work
    moved to another repository, and the lane went on answering the old folder only — the questions typed in the
    new one were counted as unrelated. A project is learned when at least `LEARN_MIN` of its recent sessions,
    and `LEARN_SHARE` of them, named the ledger's file (measured here: 40 of 48 sessions in the repository the
    work moved to, against 58 of 207 in the workspace above it and 1 or 2 in a few others). A workspace that
    holds the ledger's project is never learned — it holds every other project too."""
    path = _folders_path(cfg)
    ledgers = _ledgers(cfg)
    if not path or not ledgers:
        return {"skipped": "no ledger"}
    key = hashlib.sha1(json.dumps([[l.get("name"), _needles(l)] for l in ledgers], sort_keys=True).encode()).hexdigest()
    try:
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
        if not force and old.get("key") == key and time.time() - float(old.get("at") or 0) < LEARN_HOURS * 3600:
            return {"unchanged": True, "learned": old.get("learned") or {}}
    except (OSError, ValueError, AttributeError):
        pass
    from brain import hosts
    host = hosts.active()
    out_learned: Dict[str, List[str]] = {}
    evidence: Dict[str, Dict[str, List[int]]] = {}
    for led in ledgers:
        name = _slug(str(led["name"]))
        keep = [f for f in [_home(led)] + list(led.get("folders") or []) if f]
        counts: Dict[str, List[int]] = {}
        for folder, named in host.sessions(_needles(led), recent_files=LEARN_FILES):
            proj = _project_of(folder)
            if proj:
                c = counts.setdefault(proj, [0, 0])
                c[0] += int(bool(named))
                c[1] += 1
        evidence[name] = {p: c for p, c in sorted(counts.items(), key=lambda kv: -kv[1][0]) if c[0]}
        out_learned[name] = sorted(p for p, (hit, n) in counts.items()
                                   if hit >= LEARN_MIN and hit >= LEARN_SHARE * n
                                   and not _under(p, keep) and not _above(p, keep))
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"key": key, "at": time.time(), "learned": out_learned, "named": evidence}, fh,
                  ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return {"learned": out_learned}


def folders_of(led: dict, found: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """Where a ledger answers: the folders the person named, the project its file lives in, and the projects its
    work was learned in (§learn)."""
    out = list(led.get("folders") or []) + [_home(led)] + list((found or {}).get(_slug(str(led["name"])), []))
    return list(dict.fromkeys(os.path.normpath(os.path.expanduser(f)) for f in out if f))


def _effective(cfg: dict) -> List[dict]:
    found = learned(cfg)
    return [dict(led, folders=folders_of(led, found)) for led in _ledgers(cfg)]


def team_dir(cfg: dict) -> str:
    """Where the generated documents go: the `team` source's folder when the person indexes them with their
    memories; otherwise, with the reference lane on, `team.docs` or `<brain home>/team/docs` — a folder no
    source reads, so the main index never sees them. "" when neither is configured (nothing is generated)."""
    for s in (cfg or {}).get("sources", []) or []:
        if s.get("name") == "team" and s.get("path"):
            return os.path.expanduser(s["path"])
    t = settings(cfg)
    if t.get("reference"):
        if t.get("docs"):
            return os.path.expanduser(str(t["docs"]))
        from brain import store
        return os.path.join(store.brain_home(), "team", "docs")
    return ""


_HEAD_ID = re.compile(r"^# (\S+) ", re.M)
_ALIAS_LINE = re.compile(r"^- also known as: (.+)$", re.M)


def _doc_ids(rel: str, text: str) -> List[str]:
    """The identifiers an item document answers to exactly: its own id (the heading) and its aliases."""
    out: List[str] = []
    m = _HEAD_ID.search(text)
    if m and _ID.fullmatch(m.group(1)):
        out.append(m.group(1))
    a = _ALIAS_LINE.search(text)
    if a:
        out += [x for x in _ids_in(a.group(1))]
    return out


def reference(prompt: str, cwd: str = "", cfg: Optional[dict] = None, k: int = 2) -> List[dict]:
    """What the reference lane offers for this prompt — [] unless `team.reference` is on.

    ★Only the ledgers of the folder the person works in★: a ledger with `folders` answers only a session under
    one of them (a ledger without `folders` answers everywhere), and reports grouped by a folder answer only
    that folder. An identifier the prompt names (an item id or alias) is a lookup and passes regardless of score;
    anything else must clear the lane's own threshold (§lane)."""
    if cfg is None:
        from brain import store
        cfg = store.load_config(tolerant=True)
    if not reference_enabled(cfg):
        return []
    groups = set()
    for led in _effective(cfg):
        if not led.get("folders") or _under(cwd, led.get("folders") or []):
            groups.add(_slug(str(led["name"])))
    if cwd:
        # reports grouped by the folder they arrived in (§_groups) answer that folder — ★never under a ledger's
        # name★: measured 2026-11-25, two sessions in worktrees ending in `apps/lumi` (outside the ledger's
        # folders) got the `lumi` ledger because the folder name equalled the ledger name.
        own = _slug(os.path.basename(os.path.normpath(cwd)))
        if own not in {_slug(str(l["name"])) for l in _ledgers(cfg)}:
            groups.add(own)
    if not groups:
        return []
    from brain import lane
    return lane.search(lane_path(), prompt, k=k, groups=sorted(groups), ids=_ids_in(prompt))


def _fingerprint(ledgers: List[dict]) -> str:
    h = hashlib.sha1(("format %d" % FORMAT).encode())
    paths = list(_event_files())
    for led in ledgers:
        h.update(json.dumps(led, sort_keys=True, ensure_ascii=False).encode())
        paths += [led.get("items") or "", led.get("drafts") or ""]
    for p in paths:
        try:
            st = os.stat(os.path.expanduser(p))
            h.update(("%s %d %d" % (p, st.st_size, st.st_mtime_ns)).encode())
        except OSError:
            h.update(("%s missing" % p).encode())
    return h.hexdigest()


def build(cfg: dict) -> Dict[str, str]:
    """Every document this configuration produces, as {relative path: text}. Reads, never writes."""
    ledgers = _effective(cfg)
    groups = _groups(load_events(), ledgers)
    docs: Dict[str, str] = {}
    for led in ledgers:
        name = _slug(str(led["name"]))
        try:
            items = _items_of(_read_json(led["items"]), led.get("items_key") or "items") if led.get("items") else []
        except (OSError, ValueError):
            items = []
        try:
            drafts = _items_of(_read_json(led["drafts"]), led.get("drafts_key") or "items") if led.get("drafts") else []
        except (OSError, ValueError):
            drafts = []
        owner: Dict[str, str] = {}
        aliases: Dict[str, List[str]] = {}
        for it in items:
            iid = str(it.get("id") or "")
            if iid:
                owner[iid] = iid
        for it in items:
            iid = str(it.get("id") or "")
            if not iid:
                continue
            found = []
            for field in led.get("alias_fields") or []:
                for s in _strings(_get(it, field)):
                    found += _ids_in(s.rsplit(":", 1)[-1])
            aliases[iid] = [a for a in dict.fromkeys(found) if a != iid]
            for a in aliases[iid]:
                owner.setdefault(a, iid)
        reports: Dict[str, List[dict]] = {}
        for ev in groups.get(name, []):
            seen = set()
            for tok in _ids_in(ev.get("text", "")):
                iid = owner.get(tok)
                if iid and iid not in seen:
                    seen.add(iid)
                    reports.setdefault(iid, []).append(dict(ev, _excerpt=_excerpt(ev.get("text", ""), tok)))
        by_item: Dict[str, List[dict]] = {}
        for d in drafts:
            for tok in dict.fromkeys(_ids_in(str(d.get("title") or ""))):
                if owner.get(tok):
                    by_item.setdefault(owner[tok], []).append(d)
        for it in items:
            iid = str(it.get("id") or "")
            if iid:
                recent = list(reversed(reports.get(iid, [])[-ITEM_REPORTS:]))
                docs["%s/%s.md" % (name, _slug(iid))] = _item_doc(it, aliases.get(iid, []),
                                                                   by_item.get(iid, []), recent)
        if drafts:
            docs["%s/drafts.md" % name] = _drafts_doc(drafts)
    for group, rows in groups.items():
        senders: Dict[str, List[dict]] = {}
        for ev in rows:
            senders.setdefault(ev.get("from") or "agent", []).append(ev)
        for sender, srows in senders.items():
            docs["%s/from/%s.md" % (group, _slug(sender))] = _sender_doc(sender, srows)
    return docs


def _generated(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return MARKER in fh.read(2000)
    except OSError:
        return False


def materialize(cfg: Optional[dict] = None, force: bool = False) -> dict:
    """Write what `build` produces into the `team` source folder: only changed files are written, and only
    files this module generated are removed. Cheap when nothing changed (a fingerprint of the inputs)."""
    if cfg is None:
        from brain import store
        cfg = store.load_config(tolerant=True)
    out_dir = team_dir(cfg)
    if not out_dir:
        return {"skipped": "no team source"}
    state_path = os.path.join(out_dir, ".state.json")
    try:
        learnt = learn(cfg, force=force)
    except Exception as exc:                             # noqa: BLE001 — a log that cannot be read keeps what was known
        learnt = {"error": type(exc).__name__}
    fp = _fingerprint(_effective(cfg))
    try:
        with open(state_path, encoding="utf-8") as fh:
            if not force and json.load(fh).get("fingerprint") == fp:
                if reference_enabled(cfg):
                    return {"unchanged": True, "folders": learnt.get("learned", learnt), "lane": _rebuild_lane(out_dir)}
                return {"unchanged": True}
    except (OSError, ValueError):
        pass
    docs = build(cfg)
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    written = removed = 0
    for rel, text in docs.items():
        path = os.path.join(out_dir, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        written += 1
    keep = {os.path.normpath(os.path.join(out_dir, rel)) for rel in docs}
    for path in glob.glob(os.path.join(out_dir, "**", "*.md"), recursive=True):
        if os.path.normpath(path) not in keep and _generated(path):
            os.remove(path)
            removed += 1
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump({"fingerprint": fp, "documents": len(docs), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh)
    out = {"documents": len(docs), "written": written, "removed": removed, "folders": learnt.get("learned", learnt)}
    if reference_enabled(cfg):
        out["lane"] = _rebuild_lane(out_dir, force=bool(force or written or removed))
    return out


def _rebuild_lane(out_dir: str, force: bool = False) -> dict:
    """Index the generated documents into the reference lane — when they changed, or the lane is missing."""
    from brain import lane
    if not force and lane.info(lane_path()).get("folder") == out_dir:
        return {"unchanged": True}
    m = lane.build(out_dir, lane_path(), ids_of=_doc_ids)
    return {k: m.get(k) for k in ("docs", "floor", "threshold", "seconds")}


def main() -> int:
    import sys
    print(json.dumps(materialize(force="--force" in sys.argv[1:]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
