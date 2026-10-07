"""Behaviour layer ★automatic discovery★ — rules are learned from use, not written by hand (2026-08-26).

## Why this layer is needed

The behaviour layer (§guard) works. But its 12 rules are ★written by hand★ — words like
`.blade.php`, `Seeder` and `artisan` are baked into the code. User instruction (2026-08-26):

  *"A lot of it is triggered by terms and words specific to me → it should evolve, dynamically,
   adapted to each user. The 12 behaviour rules should also be discovered ★automatically, from use★."*

A baked-in list is wrong in two directions. It cannot create a rule that is missing (a new user has
none of their own), and ★it carries rules for actions nobody performs★. Measured 2026-08-26
(172 transcripts · 50 days): of the 31 match strings across the 12 rules, five were ★never once
executed or edited★. They only ever appeared in conversation. Those rules will never fire.
They will never fire.

## ⛔ The obstacle to know first — it was only looking at what it already knew

`bin/brain-guard` pre-filters in the shell, and its list was ★a copy of the 12 known rules★.
To learn an unknown rule it must look, and it was looking only at the known. So this module
reads ★transcripts★ instead of making the hook heavy. It sees the past too, so a new user is
bootstrapped on day one.

## ★Statistics cannot do it — rejected three times★ (measured 2026-08-26)

What separates `git push` (a rule) from `grep` (not a rule)? Four attempts, three rejected.

| Attempted signal        | `git push` | `grep`  | Verdict |
|----------------------|-----------|---------|------|
| Usage frequency          | 928       | 13,053  | ❌ the tool is more frequent |
| Memories containing it   | 4         | 47      | ❌ the tool has more |
| Present in `description` | 0         | 7       | ❌ it comes out backwards |
| ⛔ / "never" / "before" nearby | 25%  | 44%     | ❌ the tool scores higher |

The cause is that ★the writing style fills the denominator★. All 618 memories are written with ⛔,
so there is instructional phrasing near any word. The real difference is grammatical — `grep` is
★used as a tool★ ("grepping showed X"), while `git push` is ★the object of the sentence★
("get approval before pushing"). It is a question of meaning, and statistics cannot catch it.
· The same conclusion was reached twice before: §rerank (cosine → judge) · §lexicon (source ③ dropped,
  *"you cannot manufacture a missing signal with statistics"*)

⇒ ★The cross-reference nominates and the judge decides.★ The cheap local part (signal crossing)
  narrows, and the expensive remote part judges *"is this memory something to know right before
  that action"*. Exactly the criterion the user applied when labelling on 2026-08-25.

⚠️ The judge's non-determinism (giving 20 candidates in one list makes the top score wobble) is
   avoided here — ★only 5 memories per signal★ (measured: at 5 it is stable, [7,8,8,8,9]).

## Signals come in three shapes (one regex cannot catch them)

The first version re-discovered 8/31 (26%). Splitting by shape took it to 19/31 (61%).

  ① imperative  `git push` · `git checkout -b` · `php artisan tinker` (1–3 tokens, ★flags included★)
  ② extension   `.tsx` · `.blade.php`  (★compound extensions★ — take the last piece only and it becomes `.php`)
  ③ name        `Controller.php` · `Seeder` · `MEMORY.md` · `database/migrations`
                (`Seeder` lives inside `ProductSeeder.php` — CamelCase must be split to see it)

⛔ **Strip heredocs first** — ★78%★ of Bash lines are heredoc bodies (387,207 → 85,249), and
   inside them is Python or JS, not shell. Without stripping, `s.replace`, `const` and `print`
   rise to the top of "frequently used commands".

## What it produces

- `discover()` — new rule candidates (signal + attached memories + evidence)
- `stale()`   — ★rules nobody uses★. As important as discovery (the five above are the example)
- Approved rules accumulate in `rules.json`, and `all_rules()` is what the behaviour layer reads.
  ⛔ ★brain ships with no rules★ (2026-10-06, §guard.RULES) — a hand-written table holds one person's
  stack and memory names. The 12 hand-written rules described above now live in their author's own
  `rules.json` (`"origin": "former-builtin"`); for everyone else, every rule starts here.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sqlite3
import time
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import i18n, store


def transcript_pairs(pattern: str = "") -> List[Tuple[object, str]]:
    """`[(host, log file)]` — ★every host this machine has, each with its own logs★ (2026-09-21).

    ⛔ This used to be one fixed glob into Claude Code's folder. On a machine running both, the other
       host's work was ★not counted as zero by accident — it was never looked at★, and a signal nobody
       performs is exactly how "no rule needed" looks. Measured that day: 5,600 Codex tool calls in ten
       days, invisible. ⛔ One function for every reader here — the usage count and the two firing-rate
       measurements used to choose their files separately, and two of the three still read only one host.
    An explicit `pattern` still wins (tests pass one); its files are read as the active host's.
    """
    from brain import hosts
    if pattern:
        h0 = hosts.active()
        return [(h0, f) for f in glob.glob(pattern, recursive=True)]
    pairs: List[Tuple[object, str]] = []
    for h0 in hosts.detected() or [hosts.active()]:
        for pat in h0.transcripts():
            pairs += [(h0, f) for f in glob.glob(pat, recursive=True)]
    return pairs

# ── Signal normalisation, ★canonical★ ───────────────────────────────────────
# ⛔ The memory side and the transcript side call ★the same function★. Written twice they diverge
#    quietly, and then the crossing becomes 0 and nobody notices (a place this repository keeps hitting).
HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
SPLIT = re.compile(r"(?:^|\n|;|&&|\|\||\||\$\(|`|\bthen\b|\bdo\b|\belse\b)")
TOK = re.compile(r"[A-Za-z0-9_.\-/]+")
CODE = re.compile(r"`([^`\n]{2,80})`|```[a-z]*\n(.*?)```", re.S)
WORDISH = re.compile(r"[A-Za-z0-9_.\-/]{2,60}")
MAX_N = 3                      # `git checkout -b` · `php artisan tinker`
# `FOO=bar` · `TASK=$(...)` — assignments in front of a command
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def strip_heredocs(cmd: str) -> str:
    """Strip heredoc bodies — 78% of Bash lines are inside one, and that is not shell (measured 2026-08-26)."""
    out: List[str] = []
    lines = cmd.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = HEREDOC.search(line)
        out.append(line[:m.start()] if m else line)
        i += 1
        if m:
            tag = m.group(1)
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1                                   # skip the closing tag too
    return "\n".join(out)


def command_ngrams(text: str) -> Set[str]:
    """① imperative — 1–3-grams in command position.

    ⛔ Flags are not discarded — `git checkout -b` is a rule and `git checkout` is not.
    """
    out: Set[str] = set()
    for seg in SPLIT.split(text):
        seg = seg.strip()
        if not seg:
            continue
        # ⛔ ★The left side of an assignment is not a command★ — `TASK=$(aws ecs list-tasks …)`
        #    is cut at `$(` leaving `TASK=`, and taking `TASK` from there counts ★a variable name as a command★.
        #    On 2026-08-26 `TASK` (531 occurrences) really did come up as a 10-point suggestion.
        # ⛔ Assignments must be cut ★before TOK is applied★ — the TOK character set has no `=`, so
        #    the first token of `FOO=bar git push` looked like `FOO`, and the env-prefix removal that
        #    already existed (`"=" in toks[0]`) ★never once triggered★. A silent no-op.
        words = seg.split()
        i = 0
        while i < len(words) and _ASSIGN.match(words[i]):
            i += 1
        if i >= len(words):
            continue                                 # all assignments means there is no command
        toks = TOK.findall(" ".join(words[i:]))
        if not toks:
            continue
        toks = list(toks)
        toks[0] = os.path.basename(toks[0])          # /usr/bin/git → git
        for n in range(1, min(MAX_N, len(toks)) + 1):
            g = " ".join(toks[:n])
            if 2 <= len(g) <= 60:
                out.add(g)
    return out


def path_signals(path: str) -> Set[str]:
    """② extension · ③ name. ★Preserves compound extensions and CamelCase suffixes.★"""
    out: Set[str] = set()
    if not path:
        return out
    b = os.path.basename(path)
    parts = b.split(".")
    if len(parts) >= 2:
        out.add("." + parts[-1])                         # .php
        if len(parts) >= 3:
            out.add("." + ".".join(parts[-2:]))          # .blade.php
    if b and not b.startswith("."):
        out.add(b)                                       # MEMORY.md
    caps = re.findall(r"[A-Z][a-z0-9]+", parts[0] if parts else b)
    if caps:
        out.add(caps[-1])                                # ProductSeeder.php → Seeder
        if len(parts) >= 2:
            out.add("%s.%s" % (caps[-1], parts[-1]))     # UserController.php → Controller.php
    segs = [s for s in path.split("/") if s]
    for i in range(len(segs) - 1):
        out.add("%s/%s" % (segs[i], segs[i + 1]))        # database/migrations
    for s in segs[:-1]:
        out.add(s + "/")                                 # local-brain/
    return out


# ── Shape filters ───────────────────────────────────────────────────────────
# ⛔ ★Do not list common command names here★ — that is hardcoding again, and we do not know what is
#    common on someone else's machine. What is filtered here is only ★shapes that cannot be a signal★
#    (digits · a single character · pure punctuation). What counts as a rule is the judge's answer.
_JUNK = re.compile(r"^[\W\d_.\-/]*$")
# ★Shell keywords★ — `if`, `done`, `for` are grammar, not commands. Listing them here is not
# hardcoding (POSIX grammar does not vary by person or environment, and the SPLIT we use to divide
# command positions already handles then/do/else for the same reason).
# ⛔ If you feel the urge to add ★tool names★ (grep, git, aws) here, stop — those vary by environment,
#    and what counts as a rule is the judge's answer (on 2026-08-26, grep turned out to be real).
_SHELL_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "do", "done", "while", "for", "case",
    "esac", "in", "function", "select", "until", "coproc", "return", "break",
    "continue", "local", "export", "unset", "shift", "exit",
}


def _home_fragments() -> Set[str]:
    """Fragments of the home path — `Users/<name>` and `<name>/` are not signals but ★strings that are everywhere★.

    ⛔ The name is not written in the code (that would be hardcoding again, and wrong on another machine).
       It is ★derived from the environment★ by expanding `~`. In the first discovery run on 2026-08-26,
       `Users/<name>` (4,562 occurrences) and `development/` (3,522) came up as 8-point suggestions —
       the judge scores by whether the attached memory is good, and never sees how broad the signal is.
    """
    home = os.path.expanduser("~")
    segs = [x for x in home.split("/") if x]
    out: Set[str] = set()
    for i, x in enumerate(segs):
        out.add(x)
        out.add(x + "/")
        if i + 1 < len(segs):
            out.add("%s/%s" % (x, segs[i + 1]))
    return out


_HOME = _home_fragments()


def usable(sig: str) -> bool:
    """Shape only — meaning is the judge's job."""
    s = (sig or "").strip()
    if len(s) < 2 or len(s) > 60:
        return False
    if _JUNK.match(s):                                   # "2" · "." · "-" · "---"
        return False
    if s in _HOME:                                       # a home folder · a workspace root
        return False
    if s.split()[0] in _SHELL_KEYWORDS:                  # if · done · for
        return False
    letters = sum(1 for c in s if c.isalpha())
    return letters >= 2


# ── ① Extract signals from memories ─────────────────────────────────────────
def signals_from_memories(mem_dir: str = "") -> Dict[str, Dict[str, int]]:
    """signal → {memory name: how many times it appears in that memory}.

    ★Only what is inside backticks★ — memories are Markdown, and commands and paths are written as
    code. Scanning the whole body drowns the signal in prose.
    """
    base = mem_dir or store.memory_dir()
    out: Dict[str, Dict[str, int]] = collections.defaultdict(dict)
    if not base:
        return out
    for p in sorted(glob.glob(os.path.join(base, "*.md"))):
        name = os.path.basename(p)[:-3]
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        found: Set[str] = set()
        for a, b in CODE.findall(txt):
            frag = (a or b).strip()
            if not frag or len(frag) > 400:
                continue
            found |= command_ngrams(frag)
            for tok in WORDISH.findall(frag):
                found |= path_signals(tok)
        for s in found:
            if not usable(s):
                continue
            out[s][name] = txt.count(s)                  # tf for ordering — not a judgement
    return out


# ── ② Count actual actions from the transcripts ─────────────────────────────
def usage_from_transcripts(pattern: str = "",
                           since_days: int = 0) -> Tuple[collections.Counter, dict]:
    """signal → real execution/edit counts, plus the scope of that measurement (returned honestly alongside).

    ⛔ Why the scope comes with it: the verdict "0 uses = an action nobody performs" ★depends on the
       measurement window★. Looking at 50 days and saying "this person never touches blade" is wrong —
       it has to be "in the last 50 days".
    """
    seen: collections.Counter = collections.Counter()
    # ⛔⛔ ★Carry where it was counted★ (2026-08-27) — without this, `FAIL` was counted ★106 times in
    #    Bash commands★ while the rule attached to ★Edit/Write★ (`_tools_for` judged only by shape:
    #    "starts with a capital, so it is a filename"). When the place that produced the evidence and
    #    the place the rule attaches to differ, that rule ★never fires where its evidence came from★
    #    and fires somewhere else instead (in file *contents*). Shape is a guess; this is an observation.
    origin: dict = {}
    tools: collections.Counter = collections.Counter()
    per_host: collections.Counter = collections.Counter()
    tmin = tmax = ""
    # ⛔ ★Every host this machine has, not just the one that happens to sort first★ (§transcript_pairs)
    pairs = transcript_pairs(pattern)
    cutoff = ""
    if since_days > 0:
        cutoff = time.strftime("%Y-%m-%d",
                               time.localtime(time.time() - since_days * 86400))
    n_calls = 0
    for host, f in pairs:
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                m = re.search(r'"timestamp":"(\d{4}-\d\d-\d\d)', line)
                if m:
                    d = m.group(1)
                    if cutoff and d < cutoff:
                        continue
                    tmin = d if not tmin or d < tmin else tmin
                    tmax = d if not tmax or d > tmax else tmax
                for name, kind, text in host.tool_calls(line):
                    n_calls += 1
                    tools[name] += 1
                    per_host[host.name] += 1
                    if kind == "shell":
                        for sig in command_ngrams(strip_heredocs(text)):
                            seen[sig] += 1
                            origin.setdefault(sig, {"bash": 0, "path": 0})["bash"] += 1
                    elif kind == "path":
                        for sig in path_signals(text):
                            seen[sig] += 1
                            origin.setdefault(sig, {"bash": 0, "path": 0})["path"] += 1
    scope = {"files": len(pairs), "calls": n_calls, "from": tmin, "to": tmax,
             "tools": dict(tools.most_common(12)),
             # ★which host each call came from★ — a host at 0 is a reader that cannot see it,
             #   and that must never be reported as "this person does not work there".
             "hosts": dict(per_host)}
    return seen, scope, origin


# ── ③ Cross-reference → candidates ──────────────────────────────────────────
MIN_USES = int(os.environ.get("BRAIN_RULE_MIN_USES", "3") or 3)
# ★A signal more frequent than this is "broad"★ — it becomes background, not a rule. Not dropped, pushed back.
WIDE_RATE = float(os.environ.get("BRAIN_RULE_WIDE_RATE", "5") or 5)
# Memories attached per signal for judging — ⛔ never exceed 5 (the judge wobbles, measured 2026-08-25)
PER_SIGNAL = int(os.environ.get("BRAIN_RULE_PER_SIGNAL", "5") or 5)
# ★Stop after this many consecutive failures★ — the value that separates an exhausted quota from a transient error.
# At 1 a network hiccup wastes a day; too high and it takes hundreds of 429s.
FAIL_STREAK = int(os.environ.get("BRAIN_RULE_FAIL_STREAK", "3") or 3)


def candidates(mem_dir: str = "", pattern: str = "",
               since_days: int = 0) -> Tuple[List[dict], dict]:
    """(signal × memory) candidates. ★Never cut by frequency★ — it is used for ordering only.

    Measurement rejected frequency as a discriminator (§module docstring), so frequency is used for
    exactly two things: ① is this an action actually performed (0 means no rule is needed),
    and ② which ones to send to the judge first.
    """
    sigs = signals_from_memories(mem_dir)
    seen, scope, origin = usage_from_transcripts(pattern, since_days)
    known = {m for r in all_rules() for m in r["match"]}
    rows: List[dict] = []
    for s, mems in sigs.items():
        uses = seen.get(s, 0)
        if uses < MIN_USES:
            continue
        picked = sorted(mems.items(), key=lambda kv: -kv[1])[:PER_SIGNAL]
        # ⛔ ★A signal an active rule already catches is not a candidate★ — matching is by substring,
        #    so with a `git fetch` rule, `git fetch origin` already fires. Checking only exact matches
        #    nearly registered `mongosh mongodb` on top of the `mongosh` rule (2026-08-26).
        covered = any(k in s for k in known)
        rows.append({"signal": s, "uses": uses, "n_memories": len(mems),
                     "covered": covered,
                     "origin": origin.get(s) or {"bash": 0, "path": 0},
                     "memories": [n for n, _ in picked],
                     # ★How prominent it is in the memory★ — used for ★ordering★, never for judging.
                     # A one-off command is passed over once in any memory, while `terraform` appears
                     # many times as one memory's subject. ⚠️ It is biased towards long documents —
                     # which is why it is never used to judge (statistics were already rejected three times).
                     "tf": max((c for _, c in picked), default=0),
                     "known": s in known})
    # Measure ★breadth★ — the judge sees only meaning and never how broad a signal is.
    # guard's design rationale is already this axis: "Bash is 65.7% of everything, but within it
    # git push is 3.55%. Look at the command too, and firing becomes rare."
    calls = max(1, scope.get("calls") or 1)
    for r in rows:
        r["rate"] = 100.0 * r["uses"] / calls
        # A signal that ★contains★ an existing rule makes that rule meaningless (`git` ⊃ `git push`)
        r["broader_than"] = sorted(k for k in known if k != r["signal"]
                                   and r["signal"] in k)
    # ★Ordering is not by frequency★ — the top would be all broad signals (`git` 15% · `grep` 28%)
    # and the judging budget would go to rubbish first. guard's design rationale becomes the ordering:
    # ★a rule fires rarely★. Ask about the narrow ones first and push the broad ones back (never drop
    # them — something as broad as `grep` may still have real guidance attached, and that is a human's call).
    # ⚠️ Ordering by tf (prominence in memories) was tried and was ★worse★ (2026-08-26): the top became
    #    field names and collection names from inside backticks. That was the third attempt to decide
    #    even the ordering statistically, and the third rejection. Keep the order simple and
    #    ★judge everything, but cache it★ (asking once is enough).
    rows.sort(key=lambda r: (r["rate"] >= WIDE_RATE, bool(r["broader_than"]),
                             -r["uses"]))
    return rows, scope


# ── ④ The judge — ask about meaning ─────────────────────────────────────────
_PROMPT = """You are ★a behaviour-layer judge★. A developer is about to perform the action below.

Action: %s

Score each candidate memory 0–10 on ★whether it is guidance they must know right before that action★.

- 10   : a prohibition, ordering or precondition that causes an accident if unknown (e.g. "get approval before pushing")
- 6–9  : a caution that applies directly to that action
- 3–5  : near the same topic, but not needed right before that action
- 0–2  : the word was ★merely used as a tool★, or it is unrelated

⛔ The most common mistake: treating a memory that ★mentions the command as a means★ as guidance.
   "grepping showed that X" is not guidance about grep → 0–2.
⛔ Do not award points because "the word appears". There is exactly one question:
   ★must this person read this sentence at the moment they are about to perform that action?★

Output only a JSON array: [{"i": 0, "s": 7}, {"i": 1, "s": 2}, ...]

Candidates:
%s"""

# Threshold for adopting a rule — ⛔ a default, not a baked-in value. It is re-measured against a control.
MIN_SCORE = float(os.environ.get("BRAIN_RULE_MIN_SCORE", "7") or 7)


def _memory_card(name: str) -> dict:
    """One memory as shown to the judge — a one-line description plus a body excerpt."""
    base = store.memory_dir()
    path = os.path.join(base, name + ".md") if base else ""
    desc, body = "", ""
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
        m = re.search(r"^description:\s*(.+)$", txt, re.M)
        desc = m.group(1).strip().strip('"') if m else ""
        body = re.sub(r"^---.*?---\s*", "", txt, flags=re.S)[:420]
    except OSError:
        pass
    return {"name": name, "description": desc, "excerpt": body}


def _memory_is_local_only() -> bool:
    """Has the memory source declared that it ★must not be sent out★ (`"embed": false`)?

    ⛔ On 2026-08-26 this check was missing. The judge is a remote API and this module reads 420
       characters of memory body ★straight from the file★ and sends it — bypassing the `no_embed`
       that the vector indexing path (§vectors.stale_docs) respects. If turning a source off stops
       semantic search but not rule discovery, that setting was not honoured.
    """
    from . import vectors
    return "memory" in vectors.no_embed_sources()


def judge(cand: dict, model: str = "", db=None) -> Optional[List[float]]:
    """Score the memories attached to one signal. ⛔ Failure is None — distinct from a score of 0."""
    from . import rerank
    if _memory_is_local_only():
        return None                    # ★do not send it out★ — unjudged, not zero
    cards = [_memory_card(n) for n in cand["memories"]]
    if not cards:
        return []
    # ★The reserve★ — this is a batch (cron) job. The last part of the daily quota is left for
    # ★interactive★ recall, where a human is waiting. Unjudged candidates carry over to tomorrow (the cache makes that cheap).
    return rerank.score("`%s`" % cand["signal"], cards, model=model, prompt=_PROMPT,
                        reserve=rerank.RESERVE, db=db)


def _ensure(db: sqlite3.Connection) -> None:
    with db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS rule_judged("
            " signal TEXT NOT NULL, memory TEXT NOT NULL,"
            " score REAL NOT NULL, at TEXT NOT NULL,"
            " PRIMARY KEY(signal, memory))")


def discover(limit: int = 40, model: str = "", db: Optional[sqlite3.Connection] = None,
             include_known: bool = False, since_days: int = 0) -> dict:
    """Judge candidates and produce rule suggestions. ★Incremental★ — already-judged pairs come from the cache.

    ⛔ What was skipped because of a limit is ★never hidden★ (it is reported as `skipped`).
       Not saying what was cut reads as "everything was looked at".
    """
    from brain import rerank                          # for the budget report (§rerank.budget)
    own = db is None
    db = db or store.connect()
    _ensure(db)
    local_only = _memory_is_local_only()
    try:
        rows, scope = candidates(since_days=since_days)
        pool = [r for r in rows
                if include_known or not (r["known"] or r.get("covered"))]
        cached = {(r["signal"], r["memory"]): r["score"]
                  for r in db.execute("SELECT signal, memory, score FROM rule_judged")}
        proposals, judged_now, failed = [], 0, 0
        streak = 0                      # ★consecutive★ failures — the signal that the quota has run out
        now = time.strftime("%Y-%m-%d")
        for r in pool:
            need = [m for m in r["memories"] if (r["signal"], m) not in cached]
            if need:
                # ⛔ ★Stop gracefully when the limit is hit★ — before this fix it kept calling up to
                #    the limit even after a 429 (400 wasted calls). Put on a schedule, that repeats
                #    every day, and the log shows only "400 failures" with no visible cause.
                if streak >= FAIL_STREAK or judged_now >= limit:
                    continue
                scores = judge({"signal": r["signal"], "memories": need},
                               model=model, db=db)
                judged_now += 1
                if scores is None:
                    failed += 1
                    streak += 1
                    continue
                streak = 0
                with db:
                    for m, s in zip(need, scores):
                        db.execute("INSERT OR REPLACE INTO rule_judged"
                                   "(signal, memory, score, at) VALUES(?,?,?,?)",
                                   (r["signal"], m, float(s), now))
                        cached[(r["signal"], m)] = float(s)
            keep = [(m, cached[(r["signal"], m)]) for m in r["memories"]
                    if (r["signal"], m) in cached and cached[(r["signal"], m)] >= MIN_SCORE]
            if not keep:
                continue
            keep.sort(key=lambda kv: -kv[1])
            proposals.append({
                "signal": r["signal"], "uses": r["uses"], "known": r["known"],
                "rate": r.get("rate", 0.0), "broader_than": r.get("broader_than", []),
                "memories": [m for m, _ in keep],
                "scores": [s for _, s in keep],
                "top": keep[0][1],
            })
        # ★Suggestions absorb each other★ — matching is by substring, so if `git fetch` is a rule,
        # `git fetch origin` is ★a duplicate that always fires alongside it★. Keep only the shorter one.
        # ⛔ But ★a broad signal must not absorb★ — if `git` (15.3%) swallows `git fetch`, a rule that
        #    should fire rarely becomes background (that is guard's design rationale).
        proposals.sort(key=lambda p: len(p["signal"]))
        kept: List[dict] = []
        for p in proposals:
            wide = p.get("rate", 0.0) >= WIDE_RATE
            eater = next((k for k in kept
                          if k["signal"] in p["signal"]
                          and k.get("rate", 0.0) < WIDE_RATE), None)
            if eater and not wide:
                p["absorbed_by"] = eater["signal"]
            kept.append(p)
        proposals = [p for p in kept if not p.get("absorbed_by")]
        absorbed = [p for p in kept if p.get("absorbed_by")]
        proposals.sort(key=lambda p: (p.get("rate", 0.0) >= WIDE_RATE,
                                      bool(p.get("broader_than")),
                                      -p["top"], -p["uses"]))
        skipped = sum(1 for r in pool
                      if any((r["signal"], m) not in cached for m in r["memories"]))
        # ⛔ ★Say why it stopped★ — leaving only "N failures" means the log shows no cause.
        #    A quota (done for today), the reserve (interactive share) and the network all need different fixes.
        return {"scope": scope, "candidates": len(pool), "judged": judged_now,
                "failed": failed, "skipped": skipped, "proposals": proposals,
                "local_only": local_only, "absorbed": len(absorbed),
                "stopped_early": streak >= FAIL_STREAK,
                "budget": rerank.budget(db),
                "stop_reason": rerank.last_failure()}
    finally:
        if own:
            db.close()


# ── ⑤ Rules nobody uses ─────────────────────────────────────────────────────
def stale(since_days: int = 0) -> dict:
    """★Rules that will never fire★ — this person has never performed that action.

    As important as discovery. In the 2026-08-26 measurement, 5 of the 12 rules' match strings landed here.
    ⛔ Never delete them — show them to a human and let the human decide. There is a past outside the
       measurement window, and they may return to that repository next month.
    """
    seen, scope, _origin = usage_from_transcripts(since_days=since_days)
    out = []
    for r in all_rules():
        cold = []
        for m in r["match"]:
            n = seen.get(m, 0) or sum(v for k, v in seen.items() if m in k)
            if n == 0:
                cold.append(m)
        if cold:
            out.append({"id": r["id"], "cold": cold,
                        "all_cold": len(cold) == len(r["match"])})
    return {"scope": scope, "rules": out}


# ── ⑥ Storing learned rules ─────────────────────────────────────────────────
def rules_path() -> str:
    return os.path.join(store.brain_home(), "rules.json")


# ★Host tool names★ old rules carried → canonical action names
_LEGACY_TOOLS = {"Bash": "run_shell", "Edit": "edit_file", "MultiEdit": "edit_file",
                 "Write": "write_file", "NotebookEdit": "edit_notebook", "Read": "read_file"}


def _migrate_tools(d: dict) -> bool:
    """★Migrate the tool names of already-learned rules to canonical names.★ True if anything changed.

    ⛔ Why this is needed (2026-09-02): renaming to canonical names was a change to ★the code★. But
    rules already learned live ★on disk as data★, holding `tools: ["Bash"]`. Left alone they keep
    firing on one host and ★go quietly silent on another★ — and because nobody re-reads learned rules,
    nobody notices they died.

    ⚠️ An unknown name is ★left as it is★ — a human may have written it, and being caught unmigrated
       is better than being deleted (§guard._tool_matches accepts literal names too).
    """
    changed = False
    for r in d.get("learned", ()):
        tools = r.get("tools") or []
        new_tools = [_LEGACY_TOOLS.get(t, t) for t in tools]
        new_tools = list(dict.fromkeys(new_tools))
        if new_tools != tools:
            r["tools"] = new_tools
            changed = True
    return changed


def load() -> dict:
    try:
        with open(rules_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {"version": 1, "learned": [], "disabled": []}
    d.setdefault("learned", [])
    d.setdefault("disabled", [])
    d.setdefault("dismissed", [])          # signals a human turned off — auto-approval never revives them
    if _migrate_tools(d):
        try:
            save(d)                        # ★migrate once and keep it★ — so it is not recomputed every time
        except OSError:
            pass                           # even if it cannot be written, this run uses the migrated values
    return d


def save(d: dict) -> str:
    os.makedirs(store.brain_home(), exist_ok=True)
    # ⛔ ★Never write over a file that could not be read★ (2026-10-06). `load()` answers an unreadable
    #    file with an empty set, so the next approval would replace every rule this person has with one.
    #    Since brain ships no rules of its own, that file is the whole behaviour layer — keep it aside.
    try:
        with open(rules_path(), encoding="utf-8") as fh:
            json.load(fh)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        try:
            os.replace(rules_path(), "%s.unreadable-%s" % (rules_path(), time.strftime("%Y%m%d-%H%M%S")))
        except OSError:
            pass
    tmp = rules_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, rules_path())
    return rules_path()


def _host_tool_sets():
    """The ★shell tool★ and ★edit tool★ names for the current host. ⛔ The adapter is canonical."""
    try:
        from brain import hosts
        h = hosts.active()
        shell = set(h.tool_names("run_shell"))
        edit = {n for c in ("edit_file", "write_file", "edit_notebook")
                for n in h.tool_names(c)}
        if shell or edit:
            return shell, edit
    except Exception:                                    # noqa: BLE001
        pass
    return {"Bash"}, {"Edit", "Write", "NotebookEdit"}


_SHELL_TOOLS, _EDIT_TOOLS = _host_tool_sets()


def _tools_for(signal: str, origin: Optional[dict] = None) -> List[str]:
    """Decide which tool a signal's shape attaches to — extensions and filenames edit, the rest shell.

    ⛔ ★Look at whitespace first★ — `git show origin/main` contains a slash but is ★a command★, not a
       path. Splitting on the slash alone nearly attached this rule to the edit tool, where it would
       never have fired (2026-08-26, caught just before the first approval). A silent failure — the rule
       registers, appears in the list, and nothing happens for that action.
    """
    # ⛔ ★Observation beats shape★ (2026-08-27) — the rules below are all ★guesses★ from a signal's
    #    shape. Knowing where it was actually counted removes the reason to guess.
    #    Where it really went wrong: `FAIL` was counted 106 times in Bash commands but attached to
    #    Edit/Write for starting with a capital, so it fired on ★every edit whose file content held FAIL★.
    # ⛔ ★Write canonical action names★ (2026-09-02) — writing `Bash` here makes the rule ★never fire★
    #    on another host (tool names differ). Learned rules are not re-read by humans, so a name baked
    #    in here stays quietly dead while remaining in the list.
    if origin:
        got = []
        if origin.get("bash"):
            got.append("run_shell")
        if origin.get("path"):
            got += ["edit_file", "write_file", "edit_notebook"]
        if got:
            return got
    if " " in signal.strip():                            # imperative
        return ["run_shell"]
    if signal.startswith(".") or signal.endswith("/") or "/" in signal \
            or re.match(r"^[A-Z]", signal):
        return ["edit_file", "write_file", "edit_notebook"]
    return ["run_shell"]


def _origin_of(signal: str, proposals: Optional[List[dict]]) -> Optional[dict]:
    """★The observed place★ the suggestion list carried. None if absent, and it falls back to guessing by shape."""
    for p in (proposals or ()):
        if p.get("signal") == signal:
            return p.get("origin")
    return None


def approve(signal: str, proposals: Optional[List[dict]] = None,
            why: str = "", tools: Optional[Sequence[str]] = None) -> dict:
    """Register one suggestion as a rule. ⛔ A human calls this — it never turns on automatically."""
    props = proposals if proposals is not None else discover()["proposals"]
    hit = next((p for p in props if p["signal"] == signal), None)
    if not hit:
        raise KeyError("not in the suggestions: %s" % signal)
    d = load()
    d["learned"] = [r for r in d["learned"] if r["id"] != "auto-" + signal]
    d["learned"].append({
        "id": "auto-" + signal,
        "why": why or "learned from use — there is guidance attached to this action",
        "tools": list(tools) if tools else _tools_for(signal, _origin_of(signal, proposals)),
        "match": [signal],
        "memories": hit["memories"],
        "once": "session",
        "evidence": {"uses": hit["uses"], "top_score": hit["top"],
                     "at": time.strftime("%Y-%m-%d")},
        "enabled": True,
    })
    save(d)
    return d


# ★The auto-approval threshold★ — higher than the judging threshold (7). Turning something on
# without a human means only certainty gets through. The measured distribution is bimodal
AUTO_MIN_SCORE = float(os.environ.get("BRAIN_RULE_AUTO_MIN", "10") or 10)
# Cap per run — dozens turned on in one day buries that day's session in guidance
AUTO_MAX_PER_RUN = int(os.environ.get("BRAIN_RULE_AUTO_MAX", "5") or 5)
# ★To be turned on automatically it must be an action performed often enough★ — the judging floor
# (MIN_USES=3) is the bar for "worth considering as a candidate", and the bar for enabling without
# a human has to be far higher. Without this, on 2026-08-26 a leftover variable name with ★5 uses★ was auto-enabled.
AUTO_MIN_USES = int(os.environ.get("BRAIN_RULE_AUTO_MIN_USES", "50") or 50)
# ⛔ ★Measured on one judge★ (2026-10-06) — the bimodal 0–10 distribution `MIN_SCORE` and `AUTO_MIN_SCORE`
#    sit on was Gemini's (2026-08-26). Another judge's 10 is not that 10, so ★auto-approval stands down★
#    on it until a human names the bar (`BRAIN_RULE_AUTO_MIN`). Proposals still reach a human either way —
#    only the step with no human in it waits.
AUTO_SCALE_PROVIDERS = ("gemini",)


def auto_scale_ok() -> bool:
    if os.environ.get("BRAIN_RULE_AUTO_MIN"):
        return True
    from brain import engines
    return engines.choice("judge")["provider"] in AUTO_SCALE_PROVIDERS


def _path_key(sig: str) -> str:
    """Fold path signals so that ★pointing at the same place gives the same value★.

    ⛔ Two spellings of one directory escape substring absorption while meaning the same thing.
       Enable both and editing one file fires two rules (that actually happened on 2026-08-26).
       They are compared by folding to the last path segment.
    """
    t = (sig or "").strip()
    if "/" not in t:
        return t
    return t.rstrip("/").split("/")[-1]


# ── ⑥ ★Measure the firing rate exactly the way firing happens★ ─────────────
SESSION_BUDGET = int(os.environ.get("BRAIN_RULE_SESSION_BUDGET", "12") or 12)
MIN_SESSION_CALLS = 20


def measure_fire(cands: "Sequence[dict]", pattern: str = "") -> dict:
    """Measure ★how often a candidate will actually surface★, ★the same way guard does★.

    ⛔⛔ Without this the gate was spinning uselessly (measured 2026-08-28).
       The gate measured firing rates from what `usage_from_transcripts` counted (Bash = command
       n-grams · Edit = path fragments), while ★guard scans the whole `tool_input` by substring★.
       So the counted value and real firing diverged by up to ★21×★ (`const r`: 53 ↔ 1,120).
       It is ★the same disease★ as yesterday's fix (`_tools_for` guessing by shape → using observation):
       ★when the place you measure differs from the place it fires, the gate blocks nothing.★

    One more thing is measured alongside — ★how many surface per session★. Each rule may clear the
    threshold while the total does not, and then the whole layer becomes background. Measured: as
    rules went 12 → 33, the per-session median doubled exactly, ★8 → 16★. The design had written
    its own warning about this — *"guidance pouring out every session makes people ignore the layer"*.

    ⛔⛔ ★The tool name is matched by guard's own function, in the log's host★ (2026-10-07). Since rules
       moved to canonical names (`run_shell`, 2026-09-02) this compared them with the log's raw names
       (`Bash`) and ★counted 0 calls for every signal and every rule★: real rates 0, per-session median
       0. Both gates stood open for a month — `all`, `lines`, `await`, `next`, `queue` were auto-approved
       on the counted rate alone, and the daily report said all 43 rules were rare. The disease this
       docstring opens with, back under a new name: the place it measured had drifted from the place it fires.
    """
    from . import guard
    live = [r for r in all_rules() if r.get("enabled", True)]
    probes = [{"id": "?" + c["signal"], "match": [c["signal"]],
               "tools": _tools_for(c["signal"], c.get("origin"))} for c in cands]
    fired = collections.Counter()
    calls = collections.Counter()
    per_session: List[int] = []
    for host, f in transcript_pairs(pattern):
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        seen_live: set = set()
        n_calls = 0
        with fh:
            for line in fh:
                for name, blob in host.hook_calls(line):
                    n_calls += 1
                    for pr in probes:
                        if guard._tool_matches(name, pr["tools"], host):
                            calls[pr["id"]] += 1
                            if any(m in blob for m in pr["match"]):
                                fired[pr["id"]] += 1
                    for r in live:                        # firings per session in the current layer
                        if r["id"] in seen_live:
                            continue
                        if guard._tool_matches(name, r["tools"], host) and any(m in blob for m in r["match"]):
                            seen_live.add(r["id"])
        if n_calls >= MIN_SESSION_CALLS:
            per_session.append(len(seen_live))
    per_session.sort()
    n = len(per_session)
    out = {c["signal"]: {
        "fires": fired["?" + c["signal"]], "calls": calls["?" + c["signal"]],
        "rate": 100.0 * fired["?" + c["signal"]] / max(1, calls["?" + c["signal"]])}
        for c in cands}
    return {"signals": out, "sessions": n,
            "median": per_session[n // 2] if n else 0,
            "p90": per_session[int(n * 0.9)] if n else 0,
            "active": len(live)}


def auto_approve(proposals: Optional[List[dict]] = None,
                 min_score: float = 0.0, limit: int = 0) -> dict:
    """★Turn rules on without a human★ — user instruction (2026-08-26): *"automatically, from use"*.

    ⛔ The point where automation becomes dangerous is ★noise★. Guidance pouring out every session
       makes a person ignore the entire layer, and then they do not read it at the moment it matters.
       So there are four gates:
      ① score `AUTO_MIN_SCORE` (default 10) — higher than the judging threshold (7). Only certainty.
      ② ★must not be broad★ — over `WIDE_RATE` (5%) it is background, not a rule.
      ③ must not contain an existing rule — if `git` swallows `git push`, a rare rule becomes common.
      ④ ★never revive what a human turned off★ (`dismissed`).

    ⚠️ And at most `AUTO_MAX_PER_RUN` (default 5) per run — turning on dozens in a day buries that
       day in guidance.
    """
    if not auto_scale_ok():
        from brain import engines
        return {"added": [], "min_score": min_score or AUTO_MIN_SCORE,
                "cap": limit or AUTO_MAX_PER_RUN, "fire": {},
                "skipped": [(i18n.t("rule.skip.overall_label"),
                             i18n.t("rule.skip.unmeasured_judge", judge=engines.judge_id()))]}
    props = proposals if proposals is not None else discover()["proposals"]
    d = load()
    # ★If the layer as a whole is already full, add nothing★ (2026-08-28) — each rule may clear the
    # threshold while the total does not, and then ★it is not read at the moment it matters★ either.
    # ⛔ ★Measure every proposal that could be approved★, not the first 12 (2026-10-07) — past the 12th
    #    the gate below fell back to the ★counted★ rate, the one this measurement exists to replace
    #    (up to 21× low). `scout` and `all` sat past it and went through on 54 and 59 counted uses.
    lo_score = min_score or AUTO_MIN_SCORE
    fm = measure_fire([p for p in props if p["top"] >= lo_score and p["uses"] >= AUTO_MIN_USES]
                      or props[:12])
    if fm["median"] >= SESSION_BUDGET:
        return {"added": [], "min_score": min_score or AUTO_MIN_SCORE,
                "cap": limit or AUTO_MAX_PER_RUN, "fire": fm,
                "skipped": [(i18n.t("rule.skip.overall_label"),
                             i18n.t("rule.skip.overall_budget",
                                    median=fm["median"], budget=SESSION_BUDGET))]}
    dismissed = set(d.get("dismissed", ()))
    have = {m for r in all_rules() for m in r["match"]}
    have_mems = {m for r in all_rules() for m in r.get("memories", ())}
    have_keys = {_path_key(m) for r in all_rules() for m in r["match"] if "/" in m}
    lo = min_score or AUTO_MIN_SCORE
    cap = limit or AUTO_MAX_PER_RUN
    added, skipped = [], []
    for p in props:
        if len(added) >= cap:
            break
        sig = p["signal"]
        if sig in dismissed:
            skipped.append((sig, i18n.t("rule.skip.dismissed")))
            continue
        if any(k in sig for k in have):
            skipped.append((sig, i18n.t("rule.skip.caught")))
            continue
        if p["top"] < lo:
            continue                                  # everything after this is lower, by the ordering
        if p["uses"] < AUTO_MIN_USES:
            skipped.append((sig, i18n.t("rule.skip.rare", uses=p["uses"])))
            continue
        # ⛔ ★No reason to enable it if it brings no new memories★ — two spellings of the same path
        #    escape substring absorption while attaching ★the same memories★. You end up with two
        #    rules surfacing the same sentence twice (that happened on 2026-08-26).
        fresh = [m for m in p["memories"] if m not in have_mems]
        if not fresh:
            skipped.append((sig, i18n.t("rule.skip.no_new_memories")))
            continue
        if _path_key(sig) in have_keys:
            skipped.append((sig, i18n.t("rule.skip.same_path", path=_path_key(sig))))
            continue
        # ⛔ ★Gate on the real firing rate, not the counted value★ — they diverged by up to 21× (§measure_fire).
        # ⛔⛔ And use ★the worse of the two★. If the measurement cannot see that signal (outside the
        #    window, or a synthetic candidate) the real rate comes out 0, and then ★not having seen it
        #    opens the gate★ — a regression check actually caught that. ★No value is not unlimited.★
        real = max((fm["signals"].get(sig) or {}).get("rate", 0.0),
                   p.get("rate", 0.0))
        if real >= WIDE_RATE:
            skipped.append((sig, i18n.t("rule.skip.too_broad",
                                        real="%.1f" % real, counted="%.1f" % p.get("rate", 0.0))))
            continue
        if p.get("broader_than"):
            skipped.append((sig, i18n.t("rule.skip.contains_existing")))
            continue
        p["fire_rate"] = real
        approve(sig, proposals=props,
                why="learned from use — there is guidance attached to this action (auto-approved)")
        have.add(sig)
        have_mems.update(p["memories"])
        if "/" in sig:
            have_keys.add(_path_key(sig))
        added.append({"signal": sig, "top": p["top"], "uses": p["uses"],
                      "memories": p["memories"]})
    if added:
        sync_shell()
    return {"added": added, "skipped": skipped[:10],
            "min_score": lo, "cap": cap}


def disable(rule_id: str) -> dict:
    """Turn a built-in or learned rule off. ⛔ Never delete from the code — it must be reversible.

    ⛔ ★Auto-approval must never re-enable what a human turned off★ — otherwise the daily job revives
       it the next day while the human believes it is off. The moment automation overwrites a human's
       judgement, the whole layer loses trust. So the signal is written into `dismissed`.
    """
    d = load()
    for r in d["learned"]:
        if r["id"] == rule_id:
            r["enabled"] = False
            for m in r.get("match", ()):
                if m not in d.setdefault("dismissed", []):
                    d["dismissed"].append(m)
            save(d)
            return d
    if rule_id not in d["disabled"]:
        d["disabled"].append(rule_id)
    save(d)
    return d


def all_rules() -> List[dict]:
    """★The rules the behaviour layer actually uses★ = built-in (none ship; minus disabled) + learned (enabled only).

    ⛔ This is canonical — any place still reading `guard.RULES` directly loses the learned ones quietly.
    """
    from . import guard
    d = load()
    off = set(d.get("disabled", ()))
    out = [r for r in guard.RULES if r["id"] not in off]
    out.extend(r for r in d.get("learned", ()) if r.get("enabled", True))
    return out


# ── ⑦ The shell pre-filter — ★generated, not a copy★ ───────────────────────
def signals_file() -> str:
    return os.path.join(store.brain_home(), "guard-signals")


def sync_shell() -> dict:
    """Write the signal list that `bin/brain-guard` reads.

    ⛔ Why a file: a `case` list inside a shell script is ★a copy maintained by hand★ and diverges as
       rules grow. But having code rewrite the shell script means ★one syntax error blocks every Bash
       call★ (which actually happened on 2026-08-19 — including the tool needed to fix the hook).
       ⇒ Keep the shell code fixed and export ★only the list, as data★. If the file is absent the
         shell attaches nothing — there are no built-in rules, so no file means no rules.
    """
    sigs = sorted({m for r in all_rules() for m in r["match"]})
    os.makedirs(store.brain_home(), exist_ok=True)
    tmp = signals_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sigs) + "\n")
    os.replace(tmp, signals_file())
    return {"path": signals_file(), "n": len(sigs)}


def measure_active(pattern: str = "") -> dict:
    """★How often an active rule actually surfaces within its own tool★ — the table guard's session budget reads.

    ⛔ guard is a hook that runs on every tool call, so ★it cannot measure this in place★ (that means
       scanning 172 transcripts). So a scheduled job measures it once a day into `guard-rates.json`,
       and guard only reads that file. ★The expensive layer teaching the cheap one★, as elsewhere here.

    ⛔ The measuring must be ★identical to guard's★ — substring, within that rule's tools.
       (A gate once spun uselessly because the counting and the firing differed · 2026-08-28)
    """
    from . import guard
    rules = [r for r in all_rules() if r.get("enabled", True)]
    fire = collections.Counter()
    calls = collections.Counter()
    for host, f in transcript_pairs(pattern):
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                for name, blob in host.hook_calls(line):
                    for r in rules:
                        # ⛔ guard's matcher, in the log's host — a raw `name in r["tools"]` counted 0 for
                        #    every canonical rule and wrote "rare" for all of them (§measure_fire, 2026-10-07)
                        if guard._tool_matches(name, r["tools"], host):
                            calls[r["id"]] += 1
                            if any(m in blob for m in r["match"]):
                                fire[r["id"]] += 1
    rates = {}
    for r in rules:
        den = calls[r["id"]] or 1
        rates[r["id"]] = round(100.0 * fire[r["id"]] / den, 3)
    path = os.path.join(store.brain_home(), "guard-rates.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rates, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return {"path": path, "rules": len(rates), "rates": rates}
