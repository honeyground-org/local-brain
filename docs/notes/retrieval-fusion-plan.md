# Combining the two retrieval signals — plan and frozen experiment (2026-10-13)

brain has two ways to find a memory — words (`search.recall`, BM25 over n-grams) and meaning
(`vectors.search`, cosine over chunks) — and a judge that can re-rank. Measured on 2026-10-13, the two
signals are never combined into one ranking, and the judge is not shown the part of a document that
matched. This note records what was measured, the plan, and the experiment ★as fixed before it runs★, so
the result cannot be read back into its own design.

## What was measured

The author's 66 labelled questions (57 short, 9 long), the question alone (no expansion words), no judge
calls. "Top 3" means the answer is among the first three.

| Method | Answer found |
|---|---|
| words, top 3 | 22 / 66 (33%) |
| meaning, top 3 | 24 / 66 (36%) — the 9 long questions: words 0, meaning 5 |
| words 20 + meaning 20 by reciprocal rank (k = 60), top 3 | 29 / 66 (44%) |
| either one's top 3 (six documents read) | 37 / 66 (56%) |
| anywhere in words 20 ∪ meaning 20 | 59 / 66 (89%) |
| graph neighbours shown with recall | 0 / 66 |
| `grep`, words OR'd, anywhere in what it returns (median 222 files) | 54 / 66 (82%) |
| `grep`'s files ordered by how many question words they contain, top 3 | 12 / 66 (18%) |
| … ordered by the rarity of the words they contain, top 3 | 11 / 66 (17%) |

- The two signals find different answers: words alone 13, meaning alone 15, both 9.
- The answer is among the candidates 89% of the time and in the top three 33–44%: ★choosing★ is where it
  is lost, not finding. Ranking `grep`'s matches does not help (17–18%): its 82% comes from returning
  hundreds of files, not from how it matches.

## Why

- The MCP `recall` tool calls `search.recall` only (`brain/server.py`), although `search.semantic`'s
  docstring says it is appended there. The hook uses meaning only when words fall silent.
- The judge is shown a document's description (160 characters) and the first 420 characters of its body.
  Vector search keeps the best chunk's score but not ★which★ chunk. For 27 of the 54 questions whose
  answer is in the vector top 20, the best-matching chunk is not the first one — the passage that matched
  may never reach the judge.
- Reciprocal rank with k = 60 scores a document at rank 20 in both lists (2/80) above one at rank 1 in a
  single list (1/61), so an answer only one signal finds is pushed down.
- Ties: 18 of 54 judged questions had more than one candidate at the top score, so input order decides.
- Withdrawn: a label-free judge threshold of 8 measured the same day (a two-group mixture with the noise
  share bounded by the lowest ratio of cumulative distributions). That bound does not keep each score
  bin's residual non-negative, and pairs built to have no answer are not guaranteed to have none.

## Plan — one pull request each

1. **Observe.** The hook records what its judge said before the threshold — candidates sent, top two
   scores, ties, threshold, answering model, excerpt policy, cache, time, and why it ended
   (`search.judge_observation`). No extra call, nothing returned changes. Measurements reuse the hook's
   own first stage (`hook.lexical`).
2. **Evidence passage.** Recover each candidate's best chunk from the local vectors and give the judge a
   passage from it instead of the head (`search.judged(excerpt="evidence")`, `vectors.best_chunks`,
   `search.evidence`). An experiment path first; the default changes only on the result. On the author's
   index, judge-free: for 49 of the 54 answers in the vector top 20 the chunk was recovered (the other 5
   were two documents edited since they were embedded — left out, as the rule says), and the question's
   worth in what the judge reads rose from 259.7 to 338.9 (summed IDF; more in 16, less in 4).
3. **Vector results in MCP `recall`, opt-in** (`semantic_k`, default 0; `search.meaning`). A separate list
   after the word results — never a re-rank — with no answer already shown, the same `kinds`/`sources`
   filters, the passage that matched, and "unavailable — reason" when there is no embedding engine. It is a
   larger reading budget, so it is compared at the same budget (author's 66 questions, question alone, no
   judge): words 6 → 25, words 3 + meaning 3 → 37; words 11 → 31, words 8 + meaning 3 → 42.
   **Unasked when the words are unsure** (2026-10-14): with `semantic_k` omitted, 3 are added when the hook's
   own rule (§hook.select, the calibrated threshold) finds nothing to attach in what the words returned —
   no new number. Same 66 questions, asked with their terms as agents ask: words 8 → 46; + meaning 3 always
   → 52 at 11 to read; + meaning 3 only when unsure → the same 52, added on 22 of 66 (9.0 to read). Where
   the words were sure (44) they held the answer 43 times. `semantic_k: 0` turns it off; a failure while
   unasked is recorded, not shown.
   **Still unsure: learned synonyms, then three rankings** (2026-10-14 · `search.fused`): the answer was found and
   lost in the ordering — in the top 50 of words or meaning for 63 of 66, in the top 3 for 35 (question alone).
   With the words unsure, `recall` asks for the person's learned synonyms (`recall(bridge=True)`, although the
   measured decision keeps them out of the hook), and if still unsure merges words, meaning and the whole-body
   passage (the window holding the most of the question, how grep sees a line) by reciprocal rank. Same 66
   questions with their terms: top 3 43 → 48 (paired 95% +0..+10); answers anywhere in what is shown 52 → 52,
   read 9.0 → 8.3. Not in the hook: the order of what it attaches did not move (15 → 13–14 · 18 → 19 on real
   turns), and synonyms deciding whether to attach took prompts needing no memory from 3 to 17 of 34. A graph
   agreement ranking lowered every arm.
4. **Judge the union** of words 20 and meaning 20 (at most 40, one call).
5. **The hook**, only after the criteria below hold.

A judge threshold carries the scale it was measured on (model, prompt). From step 2 on it also carries the
candidate and excerpt policy (`search.judge_policy`); a threshold measured under one policy is not carried
over to another.

## The experiment, fixed before it runs

| Arm | Candidates | What the judge reads per candidate |
|---|---|---|
| A — today | meaning 20 | description + first 420 characters |
| B — evidence | meaning 20 | description + the evidence passage (≤ 420) |
| C — union | words 20 ∪ meaning 20, deduplicated by document id | description + first 420 characters |
| D — both | as C | description + the evidence passage (≤ 420) |

Same judge model, prompt, sanitising and candidate filter (the hook's `always_in_context` exclusion) in
every arm. `MAX_CAND` is raised to 40 in the experiment process only.

**Evidence passage.**
1. A document from the vector list: the chunk with the highest cosine (lowest `chunk_no` on an exact tie).
2. Within that chunk's ★body★ (the title and description prefixed to the first chunk excluded), the
   window of at most 420 characters with the largest sum of IDF over the distinct query words it contains
   (the existing tokenizer and IDF; repeats and overlapping n-grams counted once).
3. A vector chunk containing no query word: the window at the chunk's centre.
4. A document only words found: the same window score over the whole body; earliest window on a tie.
5. A document both found: the vector rule.
6. A stored chunk whose document hash no longer matches the body is not used as evidence.

**Order and ties.** A and B keep the vector order. C and D order the input by min(word rank, vector rank),
then document id (an absent rank counts as infinite). Output: judge score descending, then that order.
Top three is read without a threshold and without `judged()`'s lexicon learning (frozen during the
experiment). Every candidate must get exactly one score; a missing, duplicate or out-of-range score is an
error, not a 0.

**Controls.** Words, meaning and reciprocal rank without a judge; a ★position-randomised★ passage of the
same length from the same document (fixed seed); a fixed subset re-asked with the input order shuffled,
cache off.

**Budget.** About 12 + 66 × 3 ≈ 210 new judge calls (arm A is mostly cached). Spread over Pacific days,
leaving the hook's reserve; read the shared ledger, not the clock. Results from different answering
models are not pooled.

**How it runs.** `tests/measure_retrieval_fusion.py` (arms A · B · C · D and the position-randomised
control R). It freezes a snapshot of the index on its first run and forces the local vector scan, asks in
a seeded shuffle of (question, arm), stops before the hook's reserve or at `--max`, and resumes from the
cache. Without `--run` it spends nothing: it prints the judge-free controls and how many calls remain.
Judge-free, on the same 66 questions (2026-10-13): words 22, meaning 24, reciprocal rank 30, the union in
its input order (min of the two ranks) 31 — so the bar for a judged arm is 31 + 7 = 38.

**Accepted when** (fixed now; acceptance criteria for this experiment, not settings of the product):
- top-3 hits at least 10 points above the best judge-free control, with the paired 95% interval of the
  per-question difference above 0 (29 / 66 reproduced → at least 36 / 66, which alone is not enough);
- the same direction on a proxy sample whose source sentence is removed from the searched copy, and no CI
  regression;
- short, long and expansion-word questions reported separately;
- for the hook: p95 of a cold run within min(0.9 T, T − M), where T is the installation's hook limit and M
  the watchdog margin, fewer than 1% of runs cut, and a one-sided 95% upper bound of at most 5 points on
  the rise in false fires on a corrected no-memory set. If that cannot be shown, only MCP `recall` changes.
