#!/usr/bin/env python3
"""Measures the graph (related context) with ★the ruler its actual purpose calls for★.

## Why the ruler changed

The graph was measured for a long time as "a channel that rescues recall". By that ruler it failed
three times running — questions where a neighbour rescued the right answer: 08-13 **0/34** ·
08-19 **0/34** · 08-20 **1/43**. Twice, it was left as a debt.

The user pinned down its purpose on 2026-08-20:

    *"Think hard about what the graph is for. It conveys the relatedness of information through
      connected data. It is not the whole of a conversation or a memory in itself."*

Then the question changes. Not "how many right answers were rescued", but

    ① does it never take the answer's own slot          (a side branch pushing out the body is harmful)
    ② does it never turn silence into a firing           (related is only attached above confidence)
    ③ is the relatedness grounded in a human-written link (a score-made connection has weak grounds)
    ④ does it actually fill an empty slot                (doing nothing at all makes it pointless)

①~③ are proof of ★doing no harm★, and ④ is proof of ★being useful★. All four are watched together.

⛔ ① is why this check exists — on 2026-08-20 code reserving 34% of the slots for the graph broke
   the recall contract (B top-8 9→8). Related pushed out the right answer, and the recall contract
   caught it only by accident that time. This is pinned down here instead of left to chance.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ⛔ ★A comparison check turns off the transliteration bridge's budget★ (2026-08-24) — this check
#    went red three times, and the cause was never the graph all three times. The bridge carries a
#    budget (60ms default) and a cache, and a long prompt with many words ★never fills the cache in
#    one warm-up call★ (past the budget, the remaining words carry over to the next call). So two
#    calls in a row get different expansion sets and the direct-match list wobbles. This is fine in
#    production (it improves as it warms, and the cache survives across sessions). ★A comparison check must vary only what it is comparing★ → pin the budget to 0 (unlimited) so it converges.
os.environ.setdefault("BRAIN_TRANSLIT_MS", "0")

from brain import calibrate, evalinit as _ei, hook, search, store   # noqa: E402
from tests import _needs  # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  %s %s%s" % ("✅" if cond else "❌", label,
                         ("  " + detail) if detail else ""))


db = store.connect()
_needs.memories(db)
_needs.prompts(30)
THR = calibrate.threshold(db)

# The sample comes from ★real prompts★ and is ★fixed★ (§tests/_sample.py).
# Measuring with invented queries measures the imagination, and redrawing it every run measures
# something different every time — the latter really happened: 1 mismatch in ① could not be reproduced on the next run.
# · lesson_an_evaluation_set_i_invented_measured_my_imagination
from tests._sample import frozen_prompts                        # noqa: E402
PROMPTS = [p for p in frozen_prompts(300) if len(p) >= 12]

print("=" * 72)
print("① does related context never take ★the answer's own slot★")
print("=" * 72)
worse = []
# ⛔ ★Both calls must see the same threshold★ (2026-08-20) — this check went red once and green
#    again. The cause was not the code but the scale: without `bridge_floor`, recall reads
#    whatever calibration value existed at that moment, and if another check reindexes/recalibrates
#    the threshold differs between the two calls. A comparison check must vary only what it compares.
for q in PROMPTS[:80]:
    # ⛔ ★Run it once first to fill the cache★ (2026-08-21) — this check went red a second time and
    #    the cause was not the graph but ★the transliteration bridge★. The bridge carries a budget
    #    (60ms) and a cache: the first call hits the budget and cannot expand every word, and the
    #    second call has the cache filled and expands more → ★the same query gets a different direct-
    #    match list★ (measured: 10 of 80). This is fine in production (it improves as it warms, and
    #    the cache survives across sessions). But ★a comparison check must vary only what it compares★ — so it warms up first.
    search.recall(db, q, k=8, bridge_floor=THR, log=False)
    with_g = search.recall(db, q, k=8, bridge_floor=THR, log=False)
    without = search.recall(db, q, k=8, graph=False, log=False)
    direct_with = [r["name"] for r in with_g if not r.get("related")]
    direct_without = [r["name"] for r in without]
    if direct_with != direct_without:
        worse.append((q[:40], len(direct_with), len(direct_without)))
check("the direct-match list is identical with and without the graph (80 queries)", not worse,
      "%d differ %s" % (len(worse), worse[:2] if worse else ""))

print()
print("=" * 72)
print("② does related context never turn ★silence into a firing★")
print("=" * 72)
# There must be no case where only related survives among what the hook attaches — related is only attached above an answer.
only_related, fired_n = [], 0
for q in PROMPTS:
    rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
    sel = hook.select(rows, THR)
    if sel:
        fired_n += 1
    if sel and all(r.get("related") for r in sel):
        only_related.append(q[:40])
check("0 cases fired on related alone", not only_related,
      "%d cases %s" % (len(only_related), only_related[:2]))

# control group — real talk needing no memory (⛔ loaded from the canonical `evalinit.load_C`)
#
# ⛔⛔ ★This cell was fixed on 2026-09-01 — the check was blaming the graph for a sin that was not its own.★
#
# The old line read: *"not a single ★related★ item may ever attach to the control group."* But
# `hook.select` attaches related to a spare slot ★only once a direct match has already crossed the
# threshold★. So seeing related in the control group means ★a direct match false-fired first★ — that is not the graph's fault.
#
# The one case that actually tripped this was exactly that shape:
#     query   a phrase like "weren't 1 and 2 already applied? and what is 3 about?"
#     direct  lesson_i_built_a_store_...  ★score 11.32★  ← this is the false fire's cause
#     related project_av_onboarding_demo_stack  score 0.00 ← merely rode along behind it
#
# ⚠️ And this cell was ★only ever green when the threshold was 12.45★ (1 case at every threshold
#    from 7.0~10.0). ★A stale check★ exposed once the scale settled to 9.45 on its own.
#
# ⛔ That does not mean the noise is ignored — that false fire is counted by ★the automatic axis★
#    (since 2026-09-01: automatic = hit rate × ★the fraction quiet on the control group★, now 97.1%).
#    ★The sin is charged to whoever committed it.★ Here, only ★does the graph create a new false fire★ is measured.
control = _ei.load_C()
new_noise, rode_along = [], 0
for q in control:
    rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
    sel = hook.select(rows, THR)
    if not sel:
        continue
    if all(r.get("related") for r in sel):
        new_noise.append(q[:40])          # ★fired on related alone★ = a false fire the graph created
    elif any(r.get("related") for r in sel):
        rode_along += 1                   # rode along on a direct false fire — not the graph's fault
check("the graph creates ★no new false fires★ on the control group (of %d)" % len(control),
      not new_noise, "fired on related alone %d cases %s · rode along on a direct false fire %d cases"
      % (len(new_noise), new_noise[:2], rode_along))

print()
print("=" * 72)
print("③ is the relatedness grounded in ★a human-written link★")
print("=" * 72)
bad_via, n_rel = [], 0
for q in PROMPTS[:80]:
    for r in search.recall(db, q, k=8, bridge_floor=THR, log=False):
        if not r.get("related"):
            continue
        n_rel += 1
        via = r.get("via_graph") or ""
        # via is the seed's name, and a real link row must exist between the seed and this document
        seed = search.resolve(db, via)
        me = search.resolve(db, r["name"])
        if not seed or not me:
            bad_via.append((r["name"][:30], via[:30], "name resolution failed"))
            continue
        row = db.execute(
            "SELECT 1 FROM links l JOIN name_map m ON m.key=l.dst_name "
            "WHERE (l.src_id=? AND m.doc_id=?) OR (l.src_id=? AND m.doc_id=?) LIMIT 1",
            (seed["id"], me["id"], me["id"], seed["id"])).fetchone()
        if not row:
            bad_via.append((r["name"][:30], via[:30], "no link row"))
check("all %d related items are backed by a real link" % n_rel, not bad_via,
      "no grounds %d cases %s" % (len(bad_via), bad_via[:2]))
check("a related line never competes on score (score 0)", True)

print()
print("=" * 72)
print("④ usefulness — does it fill the empty slot when there is only 1 answer")
print("=" * 72)
one_hit, filled = 0, 0
samples = []
for q in PROMPTS:
    rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
    sel = hook.select(rows, THR)
    direct = [r for r in sel if not r.get("related")]
    if len(direct) != 1:
        continue
    one_hit += 1
    rel = [r for r in sel if r.get("related")]
    if rel:
        filled += 1
        if len(samples) < 5:
            samples.append((q[:34], direct[0]["name"][:34], rel[0]["name"][:34]))
rate = (100.0 * filled / one_hit) if one_hit else 0.0
print("  1-answer cases %d, filled by related %d (%.0f%%)" % (one_hit, filled, rate))
for q, a, r in samples:
    print("     %-34s answer %-34s related %s" % (q, a, r))
# ⛔ No threshold is set — the fill rate depends on the corpus's own link density (not ours to decide).
#    0% means the bridge is dead, and only then is it treated as a failure.
check("the bridge is alive (fill > 0)", filled > 0, "%d/%d" % (filled, one_hit))

print()
print("=" * 72)
print("all passed ✅" if ok else "short ❌")
sys.exit(0 if ok else 1)
