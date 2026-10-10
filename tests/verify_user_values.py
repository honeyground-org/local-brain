#!/usr/bin/env python3
"""★Every number has an owner★ — the person, their data, their host or engine, or a method that says so. (local · budget 0)

## What this replaced (2026-10-06, stage 5 of the neutrality audit)

Constants that had been read off ★one person's corpus or one provider's quota★ were applied to everyone:
  · `10.0` as the threshold when calibration fails — a value that worked on the author's 867 documents
  · Gemini's free-tier walls (500 judge calls a day, 100 embedded items a minute) on ★any★ provider
  · auto-approving rules at a score of 10 on a judge whose 0–10 scale was never measured
  · the author's kinds in the staleness table · a deploy margin worth one hit in a 57-item sample ·
    an absolute relative-rule floor of 7.5 · "short" as 12–40 characters (a Korean density)

## What this holds down — fixtures and child processes, never the author's data

  ① no ruler → nothing fires (`NO_RULER`), the relative rule included
  ② walls are the provider's — Gemini keeps 500/day and 100 items/min; another judge has ★no daily wall
     we know of★ (`left` None, the reserve does not block) and another embedder is not paced
  ③ auto-approval waits on a judge whose scale was not measured; a human-named bar opens it
  ④ staleness: the host's own types, then the person's config; an unknown kind takes the default
  ⑤ the deploy margin is counted in hits — one lost hit passes on any sample size, two block
  ⑥ the relative-rule floor scales with the threshold
  ⑦ "short" is the person's own shortest 30%
  ⑧ method settings name themselves and can be overridden (`BRAIN_CALIB_MARGIN`, `BRAIN_FIRE_RATE_OK`)
  Each has a control: the old constant, put back, fails the same check.

How to run:  PYTHONPATH=. python3 tests/verify_user_values.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-uservalues-")
_KEYS = ("BRAIN_HOME", "BRAIN_CONFIG", "BRAIN_HOST", "BRAIN_JUDGE_PROVIDER", "BRAIN_EMBED_PROVIDER",
         "BRAIN_RULE_AUTO_MIN")
_SAVED = {k: os.environ.get(k) for k in _KEYS}
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as _fh:
    json.dump({"sources": []}, _fh)

from brain import calibrate, evalinit, hook, hosts, search  # noqa: E402

FAILS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def child(code: str, **env) -> dict:
    """A fresh process — for values a module reads once, at import."""
    e = dict(os.environ, PYTHONPATH=ROOT, **env)
    for k, v in list(e.items()):
        if v is None:
            e.pop(k)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=e, timeout=120, encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"_error": (r.stderr or r.stdout)[-400:]}


def row(score: float, ratio: float) -> dict:
    return {"name": "n", "score": score, "ratio": ratio}


def main() -> int:
    print("=" * 72 + "\n★① no ruler, no firing★\n" + "=" * 72)
    real = calibrate.calibrate
    try:
        calibrate.calibrate = lambda db, force=False: (_ for _ in ()).throw(RuntimeError("cannot measure"))
        from brain import store
        t = calibrate.threshold(store.connect())
    finally:
        calibrate.calibrate = real
    check("a corpus that cannot be calibrated gets no ruler", t == calibrate.NO_RULER, str(t))
    check("…and nothing fires on it, the relative rule included",
          hook.select([row(50.0, 1.0)], t) == [])
    check("⛔ control: the old fallback 10.0 would have fired on that same row",
          hook.select([row(50.0, 1.0)], 10.0) != [])

    print("\n★② walls are the provider's★")
    probe = ("import json; from brain import rerank, vectors, store; db = store.connect(); "
             "b = rerank.budget(db); print(json.dumps({'rpd': rerank.RPD, 'left': b['left'], "
             "'rpm_items': vectors.RPM_ITEMS, 'waited': vectors._wait_for_quota(500)}))")
    g = child(probe, BRAIN_JUDGE_PROVIDER="gemini", BRAIN_EMBED_PROVIDER="gemini")
    o = child(probe, BRAIN_JUDGE_PROVIDER="openai", BRAIN_EMBED_PROVIDER="openai")
    check("Gemini keeps its measured walls (500 a day · 100 items a minute)",
          g.get("rpd") == 500 and g.get("left") == 500 and g.get("rpm_items") == 100, str(g))
    check("⛔ another judge has no daily wall we know of — `left` is None, not 500",
          o.get("rpd") == 0 and o.get("left") is None, str(o))
    check("⛔ another embedder is not paced by Gemini's minute", o.get("rpm_items") == 0
          and o.get("waited") == 0, str(o))
    gate = ("import json; from brain import rerank, store; db = store.connect(); "
            "store.set_meta(db, 'rerank_day', rerank._pt_day()); store.set_meta(db, 'rerank_used', '480'); "
            "b = rerank.budget(db); "
            "print(json.dumps({'left': b['left'], 'blocked': bool(b['left'] is not None and b['left'] <= 150)}))")
    check("the reserve still guards Gemini's last calls",
          child(gate, BRAIN_JUDGE_PROVIDER="gemini").get("blocked") is True)
    check("⛔ and does not invent a reserve for a judge with no known wall",
          child(gate, BRAIN_JUDGE_PROVIDER="openai").get("blocked") is False)
    learned = ("import json; from brain import rerank, store; db = store.connect(); "
               "store.set_meta(db, 'rerank_rpd', '1000'); print(json.dumps(rerank.budget(db)))")
    check("a wall learned from a refusal is the one used",
          child(learned, BRAIN_JUDGE_PROVIDER="openai").get("limit") == 1000)

    print("\n★③ auto-approval waits on an unmeasured judge★")
    probe = ("import json; from brain import ruledisc; r = ruledisc.auto_approve(proposals=[{'signal': 's', "
             "'top': 10, 'uses': 999, 'rate': 0.1, 'memories': ['m'], 'broader_than': []}]); "
             "print(json.dumps({'ok': ruledisc.auto_scale_ok(), 'skipped': r['skipped'], 'added': len(r['added'])}))")
    o = child(probe, BRAIN_JUDGE_PROVIDER="openai", BRAIN_RULE_AUTO_MIN=None)
    check("another judge: auto-approval stands down, and says why",
          o.get("ok") is False and o.get("added") == 0 and "BRAIN_RULE_AUTO_MIN" in json.dumps(o), str(o)[:140])
    check("a bar a human names opens it",
          child(probe, BRAIN_JUDGE_PROVIDER="openai", BRAIN_RULE_AUTO_MIN="9").get("ok") is True)
    check("Gemini, the judge it was measured on, is unchanged",
          child(probe, BRAIN_JUDGE_PROVIDER="gemini", BRAIN_RULE_AUTO_MIN=None).get("ok") is True)

    print("\n★④ staleness: the host's types, then the person's★")
    hosts.pin("claude-code")
    search._STALE_CACHE.clear()
    check("Claude Code's own types", search.stale_limit("project") == 21 and search.stale_limit("user") == 360)
    check("a kind nobody described takes the default", search.stale_limit("decision") == search.STALE_DAYS_DEFAULT)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [], "stale_days": {"decision": 30, "project": 45}}, fh)
    check("a person's config names their own kinds and wins",
          search.stale_limit("decision") == 30 and search.stale_limit("project") == 45)
    hosts.pin("generic")
    check("⛔ a host with no types of its own does not inherit Claude Code's",
          search.stale_limit("feedback") == search.STALE_DAYS_DEFAULT)
    hosts.pin("claude-code")

    print("\n★⑤ the deploy margin is counted in hits★")
    for n in (30, 57, 200):
        m = calibrate.deploy_margin(n)
        check("n=%d: one lost hit passes, two block" % n, 1.0 / n < m < 2.0 / n, "%.4f" % m)
    check("⛔ control: the old 0.02 blocked a single lost hit on a 30-item sample", 1.0 / 30 > 0.02)

    print("\n★⑥ the relative-rule floor follows the threshold★")
    r = [row(7.6, 0.9)]
    check("at the threshold it was measured on (9.46), a 7.6 short query is rescued", hook.select(r, 9.46) != [])
    check("on a corpus calibrated to 20, the same 7.6 is noise", hook.select(r, 20.0) == [])
    check("⛔ control: an absolute 7.5 floor would have fired at 20 too",
          [x for x in r if x["score"] >= 7.5 and x["ratio"] >= hook.REL_RATIO] != [])

    print("\n★⑦ short is the person's own shortest 30%★")
    terse = ["x" * n for n in (14, 15, 16, 18, 20, 22, 25, 30, 35, 40)]
    wordy = ["x" * n for n in (30, 40, 55, 60, 70, 80, 90, 100, 120, 150)]
    lt, ht = evalinit.short_band(terse)
    lw, hw = evalinit.short_band(wordy)
    check("someone terse gets a tight band, someone wordy a wider one", ht < hw, "%d vs %d" % (ht, hw))
    check("the floor is the hook's own", lt == lw == hook.MIN_PROMPT_CHARS)
    check("no prompts → an empty band, nothing invented", evalinit.short_band([])[1] < evalinit.short_band([])[0])

    print("\n★⑧ method settings name themselves and can be overridden★")
    probe = "import json; from brain import calibrate as c; print(json.dumps([c.MARGIN, list(c.FIRE_RATE_OK)]))"
    d = child(probe)
    check("defaults are the reference corpus's", d == [1.35, [15.0, 75.0]], str(d))
    d = child(probe, BRAIN_CALIB_MARGIN="1.5", BRAIN_FIRE_RATE_OK="20,85")
    check("a person can set both", d == [1.5, [20.0, 85.0]], str(d))
    d = child(probe, BRAIN_FIRE_RATE_OK="nonsense")
    check("a malformed band falls back instead of breaking", d == [1.35, [15.0, 75.0]], str(d))

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAILS), " · ".join(FAILS)) if FAILS
          else "✅ all passed — every number has an owner")
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for k, v in _SAVED.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
