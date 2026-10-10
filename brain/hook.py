"""Automatic recall hook — ★it surfaces even when you did not think to ask★.

## Why this is needed

`recall` finds things well (8 of 9 in the top 3). The problem is **when you do not think to call it**.
Memory is usually not found by "let me go look" — it surfaces mid-task as *"oh, that thing from before"*.
The hook fills that spot, not a human.

## ★The design axis is precision, not recall★

The hook runs on **every prompt**. So attaching a wrong memory often is not help but noise, and noise
causes distortion (a judgement gets made on the wrong grounds). So there is one discipline:

    ★say nothing at all when unsure★

Below the threshold it **quietly ends empty-handed**. It chooses to miss — a miss is fixed by calling
`recall` myself, but a wrong attachment gets mixed into the judgement.

## The threshold was set by measurement (2026-08-10 · against 9,022 real prompts)

| What | Top score |
|---|---|
| 300 real questions (random sample) | p10 5.2 · **p50 10.7** · p90 23.5 |
| 30 sentences unrelated to our domain | max **7.41** |
| 20 real talk needing no memory ("continue on"·"go commit it") | max **6.89** |

→ threshold `10.0`: both noise groups are clearly below it, and about half of real questions are above it.

★This is what an earlier memory system could not do★ — its recall score is RRF, so it **always comes out to
1/60, 1/61…, unrelated to the query** (its §4.3 states plainly, "do not place an absolute threshold
on this value"). So they had to route background-recall judgement through "can a reason be given" instead.
Our score is BM25-based, so it is **the strength of the signal itself**, and that is why a threshold holds.

## What it does not do

- ★It does not build expansions★ — that is the job of whoever knows synonyms (an agent, embeddings later).
  A hook that expands loosely breaks precision first (measured: automatic query expansion, top-3 2→1).
- ★It never exceeds 2 items★ — attach more and the model spends its reading on that instead of the user's words.
- ★It must not be slow★ — it is latency on every prompt. Budget 200ms; past it, it simply gives up.
"""
from __future__ import annotations

import json
import os
import sys
import time

from brain import i18n

# ---- knobs (every one has its measured grounds in the comment above) ----
# ⛔ ★The threshold is not nailed in here★ — the score's scale differs per corpus (IDF depends on N).
# A good value of 10.0 on 867 documents made an 8-document corpus fall permanently silent (clean-room measured).
# So what is fixed is not a value but **the way of measuring**, and the threshold is calibrated at index time
# (§calibrate). With no ruler at all, nothing fires (§calibrate.NO_RULER).
MAX_ITEMS = 2           # the maximum attached
MIN_PROMPT_CHARS = 12   # shorter than this has nothing worth recalling
MAX_PROMPT_CHARS = 600  # a long prompt is truncated to its head — so a pasted log does not swallow the query
TIME_BUDGET_SEC = 0.20  # the lexical stage's budget — past it, it gives up (session latency matters more than memory)
EXCERPT_CHARS = 200
# ★The wall★ — the limit the installer writes into the host's config for this hook (one source:
# `install.py` reads it from here). Past it the host cancels us, and everything done so far is lost.
# ⛔ It was hit ★123 times★ (transcripts 08-26 → 09-29, `hook_cancelled · timedOut · 5000ms`) while the
#    code guarded only the lexical stage. So the watchdog below ends the hook on its own terms first,
#    and hands over whatever was already ready (§brain/deadline.py carries the full account).
HOOK_TIMEOUT_SEC = 5
# The watchdog fires this long before the wall. Measured 2026-09-29: spawn → `main()` costs 31ms
# (median, max 35ms of 20 runs), and writing the answer is a single write. 0.5s is ten times that.
WATCHDOG_MARGIN_SEC = 0.5



def _read_prompt() -> str:
    try:
        raw = sys.stdin.read()
    except Exception:                                    # noqa: BLE001
        return ""
    if not raw.strip():
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw.strip()
    if not isinstance(data, dict):
        return ""
    # Field names can differ between versions — known names are checked in order.
    for key in ("prompt", "user_prompt", "userPrompt", "message", "text", "display"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _emit(text: str) -> None:
    """Attach it as context, in the calling host's shape (§hosts.Host.hook_output)."""
    from brain import hosts
    sys.stdout.write(hosts.active().hook_output("user_prompt_submit", text))


def _format(rows) -> str:
    # ★User-visible text lives in the i18n catalog★ (2026-09-07) — this is the one string
    # in the whole brain a user reads on every single prompt, so it must speak the language
    # they configured (`brain/locales/*.json`), not whatever language the code happened to
    # be written in. ⛔ Numbers are pre-formatted in Python before going into `t()` — a format
    # spec like `{cosine:.3f}` inside a catalog value would be invisible to the placeholder-
    # mismatch check (§i18n._PLACEHOLDER only matches a bare `{name}`), so it never gets caught
    # if a translation loses it.
    lines = [i18n.t("hook.header")]
    for r in rows:
        # ★Mark what was found by meaning as such★ — no word may have overlapped at all,
        # so the reader must know "why did this come" (otherwise it looks arbitrary).
        if r.get("via_vector"):
            lines.append(i18n.t("hook.vector_found", name=r["name"], source=r["source"],
                                cosine="%.3f" % r.get("cosine", 0.0),
                                desc=r["description"] or r["title"],
                                excerpt=r["excerpt"][:EXCERPT_CHARS]))
            continue
        # ★Related context is shown differently from an answer★ — two short lines (no excerpt).
        # If a consumer reads this as an answer, the graph swallows the conversation (user's point 2026-08-20).
        if r.get("related"):
            lines.append(i18n.t("hook.related_entry", name=r["name"], via=r["via_graph"],
                                desc=r["description"] or r["title"]))
            continue
        if r.get("evidence_age_days", -1) >= 0:
            age = i18n.t("hook.evidence_age", date=r["evidence_date"],
                        days=r["evidence_age_days"])
        else:
            age = i18n.t("hook.updated_age", days=r["age_days"])
        warn = i18n.t("hook.stale_warn") if r["stale"] else ""
        # ★One line for the latest change★ — the evidence date says "when", the commit title says
        # "what changed". Purpose ③ (history) gets filled in a little even from the hook. Just one —
        # this runs on every prompt, so a longer line pays that cost every single time.
        hist = (r.get("history") or [])
        hline = i18n.t("hook.history_line", line=hist[0][:76]) if hist else ""
        lines.append(i18n.t("hook.entry", name=r["name"], source=r["source"],
                            desc=r["description"] or r["title"],
                            age=age, warn=warn, hist=hline,
                            excerpt=r["excerpt"][:EXCERPT_CHARS]))
    return "\n".join(lines)


# ★The single canonical source of the firing rule is this one function★ — verification calls it too (tests/verify_short.py).
#
# ⛔ On 2026-08-19 three judges were "transcribing" this rule and quietly drifting (measured by the top score).
#    A rule written in two places only gets fixed in one — so it lives here, in one place.
# The floor is kept as ★its share of the calibrated threshold★ — measured as 7.5 when the threshold was 9.46
# (2026-08-19). ⛔ An absolute 7.5 is one corpus's scale: the score moves with N (IDF), and by 2026-10-06 the
# same corpus calibrated to 9.76, so 7.5 had quietly become a looser rule. As a share it means what it was
# measured to mean on any corpus (on the author's labelled set: hits 15/57 · false fires 3/34 either way).
REL_FLOOR_SHARE = 7.5 / 9.46   # the minimum score the relative rule may look at, as a share of the threshold
REL_RATIO = 0.80   # what fraction of the query's own maximum it covered


def _named(prompt: str, db=None):
    """Does the prompt hold a ★declared trigger phrase★ (score-independent · §brain/triggers.py)."""
    try:
        from brain import store, triggers
        return triggers.match(db or store.connect(), prompt)
    except Exception:                                    # noqa: BLE001
        return []


def select(rows, min_score: float):
    """Choose what to attach. The absolute threshold comes first, and a short query gets one more look via the relative rule.

    ★Why the relative rule is needed★ (measured 2026-08-19) — of 12 silent cases, 3 had ★the right answer
    ranked 1st, with a score below the threshold★ (a phrase like "continue X exploration" scored 7.69 against
    a threshold of 9.46). When the query is short, the maximum it could ever earn is itself low, so no
    document can reach the absolute threshold — lowering the threshold cannot fix it (that lets in the noise of long queries too).

    ⛔ Three things were measured first and two were rejected:
        1st-2nd gap    silent-A median 0.54 ↔ C median 0.53   ❌ they overlap (rejected once already on 2026-08-13)
        1st/4th ratio  silent-A 1.30 ↔ C 1.23                 ❌ nearly identical
        ★ratio★ (score÷query weight) alone, C scores higher (0.38 ↔ 0.27) — so it is used ★together
        with an absolute floor★. Grid measurement (rescued/new false fires): floor7.0·ratio0.85 → 2/1 · ★floor7.5·ratio0.80 → 2/0★
        · floor7.5·ratio0.75 → 2/1 · ratio alone 0.8 → 3/3.
    ⚠️ The threshold was chosen on a 34-item set, so it was confirmed against a wider control group — over
       300 real prompts, only ★1★ newly fires (a two-word topic phrase → the project note named after that topic,
       ratio 1.16), and the firing rate holds at 54%. Not a noise faucet.
    """
    direct = [r for r in rows if not r.get("related")]
    keep = [r for r in direct if r["score"] >= min_score][:MAX_ITEMS]
    if keep:
        # ★Only one 'related context' item goes in a spare slot★ (2026-08-20 · the user nailed down its use)
        # The graph is not a channel for finding answers but a place that tells you ★what is linked to what
        # is already matched★. So ①only once a direct match already cleared the threshold ②only in a spare slot ③only the neighbour of the top match.
        # ⛔ Because of this rule, the bridge ★can never turn silence into a fire★ — new false fires are structurally 0.
        if len(keep) < MAX_ITEMS:
            rel = next((r for r in rows if r.get("related")
                        and r.get("via_graph") == keep[0]["name"]), None)
            if rel:
                keep.append(rel)
        return keep
    # No neighbour is attached to the relative rule (a short query) — a side branch on a low-confidence item is noise
    floor = min_score * REL_FLOOR_SHARE
    return [r for r in direct
            if r["score"] >= floor and r.get("ratio", 0.0) >= REL_RATIO][:1]


def fallback(db, prompt: str, strict: bool = False, cache: bool = True):
    """★Look again by meaning, but only when words fall silent★ (2026-08-21).

    ⛔ ★The check calls this function too★ (2026-08-25). Before, this logic lived only inside `main()` and
       `verify_short` measured only as far as the lexical path (`select`). The result was an asymmetry —
       ★the gain was measured with the second stage on, and the cost was measured with it off★ —
       `verify_vs_grep`'s hit line turned the second stage on via `BRAIN_SCORECARD_FULL=1` and reported 42%,
       while `verify_short`'s false-fire line reported 0/45 with no second stage. The actually deployed
       pipeline's false fires were ★15/45★.

    Why only when silent — the measured gap sits exactly there (98% when given synonyms, 37% asked nothing).
    Where words already carried the answer, precision is already high, so there is no reason to pay for this.

    ⛔ ★What it costs now★ (re-measured 2026-09-29 — the "508ms" once written here was the embedding
       round trip alone, from before the judge was wired in). A fresh prompt, one process each, as the
       host runs it: embedding 0.55~0.68s (skipped when the query is cached) · the cosine scan over
       every chunk ~0.13s · ★the judge 0.4~2.0s — the bulk★ · whole hook 2.7~2.8s end to end.
       Its tail is not bounded here — the watchdog in `main()` is what bounds it.
    """
    try:
        from brain import search, vectors
        # ★The gate is a ratio★, and a vector must exist in ★the space in use right now★
        # (a different model, dimension or prefix is a different space — mixing them makes cosine meaningless).
        has_vec = db.execute(
            "SELECT 1 FROM vectors WHERE model=? AND dim=? LIMIT 1",
            (vectors.model_tag(), vectors.DIM)).fetchone()
        # ★Two stages★ — cosine draws candidates, and ★the judge does the deciding★ (2026-08-25).
        # Measurement forced this order: a cosine-ratio gate alone rescued 0 of 8 silent cases,
        # and adding the reranker put the right answer 1st in 3 of them, top-1 correct overall going 3→15.
        # The reason is scale — cosine measures "is this the same neighbourhood", the reranker measures "is this the answer".
        from brain import rerank
        if has_vec and rerank.min_score(db) <= 10.0:
            return search.judged(db, prompt[:MAX_PROMPT_CHARS], strict=strict,
                                 cache=cache)[:1]
        if has_vec and vectors.min_ratio(db) < vectors.MIN_RATIO_FALLBACK:
            return search.semantic(db, prompt[:MAX_PROMPT_CHARS], k=1)[:1]
    except Exception as exc:                             # noqa: BLE001
        # ⛔ ★A swallowing catch kills the signal★ (measured 2026-08-25) — this `except` had just
        #    swallowed a NameError raised while writing this function itself, making it look like "the judge fell silent",
        #    and the check called that ★0 false fires★. `strict` opens that spot up.
        from brain import search as _s
        if isinstance(exc, _s.JudgeUnavailable):
            raise                                        # ★the check must be told★
        return []                                        # in production: a key, limit or network failure stays quiet
    return []


def main() -> int:
    from brain import hosts
    hosts.utf8_stdio()                                   # ⛔ before the payload is read (§hosts.utf8_stdio)
    t0 = time.time()
    # ★which host launched us★ — written by the installer as `--host <name>`. Without it, a machine
    # with two hosts installed falls back to "the first one found" and reads the other one's world.
    from brain import hosts
    hosts.pin_from_argv()
    prompt = _read_prompt()
    # A host command (`/clear`, `!ls` …) is not the person's own question — ★the host's syntax★ (§hosts)
    if not prompt or prompt[:1] in hosts.active().command_prefixes():
        return 0
    if os.environ.get("BRAIN_HOOK_DISABLED"):
        return 0
    # ⛔ ★A declared trigger comes before the length gate★ (measured 2026-08-19) — `MIN_PROMPT_CHARS`
    #    is a score-world heuristic for "short talk has nothing worth recalling". A declared phrase is a
    #    lookup, not a score, so its length is irrelevant: an 11-character phrase and a 9-character phrase
    #    were both caught by that gate and fell silent.
    if len(prompt) < MIN_PROMPT_CHARS and not _named(prompt):
        return 0

    # ★The wall is kept on total elapsed time, whatever is blocking★ (2026-09-29 · §brain/deadline.py).
    #   The host cancels this hook at `HOOK_TIMEOUT_SEC`, and did so ★123 times★ while the only guard
    #   here was the lexical stage's. What blocked each time cannot be told from the transcripts —
    #   a replay of the same prompts finished inside 2.7s — so the guard does not bet on one cause:
    #   a SQLite lock (15s busy wait), an embedding call (60s ×2), the judge (25s) and DNS (no bound
    #   at all) all end at the same wall. At the wall it hands over what was already ready, and
    #   ★writes down which stage it was in★ — so the next such burst says what it was.
    from brain import deadline
    state = {"t0": t0, "stage": "index", "path": "lexical", "ready": [],
             "fb": None, "fb_hit": False, "hit": False, "cut": False}

    def expire() -> None:
        state["cut"] = True
        if state["ready"]:
            _emit(_format(state["ready"]))
            state["hit"] = True
        _record(state)

    wd = deadline.watchdog(
        HOOK_TIMEOUT_SEC - WATCHDOG_MARGIN_SEC - (time.time() - t0), expire)
    try:
        keep = _answer(prompt, state)
    finally:
        mine = wd.claim()                                # ★one answer only★ — see Watchdog.claim
        wd.cancel()
    if not mine:
        return 0                                         # the watchdog is answering, then exits
    if keep:
        _emit(_format(keep))
        state["hit"] = True
    _record(state)
    return 0


def _answer(prompt: str, state: dict):
    """Everything between the prompt and the answer. `state` says where it is, for the watchdog."""
    from brain import search, store
    db = store.connect()

    # ★A newly created or **edited** memory is indexed right there★
    # Without it the hook cannot recall a memory just saved (measured: 0 recalls right after creating a file directly).
    #
    # ⛔ Until 2026-08-12 this check **missed an edit entirely** — because it looked only at the
    #   directory mtime. So a user saying "this memory is wrong, fix it" and getting it fixed still had
    #   **the old content recalled all session long.** Now every file's own mtime is checked (16~20ms).
    #
    # ⛔ `recalibrate=False` — calibration runs 138 recalls and takes 3.3 seconds.
    #   Left on, one index run becomes 3.6 seconds, the hook gives up on its time budget, and
    #   **the memory just fixed is not caught in that very prompt.** The scale is measured at session start.
    try:
        if store.needs_reindex(db):
            store.reindex(db, recalibrate=False)
    except Exception:                                    # noqa: BLE001
        pass                                             # a failed index must not block recall

    if time.time() - state["t0"] > TIME_BUDGET_SEC * 3:
        return []

    # ⛔ `log=False` — the hook's own recall is not added to the usage record.
    # Counting something that runs automatically on every prompt as "used" would make weight and
    # self-improvement suggestions reflect the hook's own firing rate rather than **what a human actually looked for**.
    state["stage"] = "lexical"
    from brain import calibrate
    min_score = calibrate.threshold(db)
    rows = search.recall(db, prompt[:MAX_PROMPT_CHARS], k=MAX_ITEMS + 2, log=False)
    keep = select(rows, min_score)

    # ★A declared trigger phrase is attached regardless of score★ (user instruction 2026-08-19:
    # "do not gate it by a score threshold — there needs to be a clearer query and a way to confirm it.")
    # Calling a thread by name is a lookup, not a search — §brain/triggers.py carries the grounds and the check.
    # ★Looked up before the fallback★ (2026-09-29) — it is a lookup and costs milliseconds, so it is
    # what the watchdog can still hand over if the slow stage below never returns.
    state["stage"] = "triggers"
    named = _named(prompt[:MAX_PROMPT_CHARS], db)

    # ★Look again by meaning, but only when words fall silent★ (2026-08-21)
    #
    # Why this spot — the measured gap sits exactly here: called with synonyms, 42/43 (98%); asked
    # nothing at all, 16/43 (37%). The gap's identity is "the question's words are absent from the memory".
    # ⛔ It is the one stage that goes out over the network (2.7~2.8s fresh, measured 2026-09-29 — see
    #    `fallback`). Where words already carried the answer, precision is already high, so it is not called.
    # ⛔ Not knowing the threshold, nothing is attached (§vectors.min_cos fallback 1.01).
    if not keep:
        if named:
            state["ready"] = _with_triggers(named, [], db)
        state["stage"], state["path"] = "fallback", "fallback"
        t = time.time()
        keep = fallback(db, prompt)
        state["fb"] = round(time.time() - t, 3)
        state["fb_hit"] = bool(keep)

    state["stage"] = "merge"
    if named:
        keep = _with_triggers(named, keep, db)
    return keep


def _with_triggers(named, keep, db):
    """Declared triggers first, then what the search found — deduplicated, at most `MAX_ITEMS`."""
    try:
        from brain import search
        have = {r["name"] for r in keep}
        forced = []
        for _ph, did, nm in named[:MAX_ITEMS]:
            if nm in have:
                continue
            row = search._hydrate(db, did)
            if row is not None:
                forced.append(search._present(row, 0.0, [], "declared trigger", [], db))
        return (forced + keep)[:MAX_ITEMS]
    except Exception:                                    # noqa: BLE001
        return keep                                      # a failed lookup does not block recall


# ★Where the time went, one line per run★ (2026-09-29) — the record that was missing when 123
# cancellations had to be explained after the fact. ⛔ Never the prompt: only timings, the stage and
# whether anything was attached. Bounded — past the cap the older half is dropped.
TIMING_FILE = "hook-timing.jsonl"
TIMING_MAX_BYTES = 256 * 1024


def _timing_path() -> str:
    from brain import store
    return os.path.join(store.brain_home(), TIMING_FILE)


def _record(state: dict) -> None:
    try:
        line = json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(state["t0"])),
            "total": round(time.time() - state["t0"], 3),
            "stage": state["stage"], "path": state["path"], "fb": state["fb"],
            "fb_hit": state["fb_hit"], "hit": state["hit"], "cut": state["cut"],
        }) + "\n"
        path = _timing_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        if os.path.getsize(path) > TIMING_MAX_BYTES:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines[len(lines) // 2:])
    except Exception:                                    # noqa: BLE001
        pass                                             # a record must never cost the answer


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                    # noqa: BLE001
        # ★A hook's failure must not block the session★ — it withdraws quietly.
        sys.exit(0)
