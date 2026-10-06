"""A sample that needs ★no labels★ — so the calibration guard is alive on someone else's machine.

## The hole this fills (measured 2026-09-02, closed 2026-09-09)

The guard that stops a bad ruler from being deployed compares candidate thresholds on a fixed
sample of (question → right answer) pairs. That sample is ★one person's own memory names★, it is
personal data, and `.gitignore` rightly keeps it out of the distribution. So on a new machine
`_bench` returns `{}` and ★any ruler at all is deployed, with no warning★. The incident the guard
exists for (9.45 → 13.21, automatic 0.284 → 0.128) happens there with no brake.

`tests/verify_guard_reaches_others.py` made the machine ★say★ it was unprotected. This module makes
it ★protected★ — without asking anyone to label anything.

## Where the pairs come from — and why they are not invented

⛔ This repository has a standing rule: an evaluation query is never invented
(an evaluation set one invents measures one's own imagination). So a pair here is
★cut out of the user's own documents★, never written by us:

    query = a short fragment of a document's body, with the words of that document's own name removed
    gold  = that document

The removal matters: leave the name's words in and the query is a lookup, not a question. This is
the standard inverse-cloze construction, and every word in it was written by the user.

## ⛔ What this sample can and cannot decide — ★measured, not assumed★ (2026-09-09)

Measured against this machine's real labelled sample (`tests/verify_proxy_sample.py` re-measures all
of this on every run, so these numbers cannot quietly go stale):

    inside the envelope it is allowed to judge   decides what gold decides  ★18/18★
    the rises gold actually wants taken          it stands down on          ★7/7★
    the candidate is ★below★ the incumbent       agrees                     ★10/15★
    ★end to end★: the recorded incident (9.45 → 13.21) is ★stopped★, and a proxy with shuffled
    answers stops ★nothing★ — so the protection comes from the sample, not from the shape of a curve

The downward weakness is visible in the numbers: a fragment of a document finds its own document
~90% of the time, so the proxy's hit rate barely falls as the threshold drops, and it sees no cost
in going lower. Its `auto` curve is monotonically decreasing — it has ★no interior optimum★.

⇒ So the guard uses this sample for ★one direction only★: it may veto a candidate that sits ★above★
  the incumbent. A downward candidate is left to the existing unguarded path (deployed, with the
  trace and the status line). ★A judgement we measured to be wrong is not made at all.★
  That direction is also the one the guard exists for — the recorded incident was a threshold
  climbing until the hook went silent.

⛔ ★One direction was not enough★ — see `may_judge` ②. Splitting the upward cases by what gold
  actually says showed the proxy rejecting every rise gold rejects but also ★5 of the 7 it wanted
  accepted★: a one-way ratchet, where the threshold can only ever sink. The second restriction
  (stand down when the incumbent's real firing rate is already above the healthy band) removes
  exactly those 7 and keeps the rest — 18/18 and 7/7 above.

⚠️ Fitted on ★one corpus★ (this machine, 1,400 documents · 713 memories). Whether the agreement
   holds on a different corpus is ★unmeasured★ — the same honesty §0 risk 2 asks for the languages.

## ⛔ The sample is frozen

`tests/_sample.py` records what happens otherwise: a sample redrawn each run made the same code give
a different verdict (68% → 73% → 71% in one day). So it is built once, stored in the DB, and rebuilt
only when the corpus has moved materially or the measuring code's generation changed.
"""
from __future__ import annotations

import os
import random
import re
import sqlite3
from typing import List, Optional, Tuple

# How many pairs to aim for. 30 is the guard's own minimum (§calibrate.guard_status); the target sits
# above it so that documents dropping out of the corpus do not immediately take the guard down.
TARGET_A = int(os.environ.get("BRAIN_PROXY_A", "60") or 60)
MIN_A = 30

# ★Fragment length★ — chosen by measurement (2026-09-09), not taste. Swept 2–7 words against the
# score distribution of this user's ★real prompts★ (a label-free target that exists on any machine):
#   7 words → median 14.17 · 5 → ★11.13★ · 4 → 9.42 · 3 → 7.57   against a real-prompt median of 12.15
# Five is the closest, and every length gave the same verdict agreement, so the closest one wins.
FRAGMENT_WORDS = int(os.environ.get("BRAIN_PROXY_WORDS", "5") or 5)

# A body shorter than this has no fragment worth cutting; a sentence outside this range is either
# a heading (too short to be a question) or a paragraph (too long to be one).
MIN_BODY, MIN_SENT, MAX_SENT = 400, 25, 160

# ★Rebuild when the corpus has moved this much★ — the same shape as the transliteration cache's
# tolerance. Small edits must not redraw the sample (that is the freezing rule), but a corpus that
# has grown by a fifth is a different corpus and the old fragments no longer represent it.
REBUILD_RATIO = 0.20

# Sentence ends in the scripts the tokenizer reads (Latin `. ! ?`, CJK `。！？`, an ellipsis) plus a newline and
# the middle dot many notes use between items. ⛔ It used to be `[.\n·]` — one writer's habit; a Japanese
# note was one long "sentence" and never yielded a fragment.
_SENT_SPLIT = re.compile(r"[.!?\n。！？…·]")


def _ensure(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS proxy_sample("
               "q TEXT PRIMARY KEY, gold TEXT NOT NULL,"
               " built_docs INTEGER NOT NULL DEFAULT 0,"
               " gen INTEGER NOT NULL DEFAULT 0)")


def _fragment(name: str, body: str, rnd: random.Random) -> str:
    """A short fragment of `body` with the words of `name` removed · '' when none is usable."""
    from brain import textindex
    own = {w.lower() for w in textindex.words(name.replace("_", " "))}
    sents = [s.strip() for s in _SENT_SPLIT.split(body or "")
             if MIN_SENT < len(s.strip()) < MAX_SENT]
    rnd.shuffle(sents)
    for s in sents:
        words = [w for w in textindex.words(s) if w.lower() not in own]
        if len(words) >= 4:
            return " ".join(words[:FRAGMENT_WORDS])
    return ""


def build(db: sqlite3.Connection, n: int = 0, seed: int = 7) -> List[Tuple[str, str]]:
    """Draw `n` (query, gold) pairs from the corpus. ★Deterministic★ for a given corpus and seed.

    ⛔ Ordered by `id`, never `RANDOM()` — the ordering has to be reproducible or the "frozen"
    promise is empty. Randomness enters only through `seed`, which is fixed.
    """
    n = n or TARGET_A
    rnd = random.Random(seed)
    rows = db.execute(
        "SELECT id, name, body FROM docs WHERE length(body) > ? ORDER BY id",
        (MIN_BODY,)).fetchall()
    if not rows:
        return []
    order = list(range(len(rows)))
    rnd.shuffle(order)                                   # a fixed shuffle — not the SQL engine's
    out: List[Tuple[str, str]] = []
    seen = set()
    for i in order:
        row = rows[i]
        q = _fragment(row["name"], row["body"], rnd)
        if not q or q in seen:
            continue
        seen.add(q)
        out.append((q, row["name"]))
        if len(out) >= n:
            break
    return out


def positives(db: sqlite3.Connection, rebuild: bool = False) -> List[Tuple[str, str]]:
    """The frozen proxy positives — built once, reused after, rebuilt only when it must be."""
    from brain import calibrate, store
    _ensure(db)
    n_docs = max(1, store.corpus_stats(db)["docs"])
    gen = int(calibrate.CODE_GENERATION)
    rows = db.execute("SELECT q, gold, built_docs, gen FROM proxy_sample").fetchall()
    if rows and not rebuild:
        was, was_gen = int(rows[0]["built_docs"] or 0), int(rows[0]["gen"] or 0)
        moved = abs(n_docs - was) > max(1, was) * REBUILD_RATIO
        if not moved and was_gen == gen and len(rows) >= MIN_A:
            return [(r["q"], r["gold"]) for r in rows]
    pairs = build(db)
    with db:
        db.execute("DELETE FROM proxy_sample")
        db.executemany(
            "INSERT OR REPLACE INTO proxy_sample(q, gold, built_docs, gen) VALUES(?,?,?,?)",
            [(q, g, n_docs, gen) for q, g in pairs])
    return pairs


def controls(db: sqlite3.Connection) -> List[str]:
    """The proxy controls — queries that ★should not★ pull a memory out.

    ⛔ Nothing new is invented here either: these are the same domain-neutral probes and
    common-word queries `measure()` already uses for the noise floor. Building a second set would
    put the same rule in two places, and this repository's own ⛔ rule says one of them then rots.
    """
    from brain import calibrate, store
    n_docs = max(1, store.corpus_stats(db)["docs"])
    out = list(calibrate.neutral_probes(db))
    try:
        out += list(calibrate._common_word_queries(db, n_docs))
    except Exception:                                    # noqa: BLE001
        pass
    return out + _word_salad(db, n_docs)


# A control has to be a query that ★matches documents but is nobody's answer★. The two sets above
# give about 18 of them, under the 20 the guard asks for, so this adds the hardest kind: common
# words of ★this corpus★ thrown together into something that means nothing.
# ⛔ Added on evidence, not to clear a number — measured 2026-09-09, adding 12 and 20 of these left
#    every threshold verdict exactly as it was. They enlarge the control mass without tilting it.
SALAD_WORDS, SALAD_N, SALAD_SEED = 3, 14, 23


def _word_salad(db: sqlite3.Connection, n_docs: int) -> List[str]:
    """Meaningless combinations of this corpus's own common words — the hardest controls."""
    lo, hi = max(3, int(n_docs * 0.02)), max(4, int(n_docs * 0.25))
    pool = [r["term"] for r in db.execute(
        "SELECT term FROM terms WHERE df BETWEEN ? AND ? AND length(term) >= 2 "
        "ORDER BY df DESC LIMIT 120", (lo, hi))]
    if len(pool) < SALAD_WORDS * 2:
        return []
    rnd = random.Random(SALAD_SEED)                      # ★fixed★ — controls are frozen too
    return [" ".join(rnd.sample(pool, SALAD_WORDS)) for _ in range(SALAD_N)]


def sample(db: sqlite3.Connection) -> Tuple[List[Tuple[str, str]], List[str]]:
    """(positives, controls) — the whole proxy sample."""
    return positives(db), controls(db)


def usable(db: sqlite3.Connection) -> bool:
    """Is there enough of a corpus for the proxy to mean anything."""
    try:
        return len(positives(db)) >= MIN_A
    except sqlite3.Error:
        return False


def may_judge(incumbent: Optional[float], candidate: float,
              incumbent_fire: Optional[float] = None) -> bool:
    """★The two restrictions★ — when this sample is allowed to veto at all.

    ① ★Upward only.★ Measured against real gold on this corpus: inside the envelope below, the
       proxy decides exactly what gold decides (18/18); ★below★ the incumbent it agrees 10/15.
       So downward is not judged at all rather than judged badly.

    ② ★And only when the incumbent is not already too noisy.★ The first version stopped at ①, and
       the control group in `tests/verify_proxy_sample.py` caught what that costs: of the upward
       moves gold wanted ★accepted★, the proxy blocked 5 of 7. A guard that blocks every rise while
       waving every fall through is a ★one-way ratchet★ — the threshold sinks into noise and can
       never climb out. That is a different failure from the one this guard was built for, not a
       smaller one.

       What separates the two cases is not the proxy at all, it is a signal this project already
       had: the share of ★real prompts★ that fire at the incumbent (`FIRE_RATE_OK`, 15–75%).
       Measured 2026-09-09 — ★all 7★ of the rises gold wanted accepted start from an incumbent
       ★above★ that band (86.7% and 90.8%): the ruler was already drowning in noise and raising it
       was simply right. Every rise gold wanted rejected that starts from inside the band is caught.
       And the recorded incident (9.45 → 13.21) starts at 65.8%, ★inside the band★ — still caught.

       ⇒ Above the band the proxy stands down: raising is directionally right and it has no
         standing to argue about how far. Inside or below it, the proxy may veto.
       ⛔ Unknown firing rate = stand down. An unverifiable precondition is not a satisfied one.
    """
    from brain import calibrate
    if incumbent is None or candidate <= incumbent + 1e-9:
        return False
    if incumbent_fire is None:
        return False
    return incumbent_fire <= calibrate.FIRE_RATE_OK[1]
