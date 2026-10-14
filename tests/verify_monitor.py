#!/usr/bin/env python3
"""★`brain monitor` says whether words and meaning are used together — from the records, with no model call★
(local · budget 0)

Why this exists (2026-10-14)
----------------------------
On the day the plan was made the scores looked as always, while 27% of the author's prompts fell silent while
reindexing and 15 of 16 second-stage runs were cut waiting on an overloaded judge. §monitor reads the per-request
records (§observe) and says so. This check writes records of known content for several days and holds down:

  ① the summary counts what happened, with its denominators — ways out, searched, attached by source, silent
     while reindexing, cuts and where, the second stage reached and finished (a verdict, not merely a reply —
     an overloaded judge's error used nothing of meaning), which store answered, the judge
     sent / answered / valid / cut while out, MCP recall and the worker
  ② what stands out is raised by the event, not by a tuned number — a stage reached and never finished is ⛔;
     a day with nothing unusual says so
  ③ a completed day is recorded ★once★ (a second run writes nothing) and today never; history is per day
  ④ against its own history: the median and range of earlier days, "outside the range" when it is, and
     "not enough history" until there are two earlier days
  ⑤ old raw records are removed only after their day is summarised
  ⑥ `brain status`'s line reads only the recorded summary, and marks a ⛔ day
  ⑦ ★no model call★ — neither the judge nor the embedding is touched by `brain monitor` or `brain status`
  ⑧ the screen renders in all six languages with nothing left as a raw key

⛔ Control: a day whose second stage finished does ★not★ raise ⛔ — so ② can tell the two apart.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-monitor-")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")

from brain import cli, engines, i18n, monitor, observe, vectors  # noqa: E402

FAILS = []
NOW = time.time()


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def day(back: int) -> str:
    return observe.day(NOW - back * 86400)


def put(kind: str, d: str, rows: list) -> None:
    p = observe.path(kind, d)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(dict({"schema": 1, "run": "x", "ts": d + "T12:00:00Z", "gen": 0}, **r)) + "\n")


def searched(**kw) -> dict:
    r = {"total": 0.3, "stage": "merge", "path": "lexical", "hit": True, "cut": False,
         "t": {"index": 0.01}, "reindex": {"ran": False},
         "attached": {"lexical": 1, "trigger": 0, "semantic": 0, "graph": 0}, "judge": None}
    r.update(kw)
    return r


def judge(**kw) -> dict:
    j = {"outcome": "fired", "phase": "done", "sent": True, "returned": True, "valid": True, "cached": False,
         "secs": 1.0, "kind": None, "decision": "fired", "top": 9.0,
         "vector": {"embedded": False, "store": "qdrant", "by": "remote", "why": None}}
    j.update(kw)
    return j


def day_rows(attached: int, silent: int, finished: bool) -> list:
    rows = [{"excluded": "short", "total": 0.0}, {"excluded": "command", "total": 0.0}]
    rows += [searched() for _ in range(attached)]
    rows += [searched(stage="index", hit=False, attached={"lexical": 0, "trigger": 0, "semantic": 0, "graph": 0},
                      reindex={"ran": True, "secs": 1.2, "changed": 1}) for _ in range(silent)]
    if finished:
        rows.append(searched(path="fallback", attached={"lexical": 0, "trigger": 0, "semantic": 1, "graph": 0},
                             judge=judge()))
    rows.append(searched(path="fallback", stage="fallback", hit=False, cut=True,
                         attached={"lexical": 0, "trigger": 0, "semantic": 0, "graph": 0},
                         judge=judge(outcome="cut", phase="judge", returned=False, valid=None, secs=None,
                                     decision=None, top=None)))
    rows.append(searched(path="fallback", hit=False, attached={"lexical": 0, "trigger": 0, "semantic": 0, "graph": 0},
                         judge=judge(outcome="failed", phase="done", valid=None, kind="overloaded", decision=None,
                                     top=None, vector={"embedded": True, "store": "sqlite", "by": "local",
                                                       "why": "not_synced"})))
    return rows


def main() -> int:
    print("=" * 78)
    print("brain monitor — words and meaning, from the records, no model call")
    print("=" * 78)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [], "observe_keep_days": 2}, fh)
    # four earlier days and today: day 1 (yesterday) has a finished second stage; day 2 does not
    put("hook", day(3), day_rows(6, 1, True))
    put("hook", day(2), day_rows(5, 4, False))
    put("hook", day(1), day_rows(6, 2, True))
    put("hook", day(0), day_rows(1, 9, True))
    put("hook", day(20), day_rows(3, 0, True))
    put("recall", day(1), [{"k": 8, "terms": 5, "filtered": False, "semantic_k": 0, "words": 8, "related": 1,
                            "meaning": None, "meaning_fail": None, "graph": {"by": "remote"}},
                           {"k": 3, "terms": 4, "filtered": True, "semantic_k": 3, "words": 3, "related": 0,
                            "meaning": 3, "meaning_fail": None, "vector": {"by": "remote"}, "graph": {"by": "local"}},
                           {"k": 3, "terms": 0, "filtered": False, "semantic_k": 2, "words": 3, "related": 0,
                            "meaning": 0, "meaning_fail": "NoKey", "graph": {"by": "local"}}])
    put("deferred", day(1), [{"work": ["sync"], "cleared": True, "stores": {"graph": "sent"}, "secs": 1.1},
                             {"work": ["sync"], "cleared": False, "stores": {"graph": "error"}, "secs": 0.4}])

    # ── ① the summary ────────────────────────────────────────────────────────
    s = monitor.summarise(monitor.rows([day(1)]))
    h, sec, m, d = s["hook"], s["second"], s["recall"], s["deferred"]
    check("① ways out and the denominator", h["rows"] == 13 and h["excluded"] == {"short": 1, "command": 1}
          and h["searched"] == 11, json.dumps({k: h[k] for k in ("rows", "excluded", "searched")}))
    check("① attached by source, silent while reindexing, cuts and where",
          h["attached_any"] == 7 and h["by_source"] == {"lexical": 6, "trigger": 0, "semantic": 1, "graph": 0}
          and h["silent_reindex"] == 2 and h["cut"] == 1 and h["cut_at"] == {"judge": 1}
          and h["reindex"]["ran"] == 2, json.dumps({k: h[k] for k in ("attached_any", "by_source", "silent_reindex", "cut_at")}))
    check("① the second stage: reached, finished, which store, the judge",
          sec["reached"] == 3 and sec["finished"] == 1 and sec["vector"]["remote"] == 2
          and sec["vector"]["local_why"] == {"not_synced": 1} and sec["vector"]["embedded"] == 1
          and sec["sent"] == 3 and sec["returned"] == 2 and sec["valid"] == 1 and sec["cut_out"] == 1
          and sec["kinds"] == {"overloaded": 1} and sec["latency"]["n"] == 2,
          json.dumps({k: sec[k] for k in ("reached", "finished", "sent", "returned", "valid", "cut_out")}))
    check("① MCP recall and the worker",
          m["calls"] == 3 and m["semantic_asked"] == 2 and m["meaning_returned"] == 1
          and m["meaning_fail"] == {"NoKey": 1} and m["graph"] == {"remote": 1, "local": 2}
          and d["runs"] == 2 and d["cleared"] == 1 and d["stores"] == {"sent": 1, "error": 1},
          json.dumps({"recall": m, "deferred": d})[:160])

    # ── ② what stands out ────────────────────────────────────────────────────
    codes = {c for _lv, c, _v in monitor.facts(s)}
    check("② events are named: silent while reindexing, a call cut while out, the provider, a store fallback",
          {"silent_reindex", "judge_cut_out", "judge_provider", "store_fallback"} <= codes, ", ".join(sorted(codes)))
    check("② ★control★: a day whose second stage finished raises no ⛔",
          not any(lv == "stop" for lv, _c, _v in monitor.facts(s)))
    s2 = monitor.summarise(monitor.rows([day(2)]))
    check("② a day where the second stage was reached and never finished is ⛔",
          ("stop", "second_never_finished") in {(lv, c) for lv, c, _v in monitor.facts(s2)})
    check("② no records → said so", monitor.facts(monitor.summarise(monitor.rows(["1999-01-01"])))
          == [("info", "no_records", {})])

    # ── ③ ⑤ recording and pruning ────────────────────────────────────────────
    got = monitor.record(now=NOW)
    again = monitor.record(now=NOW)
    days = [r["day"] for r in monitor.history()]
    check("③ every completed day is recorded once, today never",
          sorted(got["recorded"]) == sorted([day(20), day(3), day(2), day(1)]) and again["recorded"] == []
          and days == sorted(days) and day(0) not in days, "%s · again %s" % (got["recorded"], again["recorded"]))
    left = sorted(f for f in os.listdir(os.path.join(os.environ["BRAIN_HOME"], observe.DIR)))
    check("⑤ raw records older than the kept days are removed — only once summarised",
          not any(day(20) in f or day(3) in f for f in left) and any(day(1) in f for f in left)
          and got["pruned"] >= 2, "%s" % left)

    # ── ④ against its own history ────────────────────────────────────────────
    hist = monitor.history()
    today = monitor.metrics(monitor.summarise(monitor.rows([day(0)])))
    tr = monitor.trend(today, hist)
    sr = tr.get("silent_reindex_rate") or {}
    check("④ today beside the median and range of earlier days, flagged when outside",
          sr.get("n") == 4 and sr.get("outside") is True and sr.get("lo") is not None, json.dumps(sr))
    tr1 = monitor.trend(today, hist[:1])
    check("④ fewer than two earlier days → not enough history", all("short" in v for v in tr1.values()),
          json.dumps(tr1)[:120])

    # ── ⑥ ⑦ status and no model call ─────────────────────────────────────────
    calls = []
    real_j, real_e, real_r = engines.judge_text, vectors.embed, observe.read
    engines.judge_text = lambda *a, **k: calls.append("judge")
    vectors.embed = lambda *a, **k: calls.append("embed")
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["monitor"])
            cli.main(["monitor", "--json"])
        observe.read = lambda *a, **k: (_ for _ in ()).throw(AssertionError("raw records read"))
        line = monitor.status_line()
    finally:
        engines.judge_text, vectors.embed, observe.read = real_j, real_e, real_r
    check("⑦ ★no model call★ from `brain monitor`", not calls and "Words and meaning" in out.getvalue(), "%s" % calls)
    check("⑥ the status line reads only the recorded summary", day(1) in line and "/3" in line, line[:120])
    with open(monitor._history_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"day": "2999-01-01", "summary": s2, "metrics": monitor.metrics(s2),
                             "facts": [list(f) for f in monitor.facts(s2)]}) + "\n")
    check("⑥ a ⛔ day is marked on the status line", monitor.status_line().startswith("⛔"))

    # ── ⑧ every language ─────────────────────────────────────────────────────
    bad = []
    saved = os.environ.get("BRAIN_LANG")
    for code in ("en", "ko", "de", "es", "fr", "ja"):
        os.environ["BRAIN_LANG"] = code
        i18n._lang = None
        text = monitor.render(s, tr, "24h") + "\n" + monitor.status_line()
        if "monitor." in text or "cli.status." in text:
            bad.append(code)
    if saved is None:
        os.environ.pop("BRAIN_LANG", None)
    else:
        os.environ["BRAIN_LANG"] = saved
    i18n._lang = None
    check("⑧ the screen renders in all six languages, no raw key left", not bad, "%s" % bad)

    print("=" * 78)
    if FAILS:
        print("❌ %d failed" % len(FAILS))
        return 1
    print("✅ all passed — the records say whether words and meaning are used together")
    return 0


if __name__ == "__main__":
    sys.exit(main())
