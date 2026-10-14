"""★Are words and meaning really used together★ — read every day from the per-request records (2026-10-14).

Why this exists
---------------
brain finds a memory two ways — by its words and by its meaning (a vector store, or the local copy) — with a
graph of links beside them and a judge to re-rank. Whether that combination is working cannot be read off a
score: on 2026-10-14 the scores were where they always were, while on the author's machine 27% of the day's
prompts fell silent while reindexing and, from one evening on, 15 of 16 second-stage runs were cut waiting
on an overloaded judge. Nobody would have known. This module reads what §observe recorded and says so.

Rules (docs/notes/monitoring-plan.md, step 4)
- ★Facts first★: what happened, counted, with its denominator ("reached 12, finished 0"). A fact is raised
  by what happened (a stage reached and never finished, a call spent for nothing), never by a number tuned
  to one person's data.
- ★Then change against the person's own history★: today's value beside the median and the range of the
  days recorded before it, and "not enough history" until there are two earlier days. No composite score.
- ★No model call★: it reads records and the local index only.
- A completed UTC day is summarised once into `monitor.jsonl` under the brain's home (by the daily jobs, or
  `brain monitor --record`); raw record files of summarised days older than `observe_keep_days` (config,
  default 14) are removed then — never a day that has not been summarised.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

HISTORY = "monitor.jsonl"
KEEP_DAYS_DEFAULT = 14


# ── reading ──────────────────────────────────────────────────────────────────
def _pct(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)


def window(hours: float = 24.0, now: Optional[float] = None) -> Tuple[List[str], str]:
    """The UTC days that cover the last `hours`, and the earliest timestamp inside it."""
    from brain import observe
    now = now if now is not None else time.time()
    start = now - hours * 3600
    days = sorted({observe.day(start), observe.day(now)})
    return days, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start))


def rows(days: List[str], since: str = "") -> Dict[str, list]:
    from brain import observe
    out, broken = {}, 0
    for kind in ("hook", "recall", "deferred", "use"):
        got, b = observe.read(kind, days)
        out[kind] = [r for r in got if r.get("ts", "") >= since]
        broken += b
    out["broken"] = broken
    return out


def summarise(rec: Dict[str, list]) -> dict:
    """Counts with their denominators — the whole summary of a window."""
    hook = rec.get("hook") or []
    searched = [r for r in hook if "excluded" not in r]
    by_source = Counter()
    for r in searched:
        for k, v in (r.get("attached") or {}).items():
            by_source[k] += int(v or 0)
    rx = [r.get("reindex") or {} for r in searched]
    rx_ran = [x for x in rx if x.get("ran")]
    cuts = [r for r in searched if r.get("cut")]
    where = Counter((r.get("judge") or {}).get("phase") or r.get("stage") for r in cuts)
    h = {"rows": len(hook), "excluded": dict(Counter(r["excluded"] for r in hook if "excluded" in r)),
         "searched": len(searched), "attached_any": sum(1 for r in searched if r.get("hit")),
         "by_source": {k: by_source.get(k, 0) for k in ("lexical", "trigger", "semantic", "graph")},
         "silent_reindex": sum(1 for r in searched if r.get("stage") == "index" and not r.get("cut")
                               and not r.get("hit")),
         "cut": len(cuts), "cut_at": dict(where),
         "reindex": {"ran": len(rx_ran),
                     "p50": _pct([x["secs"] for x in rx_ran if x.get("secs") is not None], 0.5),
                     "p95": _pct([x["secs"] for x in rx_ran if x.get("secs") is not None], 0.95),
                     "deferred": sum(1 for x in rx_ran if x.get("deferred"))}}
    js = [r["judge"] for r in searched if r.get("judge")]
    vec = [j.get("vector") or {} for j in js if j.get("vector")]
    fresh = [j for j in js if j.get("returned") and not j.get("cached") and j.get("secs") is not None]
    # ★finished = the judge reached a verdict★ (above or below the threshold). A run that came back with an
    #   error went the whole way and still used nothing of meaning — counting it would hide exactly the day
    #   this monitor is for (2026-10-14: an overloaded judge).
    s = {"reached": len(js), "finished": sum(1 for j in js if j.get("decision") in ("below", "fired")),
         "outcomes": dict(Counter(j.get("outcome") for j in js)),
         "vector": {"remote": sum(1 for v in vec if v.get("by") == "remote"),
                    "local": sum(1 for v in vec if v.get("by") == "local"),
                    "local_why": dict(Counter(v.get("why") for v in vec if v.get("by") == "local" and v.get("why"))),
                    "embedded": sum(1 for v in vec if v.get("embedded"))},
         "sent": sum(1 for j in js if j.get("sent")), "returned": sum(1 for j in js if j.get("returned")),
         "valid": sum(1 for j in js if j.get("valid")),
         "cut_out": sum(1 for j in js if j.get("sent") and not j.get("returned")),
         "latency": {"n": len(fresh), "p50": _pct([j["secs"] for j in fresh], 0.5),
                     "p95": _pct([j["secs"] for j in fresh], 0.95)},
         "kinds": dict(Counter(j.get("kind") for j in js if j.get("kind"))),
         "top": dict(sorted(Counter(str(int(j["top"])) for j in js
                                    if j.get("valid") and j.get("top") is not None).items()))}
    rc = rec.get("recall") or []
    rvec = [r.get("vector") or {} for r in rc if r.get("vector")]
    # `auto` (2026-10-14): meaning added unasked because the words were unsure — apart from an asked list
    m = {"calls": len(rc), "semantic_asked": sum(1 for r in rc if int(r.get("semantic_k") or 0) > 0),
         "auto": sum(1 for r in rc if r.get("auto")),
         "meaning_returned": sum(1 for r in rc if int(r.get("meaning") or 0) > 0),
         "meaning_fail": dict(Counter(r.get("meaning_fail") for r in rc if r.get("meaning_fail"))),
         "vector": {"remote": sum(1 for v in rvec if v.get("by") == "remote"),
                    "local": sum(1 for v in rvec if v.get("by") == "local")},
         "graph": {"remote": sum(1 for r in rc if (r.get("graph") or {}).get("by") == "remote"),
                   "local": sum(1 for r in rc if (r.get("graph") or {}).get("by") == "local")}}
    dr = rec.get("deferred") or []
    d = {"runs": len(dr), "cleared": sum(1 for r in dr if r.get("cleared")),
         "stores": dict(Counter(v for r in dr for v in (r.get("stores") or {}).values())),
         "p50": _pct([r["secs"] for r in dr if r.get("secs") is not None], 0.5)}
    # ★What the agent then opened★ (§tail) — the one measure of "the right memory was found" that needs no
    #   label: was the opened memory shown by brain just before, and at which rank.
    us = rec.get("use") or []
    hit = [r for r in us if r.get("via")]
    u = {"opened": len(us), "shown": len(hit), "hook": sum(1 for r in hit if r["via"] == "hook"),
         "recall": sum(1 for r in hit if r["via"] == "recall"),
         "first": sum(1 for r in hit if r.get("rank") == 1),
         "top3": sum(1 for r in hit if (r.get("rank") or 99) <= 3)}
    return {"hook": h, "second": s, "recall": m, "deferred": d, "use": u, "broken": rec.get("broken", 0)}


# ── what stands out ──────────────────────────────────────────────────────────
def facts(s: dict) -> List[Tuple[str, str, dict]]:
    """(level, code, values) for what happened — raised by the event, never by a tuned number."""
    h, sec, m = s["hook"], s["second"], s["recall"]
    out: List[Tuple[str, str, dict]] = []
    if not h["rows"] and not m["calls"]:
        return [("info", "no_records", {})]
    if sec["reached"] and not sec["finished"]:
        out.append(("stop", "second_never_finished", {"n": sec["reached"]}))
    if h["silent_reindex"]:
        out.append(("warn", "silent_reindex", {"n": h["silent_reindex"], "of": h["searched"]}))
    if sec["cut_out"]:
        out.append(("warn", "judge_cut_out", {"n": sec["cut_out"]}))
    busy = {k: v for k, v in sec["kinds"].items() if k in ("overloaded", "rate_limited", "network")}
    if busy:
        out.append(("warn", "judge_provider", {"n": sum(busy.values()),
                                               "kinds": ", ".join("%s %d" % kv for kv in sorted(busy.items()))}))
    lost = {k: v for k, v in sec["vector"]["local_why"].items() if k in ("not_synced", "down_recently", "failed")}
    if lost:
        out.append(("warn", "store_fallback", {"n": sum(lost.values()),
                                               "why": ", ".join("%s %d" % kv for kv in sorted(lost.items()))}))
    if sec["outcomes"].get("uncalibrated"):
        out.append(("info", "judge_uncalibrated", {"n": sec["outcomes"]["uncalibrated"]}))
    if m["calls"] and not m["semantic_asked"] and not m["auto"]:
        out.append(("info", "meaning_never_asked", {"n": m["calls"]}))
    return out


METRICS = ("attach_rate", "silent_reindex_rate", "second_finish_rate", "judge_p50", "meaning_ask_rate",
           "shown_before_open", "meaning_used_rate")


def metrics(s: dict) -> Dict[str, Optional[float]]:
    """The few ratios compared with the person's own history — None when there is no denominator."""
    h, sec, m = s["hook"], s["second"], s["recall"]

    def ratio(a, b):
        return round(a / b, 3) if b else None
    return {"attach_rate": ratio(h["attached_any"], h["searched"]),
            "silent_reindex_rate": ratio(h["silent_reindex"], h["searched"]),
            "second_finish_rate": ratio(sec["finished"], sec["reached"]),
            "judge_p50": sec["latency"]["p50"],
            "meaning_ask_rate": ratio(m["semantic_asked"], m["calls"]),
            "shown_before_open": ratio(s.get("use", {}).get("shown", 0), s.get("use", {}).get("opened", 0)),
            "meaning_used_rate": ratio(m["meaning_returned"], m["calls"])}


def trend(now: Dict[str, Optional[float]], before: List[dict]) -> Dict[str, dict]:
    """Each metric beside the median and range of the earlier days that have it; 'short' until there are two."""
    out = {}
    for k in METRICS:
        prev = [b["metrics"][k] for b in before if (b.get("metrics") or {}).get(k) is not None]
        if now.get(k) is None:
            continue
        if len(prev) < 2:
            out[k] = {"now": now[k], "short": len(prev)}
            continue
        ps = sorted(prev)
        out[k] = {"now": now[k], "median": ps[len(ps) // 2], "lo": ps[0], "hi": ps[-1], "n": len(ps),
                  "outside": not ps[0] <= now[k] <= ps[-1]}
    return out


# ── history ──────────────────────────────────────────────────────────────────
def _history_path() -> str:
    from brain import store
    return os.path.join(store.brain_home(), HISTORY)


def history() -> List[dict]:
    """Recorded days, oldest first — one row per day (a day recorded twice keeps its first row)."""
    seen, out = set(), []
    try:
        with open(_history_path(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("day") and r["day"] not in seen:
                    seen.add(r["day"])
                    out.append(r)
    except OSError:
        pass
    return sorted(out, key=lambda r: r["day"])


def latest() -> Optional[dict]:
    h = history()
    return h[-1] if h else None


def _keep_days() -> int:
    try:
        from brain import store
        return int(store.load_config(tolerant=True).get("observe_keep_days") or KEEP_DAYS_DEFAULT)
    except Exception:                                    # noqa: BLE001
        return KEEP_DAYS_DEFAULT


def record(now: Optional[float] = None) -> dict:
    """Summarise every completed UTC day that has records and no summary yet; prune old summarised days."""
    from brain import calibrate, observe, store
    now = now if now is not None else time.time()
    today = observe.day(now)
    done = {r["day"] for r in history()}
    base = os.path.join(store.brain_home(), observe.DIR)
    days = set()
    try:
        for f in os.listdir(base):
            if f.endswith(".jsonl") and "-" in f:
                days.add(f.rsplit(".", 1)[0][-10:])
    except OSError:
        pass
    written = []
    for d in sorted(x for x in days if x < today and x not in done):
        s = summarise(rows([d]))
        row = {"day": d, "gen": calibrate.CODE_GENERATION, "summary": s, "metrics": metrics(s),
               "facts": [[lv, c, v] for lv, c, v in facts(s)]}
        with open(_history_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        written.append(d)
        done.add(d)
    cutoff = observe.day(now - _keep_days() * 86400)
    pruned = 0
    try:
        for f in os.listdir(base):
            d = f.rsplit(".", 1)[0][-10:]
            if f.endswith(".jsonl") and d < cutoff and d in done:
                os.remove(os.path.join(base, f))
                pruned += 1
    except OSError:
        pass
    return {"recorded": written, "pruned": pruned}


# ── the screen ───────────────────────────────────────────────────────────────
def _fact_line(code: str, v: dict) -> str:
    from brain import i18n
    # ⛔ ★literal keys★ — `verify_i18n` finds a key only where it is written out
    table = {
        "no_records": lambda: i18n.t("monitor.fact.no_records"),
        "second_never_finished": lambda: i18n.t("monitor.fact.second_never_finished", **v),
        "silent_reindex": lambda: i18n.t("monitor.fact.silent_reindex", **v),
        "judge_cut_out": lambda: i18n.t("monitor.fact.judge_cut_out", **v),
        "judge_provider": lambda: i18n.t("monitor.fact.judge_provider", **v),
        "store_fallback": lambda: i18n.t("monitor.fact.store_fallback", **v),
        "judge_uncalibrated": lambda: i18n.t("monitor.fact.judge_uncalibrated", **v),
        "meaning_never_asked": lambda: i18n.t("monitor.fact.meaning_never_asked", **v),
    }
    return table[code]() if code in table else code


def _metric_name(k: str) -> str:
    from brain import i18n
    return {"attach_rate": i18n.t("monitor.metric.attach_rate"),
            "silent_reindex_rate": i18n.t("monitor.metric.silent_reindex_rate"),
            "second_finish_rate": i18n.t("monitor.metric.second_finish_rate"),
            "judge_p50": i18n.t("monitor.metric.judge_p50"),
            "meaning_ask_rate": i18n.t("monitor.metric.meaning_ask_rate"),
            "shown_before_open": i18n.t("monitor.metric.shown_before_open"),
            "meaning_used_rate": i18n.t("monitor.metric.meaning_used_rate")}.get(k, k)


def _kv(d: dict) -> str:
    return ", ".join("%s %s" % (k, v) for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))) or "-"


def _s(x) -> str:
    return "-" if x is None else ("%.2f" % x if isinstance(x, float) else str(x))


def render(s: dict, tr: Dict[str, dict], label: str) -> str:
    from brain import i18n, observe, store
    h, sec, m, d = s["hook"], s["second"], s["recall"], s["deferred"]
    pct = round(100.0 * h["attached_any"] / h["searched"]) if h["searched"] else 0
    lines = [i18n.t("monitor.title", window=label, home=os.path.join(store.brain_home(), observe.DIR)), "",
             i18n.t("monitor.hook", rows=h["rows"], excluded=sum(h["excluded"].values()),
                    reasons=_kv(h["excluded"]), searched=h["searched"]),
             i18n.t("monitor.attached", any=h["attached_any"], searched=h["searched"], pct=pct,
                    **h["by_source"]),
             i18n.t("monitor.reindex", ran=h["reindex"]["ran"], p50=_s(h["reindex"]["p50"]),
                    p95=_s(h["reindex"]["p95"]), deferred=h["reindex"]["deferred"], silent=h["silent_reindex"]),
             i18n.t("monitor.cut", cut=h["cut"], where=_kv(h["cut_at"])),
             i18n.t("monitor.second", reached=sec["reached"], finished=sec["finished"],
                    outcomes=_kv(sec["outcomes"])),
             i18n.t("monitor.vector", remote=sec["vector"]["remote"], local=sec["vector"]["local"],
                    why=_kv(sec["vector"]["local_why"]), embedded=sec["vector"]["embedded"]),
             i18n.t("monitor.judge", sent=sec["sent"], returned=sec["returned"], valid=sec["valid"],
                    cut_out=sec["cut_out"], p50=_s(sec["latency"]["p50"]), p95=_s(sec["latency"]["p95"]),
                    kinds=_kv(sec["kinds"])),
             i18n.t("monitor.recall", calls=m["calls"], asked=m["semantic_asked"], auto=m.get("auto", 0),
                    returned=m["meaning_returned"], fails=_kv(m["meaning_fail"])),
             i18n.t("monitor.deferred", runs=d["runs"], cleared=d["cleared"], stores=_kv(d["stores"])),
             i18n.t("monitor.use", **s.get("use", {"opened": 0, "shown": 0, "hook": 0, "recall": 0,
                                                    "first": 0, "top3": 0})),
             "", i18n.t("monitor.facts")]
    fs = facts(s)
    lines += ["  " + _fact_line(c, v) for _lv, c, v in fs] or ["  " + i18n.t("monitor.fact.none")]
    before = len(history())
    lines += ["", i18n.t("monitor.trend.title", n=before)]
    for k, v in tr.items():
        if "short" in v:
            lines.append("  %s: %s — %s" % (_metric_name(k), _s(v["now"]), i18n.t("monitor.trend.short", n=v["short"])))
        else:
            lines.append("  " + i18n.t("monitor.trend.row", metric=_metric_name(k), now=_s(v["now"]),
                                       median=_s(v["median"]), lo=_s(v["lo"]), hi=_s(v["hi"]))
                         + (i18n.t("monitor.trend.outside") if v["outside"] else ""))
    if s.get("broken"):
        lines.append(i18n.t("monitor.broken", n=s["broken"]))
    return "\n".join(lines)


def status_line() -> str:
    """One line for `brain status` — from the last recorded day only (it never reads raw records)."""
    from brain import i18n
    r = latest()
    if not r:
        return i18n.t("cli.status.monitor_none")
    s = r["summary"]
    stop = any(f[0] == "stop" for f in r.get("facts") or [])
    return ("⛔ " if stop else "") + i18n.t(
        "cli.status.monitor", day=r["day"], searched=s["hook"]["searched"], attached=s["hook"]["attached_any"],
        silent=s["hook"]["silent_reindex"], finished=s["second"]["finished"], reached=s["second"]["reached"],
        asked=s["recall"]["semantic_asked"], calls=s["recall"]["calls"])
