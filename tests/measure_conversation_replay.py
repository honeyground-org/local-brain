#!/usr/bin/env python3
"""★The second axis — real conversations replayed★: what the agent actually opened after each prompt. (local · budget 0 · a measurement)

Why this exists (2026-11-02)
----------------------------
The labelled sample (`tests/eval/short.json`) is one axis; a change that wins there can still lose in use.
The project's rule since 2026-10-14 is ★two axes, no loss on either★ (docs/domains/local-brain/CODEX_HANDOFF
§3-2): the second is the person's own transcripts — a prompt, then the indexed documents the agent opened in
that turn. They are weak labels (a file opened to edit it is not an answer), so this axis is never the only one
turned on; it is the one that says whether the labelled gain survives real prompts.

What it measures, on the same snapshot of the index (so a run spread over minutes measures one corpus):

  recall surface   per turn with at least one opened indexed document — the rank of the first opened document in
                   the words' top 8 (`search.recall`) and in the two-lane order where the words are unsure
                   (`search.union`, the hook's own rule deciding "unsure" — §server._words_unsure)
  hook surface     per turn — what the deployed words' stage attaches (`hook.lexical`) and what the judge-free
                   second stage adds (`hook.fallback`); turns that opened ★no★ indexed document stand in for
                   prompts that needed no memory — the wider control group the 34 labelled ones cannot be

Transcripts are read as the host writes them (§hosts.ClaudeCode: `user_text`, `brain_events`), the newest first,
within `--days`. Nothing is sent anywhere; the query embedding is the local engine's or the cache's.

Usage:
    PYTHONPATH=. python3 tests/measure_conversation_replay.py                 # last 14 days, up to 400 turns
    PYTHONPATH=. python3 tests/measure_conversation_replay.py --days 30 --max 800 --json out.json
    PYTHONPATH=. python3 tests/measure_conversation_replay.py --snapshot /tmp/brain-replay   # where the copy lives

Exit code 77 when there are no transcripts or no indexed notes to replay against.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MIN_PROMPT_CHARS = 12


def _snapshot(path: str) -> None:
    """Point this process at a fresh copy of the live index — never the one the hook is writing to."""
    from brain import evalinit, rerank, store
    os.environ.setdefault("BRAIN_CONFIG", store.default_config_path())
    os.environ.setdefault("BRAIN_EVAL_DIR", evalinit.eval_dir())
    os.environ.setdefault("BRAIN_LEDGER", rerank.ledger_path())
    live = store.db_path()
    home = store.brain_home()
    os.makedirs(path, exist_ok=True)
    manifest = os.path.join(home, "embeddinggemma.json")
    if os.path.exists(manifest):
        shutil.copy(manifest, os.path.join(path, "embeddinggemma.json"))
    os.environ["BRAIN_VECTOR_STORE"] = "sqlite"
    os.environ["BRAIN_GRAPH_STORE"] = "sqlite"
    os.environ["BRAIN_HOME"] = path
    copy = store.db_path()
    if os.path.exists(copy):
        os.remove(copy)
    src, dst = sqlite3.connect(live), sqlite3.connect(copy)
    src.backup(dst)
    dst.close()
    src.close()
    print("snapshot: %s" % copy)


def _turns(host, days: int, limit: int):
    """[(prompt, [opened paths])] newest first, from the host's transcripts within `days`."""
    cutoff = time.time() - days * 86400
    files = []
    for pattern in host.transcripts():
        files += [p for p in glob.glob(os.path.expanduser(pattern)) if os.path.getmtime(p) >= cutoff]
    files.sort(key=lambda p: -os.path.getmtime(p))
    out = []
    prefixes = tuple(host.command_prefixes())
    for path in files:
        turns = []
        cur = None
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    text = host.user_text(line)
                    if text:
                        if cur is not None:
                            turns.append(cur)
                        cur = [text, []] if len(text) >= MIN_PROMPT_CHARS and not text.startswith(prefixes) else None
                        continue
                    if cur is None:
                        continue
                    for ev in host.brain_events(line):
                        if ev[0] == "read" and ev[1]:
                            cur[1].append(ev[1])
        except OSError:
            continue
        if cur is not None:
            turns.append(cur)
        out.extend(reversed(turns))                      # newest turn first within the file
        if len(out) >= limit:
            break
    return out[:limit]


def _rank(names, wanted) -> int:
    return next((i + 1 for i, n in enumerate(names) if n in wanted), 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max", type=int, default=400, help="turns to replay, newest first")
    ap.add_argument("--snapshot", default="", help="folder for the index copy (default: under the brain's home)")
    ap.add_argument("--json", default="", help="write the per-turn rows and the summary here")
    ap.add_argument("--calibrate", action="store_true",
                    help="measure both vector gates on the copy from the labelled sample first (as `brain vec calibrate` would)")
    args = ap.parse_args()
    from brain import hosts, store
    snap = args.snapshot or os.path.join(store.brain_home(), "measure", "conversation-replay")
    _snapshot(snap)
    from brain import calibrate, hook, search, tail, vectors
    from tests import _needs
    search._record = lambda *a, **k: None
    db = store.connect()
    _needs.memories(db)
    host = hosts.ClaudeCode()
    if not host.detect():
        _needs.skip("needs Claude Code transcripts (~/.claude/projects/*/*.jsonl)")
    turns = _turns(host, args.days, args.max)
    if not turns:
        _needs.skip("no transcripts in the last %d days" % args.days)
    if args.calibrate:
        import json as _json
        from brain import evalinit
        p = os.path.join(evalinit.eval_dir(), "short.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                d = _json.load(fh)
            control = [c["q"] if isinstance(c, dict) else c for c in d.get("C_no_memory_needed", [])]
            positives = [(c["q"], c["gold"]) for c in d.get("A_memory_needed", []) if c.get("gold")]
            if control and positives:
                res = vectors.calibrate_cos(db, control, positives, save=True)
                print("gates measured on the copy: ratio %.4f · agreement %.4f (controls agreeing %d)"
                      % (res["min_ratio"], res["agree_gate"]["min_ratio"], res["agree_gate"]["controls_agreeing"]))
    threshold = calibrate.threshold(db)
    print("replaying %d turns from the last %d days · hook threshold %.2f · agreement gate %s · ratio gate %s"
          % (len(turns), args.days, threshold,
             ("%.3f" % vectors.min_agree_ratio(db)) if vectors.min_agree_ratio(db) < 99 else "unset",
             ("%.3f" % vectors.min_ratio(db)) if vectors.min_ratio(db) < 99 else "unset"))
    rows = []
    cost = []
    for prompt, opened in turns:
        wanted = set()
        for p in opened:
            r = tail._doc_of(db, p)
            if r is not None:
                wanted.add(r["name"])
        q = prompt[:hook.MAX_PROMPT_CHARS]
        # recall surface — as an agent's `recall` runs, question alone (no terms): words, then the unsure rule
        wrows = search.recall(db, q, k=search.FUSE_POOL, log=False)
        words = [r["name"] for r in wrows if not r.get("related")]
        sure = bool(hook.select(wrows, threshold))
        t0 = time.perf_counter()
        if sure:
            merged = words
        else:
            urows, _why = search.union(db, q, wrows, 8)
            merged = [r["name"] for r in urows]
        cost.append(time.perf_counter() - t0)
        # hook surface — the deployed words' stage, then the judge-free second stage
        lex = [r["name"] for r in hook.lexical(db, q)]
        second = [] if lex else [r["name"] for r in hook.fallback(db, q, strict=False, observe=search.judge_observation())]
        rows.append({"chars": len(prompt), "opened": len(wanted), "sure": sure,
                     "rank_words": _rank(words[:8], wanted), "rank_two_lanes": _rank(merged[:8], wanted),
                     "lexical_attached": len(lex), "lexical_hit": bool(set(lex) & wanted),
                     "second_attached": len(second), "second_hit": bool(set(second) & wanted)})
    with_open = [r for r in rows if r["opened"]]
    none_open = [r for r in rows if not r["opened"]]

    def hits(key, n):
        return sum(1 for r in with_open if 0 < r[key] <= n)
    lost = [r for r in with_open if 0 < r["rank_words"] <= 3 and not (0 < r["rank_two_lanes"] <= 3)]
    gained = [r for r in with_open if not (0 < r["rank_words"] <= 3) and 0 < r["rank_two_lanes"] <= 3]
    print()
    print("recall surface · %d turns opened an indexed document (%d opened none)" % (len(with_open), len(none_open)))
    print("  %-28s %7s %7s" % ("", "top 3", "top 8"))
    print("  %-28s %7d %7d" % ("words", hits("rank_words", 3), hits("rank_words", 8)))
    print("  %-28s %7d %7d   lost %d · gained %d · words unsure on %d of these turns"
          % ("two lanes where unsure", hits("rank_two_lanes", 3), hits("rank_two_lanes", 8), len(lost), len(gained),
             sum(1 for r in with_open if not r["sure"])))
    cost.sort()
    if cost:
        print("  two-lane cost where unsure: p50 %.3fs · p95 %.3fs" % (cost[len(cost) // 2], cost[int(len(cost) * .95) - 1]))
    print()
    print("hook surface · %d turns" % len(rows))
    print("  %-28s %12s %14s %18s" % ("", "attached", "opened it too", "fires, nothing opened"))
    print("  %-28s %12d %14d %18d" % ("words' stage (deployed)", sum(r["lexical_attached"] for r in rows),
                                        sum(1 for r in rows if r["lexical_hit"]),
                                        sum(1 for r in none_open if r["lexical_attached"])))
    print("  %-28s %12d %14d %18d" % ("+ judge-free second stage", sum(r["second_attached"] for r in rows),
                                        sum(1 for r in rows if r["second_hit"]),
                                        sum(1 for r in none_open if r["second_attached"])))
    print("  ⛔ a turn that opened nothing stands in for 'no memory needed' — a weak label, read as a bound, not a verdict")
    summary = {"turns": len(rows), "with_opened": len(with_open), "none_opened": len(none_open), "days": args.days,
               "words_top3": hits("rank_words", 3), "words_top8": hits("rank_words", 8),
               "two_lanes_top3": hits("rank_two_lanes", 3), "two_lanes_top8": hits("rank_two_lanes", 8),
               "lost": len(lost), "gained": len(gained),
               "lexical_attached": sum(r["lexical_attached"] for r in rows), "lexical_hit": sum(1 for r in rows if r["lexical_hit"]),
               "lexical_fires_none_opened": sum(1 for r in none_open if r["lexical_attached"]),
               "second_attached": sum(r["second_attached"] for r in rows), "second_hit": sum(1 for r in rows if r["second_hit"]),
               "second_fires_none_opened": sum(1 for r in none_open if r["second_attached"]),
               "threshold": threshold, "agree_gate": vectors.min_agree_ratio(db), "ratio_gate": vectors.min_ratio(db)}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "rows": rows}, fh, ensure_ascii=False, indent=1)
        print("written %s" % args.json)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
