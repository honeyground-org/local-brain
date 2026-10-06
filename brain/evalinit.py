"""Eval-set bootstrap — ★someone else's gold belongs to someone else★.

Why this tool exists
---------------------
`verify_recall`·`verify_short` judge by "this memory should surface for this question" (gold).
That gold is **that person's own memory names**. Run the set its author built on a different person and
everything fails, and there's no way to tell whether the engine is at fault or the corpus just differs.

And ⛔ **eval queries must never be invented.** This project has actually been burned here: measuring
with 10 invented questions raised a false alarm that "the hook never fires once," and re-measuring with
real records put the median at 11.3. The set has to come **from your own conversation history**.

So this tool builds ★only a draft★
------------------------------------
The gold verdict is a human's job — only that person knows what actually answers the question. Here:

  ① pull **short one-line queries** from your own transcript (real turns)
  ② run `recall` on each query and attach **3 gold candidates**
  ③ write a JSON draft with `gold` left empty — a person fills in one of the candidates and it becomes a set

The reason for ②: this project's own original set was built the same way — *"how gold was decided — not
a hunch. What `recall` actually returned once given expansion words."* A person never imagines it from scratch.

⛔ A built set is personal data (a user's own turns land in it as-is) — `tests/eval/` is gitignored.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import List, Optional

from . import calibrate, i18n, search, store

def eval_dir() -> str:
    """Where this person's own evaluation set lives.

    ⛔ ★Someone who installed with pip has no repository★ — the same trap `store.default_config_path`
    already documents. The old value was a plain repo-relative constant, so in an installed copy it
    resolved to ★`site-packages/tests/eval`★: outside the package, polluting the top level of
    site-packages, wiped by an upgrade, and often not even writable. `brain eval-init` would have
    tried to ★write★ a person's labelled sample there.

    Order (first wins) — the same rule as `default_config_path()`:
        1. `BRAIN_EVAL_DIR`               what a human decided wins
        2. ★next to the repository★       does not break a development checkout
        3. `brain_home()/eval`            where an installed copy keeps it

    Found 2026-09-10 by `tests/verify_first_day.py`: on a clean machine the guard reported
    `labelled` because this constant reached back into ★the author's own repository★.
    """
    env = os.environ.get("BRAIN_EVAL_DIR")
    if env:
        return env
    beside = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "tests", "eval")
    if os.path.isdir(beside):
        return beside
    from brain import store as _s
    return os.path.join(_s.brain_home(), "eval")


# ⛔ Kept as a module attribute because checks reassign it to hide the sample
# (`verify_guard_reaches_others`, `verify_proxy_sample`). Reading it through `_dir()` below means a
# reassignment still wins, while an unset value resolves by the rule above.
EVAL_DIR = ""


def _dir() -> str:
    return EVAL_DIR or eval_dir()

# ★Short one-line queries★ — the shape the hook actually meets. "Short" is ★this person's★ shortest 30% of
# one-line prompts, not a character count: 12–40 characters was the author's (40 sat at the 29th percentile,
# 2026-10-06), and a character count is a language's density — the same question is longer in English.
SHORT_SHARE = 0.30


def short_band(prompts: List[str]) -> tuple:
    """(min, max) characters of a short query, read off these prompts. The floor is the hook's own."""
    from brain import hook                            # ⛔ local import — a module-level one would cycle
    lo = hook.MIN_PROMPT_CHARS
    one = sorted(len(q) for q in prompts if "\n" not in q and len(q) >= lo)
    if not one:
        return lo, lo - 1                             # an empty band — nothing is invented
    return lo, one[min(len(one) - 1, int(len(one) * SHORT_SHARE))]


# ── ★the gold verdict lives in exactly one place★ ──────────────────────────────────────────
#
# ⛔ Measured 2026-08-31: code reading gold was scattered across ★8+ spots★, and all of it
#    assumed "gold is a single string" (`gold in name`). So widening a label to ★several★
#    made `brain score` die with a TypeError — dying instead of going quietly wrong was
#    a mercy. The same rule living in several places means only one of them ever gets fixed.
#
# ★one query can have several right answers★ — for "show me the terraform plan/apply command,"
# a document naming the command and a lesson naming its trap are both correct. Counting only one
# counts a right answer as wrong, and that wrong answer flows into the threshold and calibration.


def golds(gold) -> list:
    """gold, ★always as a list★ — the same shape whether it was one string or several."""
    if gold is None:
        return []
    if isinstance(gold, (list, tuple)):
        return [g for g in gold if g]
    return [gold] if gold else []


def gold_hit(gold, name: str) -> bool:
    """Is this name one of the gold answers. ⛔ Substring matching is ★kept exactly as the original rule★
    (some sets write a long name like `docs:domains/...` abbreviated to a shorter one)."""
    n = name or ""
    return any(g in n for g in golds(gold))


def load_A(path: str = "") -> list:
    """Eval sample A (real sentences needing memory) → [(query, raw gold)]. Rows with no gold are dropped."""
    import json as _json
    p = path or os.path.join(_dir(), "short.json")
    try:
        with open(p, encoding="utf-8") as fh:
            ev = _json.load(fh)
    except (OSError, ValueError):
        return []
    return [(r["q"], r["gold"]) for r in ev.get("A_memory_needed", [])
            if isinstance(r, dict) and r.get("gold")]


def load_C(path: str = "") -> List[str]:
    """Eval sample C (★real sentences needing no memory★ = control group) → [query].

    ⛔ ★loading lives in this one place only.★ Burned by this on 2026-09-01: unlike `A_memory_needed`,
    this sample is ★an array of strings★ (not dicts), and reading it with `r.get("q")` without knowing
    that ★loaded 0 entries, and the table confidently reported '0.0% false-fire rate'★. When the control
    group is empty, every axis measuring noise quietly scores a perfect result — ★a probe that can never fail★.

    So it accepts both shapes (string · dict), and ★an empty file returns an empty list★. The caller
    must treat an empty result as ★not measured, never a score★ (the scorecard does exactly that).
    """
    import json as _json
    p = path or os.path.join(_dir(), "short.json")
    try:
        with open(p, encoding="utf-8") as fh:
            ev = _json.load(fh)
    except (OSError, ValueError):
        return []
    out = [(r if isinstance(r, str) else (r.get("q") if isinstance(r, dict) else None))
           for r in (ev.get("C_no_memory_needed") or [])]
    return [q for q in out if q]


def harvest(limit_scan: int = 4000) -> List[str]:
    """Pulls short one-line queries from your own conversation history. ⛔ Never invents any."""
    prompts = [q.strip() for q in calibrate._real_prompts(limit=limit_scan)]
    lo, hi = short_band(prompts)
    out, seen = [], set()
    for q in prompts:
        if "\n" in q or not (lo <= len(q) <= hi):
            continue
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def draft(db: sqlite3.Connection, queries: List[str], k: int = 3) -> dict:
    """Attaches gold ★candidates★ to each query. gold is left empty — a human picks."""
    thr = calibrate.threshold(db)
    cases, quiet = [], []
    for q in queries:
        res = search.recall(db, q, k=k, log=False)
        if not res:
            quiet.append(q)
            continue
        top = res[0]["score"]
        row = {
            "q": q,
            "gold": "",                      # ← a human fills in one of the candidates here
            "terms": [],                     # ← write the topic's synonyms here (raises accuracy a lot)
            "_candidates": [{"name": r["name"], "score": round(r["score"], 2),
                             "description": (r["description"] or "")[:90]}
                            for r in res],
        }
        (cases if top >= thr else quiet).append(row if top >= thr else q)
    return {
        "_": [
            i18n.t("evalinit.file.note1"),
            i18n.t("evalinit.file.note2"),
            i18n.t("evalinit.file.note3"),
            i18n.t("evalinit.file.note4"),
            i18n.t("evalinit.file.note5"),
            i18n.t("evalinit.file.note6"),
        ],
        "meta": {"threshold_at_draft": thr,
                 "harvested": len(queries),
                 "with_candidates": len(cases),
                 "silent": len(quiet)},
        "A_memory_needed": cases,
        "C_no_memory_needed": quiet,
    }


def write(db: sqlite3.Connection, out_path: str = "", limit: int = 60) -> dict:
    qs = harvest()[:limit]
    d = draft(db, qs)
    path = out_path or os.path.join(_dir(), "short.draft.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    d["_path"] = path
    return d


def render(d: dict) -> str:
    m = d["meta"]
    out = [i18n.t("evalinit.built", path=d.get("_path", "?")),
           "",
           i18n.t("evalinit.harvested", n=m["harvested"]),
           i18n.t("evalinit.with_candidates", n=m["with_candidates"],
                 thr="%.2f" % m["threshold_at_draft"]),
           i18n.t("evalinit.silent", n=m["silent"]),
           "",
           i18n.t("evalinit.todo_title"),
           i18n.t("evalinit.todo1"),
           i18n.t("evalinit.todo2"),
           i18n.t("evalinit.todo3"),
           i18n.t("evalinit.todo4"),
           "",
           i18n.t("evalinit.never_invent")]
    return "\n".join(out)
