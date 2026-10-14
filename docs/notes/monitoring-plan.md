# Monitoring words and meaning used together — plan (2026-10-14)

brain answers from two ways of finding a memory — words (`search.recall`) and meaning (`vectors.search`,
served by the chosen vector store or the local copy) — with a graph of links beside them and a judge that
can re-rank. Whether using them together actually works has to be watched continuously, on each person's
own data, without a number tuned to one person. This note records the plan (reviewed with a second model on
2026-10-14) and what each step must prove.

## What was seen on the day the plan was made

On the author's machine, 24 hours, 499 hook runs: words answered 62% · ★silent while reindexing 27%★ (the hook
reindexes changed notes first and withdraws past its time budget) · second stage silent 5% · ★cut by the
watchdog 5%★ · attached by the second stage 2%. From one evening on, 15 of 16 second-stage runs were cut: the
embedding took 0.7 s, the vector store 0.09 s, and ★the judge 5–9 s, then HTTP 503 "high demand"★ — the free
judge model was overloaded, and each cut run spent a call for nothing. The old timing line could not tell
these apart; that is why the first step is the record.

## Steps — one pull request each

1. **Per-request records** (`brain/observe.py`). Hook: every way out (empty · command · disabled · short), each
   stage's time, the reindex, what was attached by which source (words · trigger · meaning · graph), and where
   the second stage was (vector · inputs · judge · done; request sent and returned; a valid answer; a fixed
   failure code). Which database answered (`stores.last_served`, `vectors.last_search`). MCP `recall`: a row per
   call. One file per kind per UTC day, appended only, no text.
2. **Reindex out of the hook.** Measured first (2026-10-14, author's index, one changed document): 1.70 s, of
   which ★1.10 s updating the graph database★ and 0.17 s re-reading every file for trigger declarations. So the
   hook keeps updating the words index in place — ⛔ never "once a day": a memory edited must not be recalled
   with its old content (`store.needs_reindex`, 2026-08-12) — and leaves the databases' update and the trigger
   healing to one detached worker (`brain/deferred.py`, started after the answer, one at a time). Until it lands
   the stores answer from the local copy (§stores.serve). The hook's pass went from 0.58–1.33 s to 0.32 s.
3. **The judge within the time left.** One deadline for embedding, search and judge; the cache first; whether a
   call can finish in the time left is judged from this installation's own records — cut runs count as lower
   bounds, never as completions; when the provider is overloaded, stop asking and probe for recovery through a
   shared slot (respecting `Retry-After`), so a bad hour costs no budget.
4. **`brain monitor`** (`brain/monitor.py`) — a daily summary, a line in `brain status`, history. Facts first
   ("the second stage was reached 12 times and finished 0" — finished means the judge reached a verdict, not
   merely replied), raised by the event and never by a tuned number; change against the person's own history
   second (the median and range of earlier days, "not enough history" until there are two). No composite
   score, no automatic action, ★no model call★. A completed UTC day is summarised once into `monitor.jsonl`
   by whichever daily job runs first (or `brain monitor --record`); raw record files are removed only after
   their day is summarised and older than `observe_keep_days` (default 14).
5. **Quality against the person's own history** — on the day's corpus and the stores in use: words top 3 ·
   meaning top 3 · ★words 3 + meaning 3 (as MCP `recall` returns it) against words 6★ at the same reading, the
   answers gained and lost, vector freshness (current sha and every chunk, `embed: false` excluded) and the
   stores' agreement (`stores.check`). A proxy sample measures rediscovering a document, not answering a
   question, and is labelled so.

A judge score distribution that moves is not a reason to move the threshold: the scale belongs to the model,
the prompt and the excerpt policy.
