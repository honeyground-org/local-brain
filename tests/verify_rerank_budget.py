"""Judge daily-budget check — ★there were two walls and only one was known★.

## Why this is needed (measured 2026-08-26)

`rerank.py` only knew the per-minute limit (15/min). But the provider has two walls:

    GenerateRequestsPerMinutePerProjectPerModel-FreeTier =    15/min
    GenerateRequestsPerDayPerProjectPerModel-FreeTier    = ★500/day★

A 429 from the daily wall was being mashed into None ★exactly like a network error★, so on an
afternoon past the limit, two-stage search fell entirely silent ★with no trace in the log or the checks★.
5 of 6 calls meant to measure reproducibility that afternoon died that way, and finding the cause
required opening the raw 429 body by hand.

⛔ ★A silent failure is the most expensive kind★ — so this check does not ask "is there code that
   handles the wall". It intercepts urlopen and counts ★how many calls actually went out★. If it is
   not 0 behind the wall, the hook is paying a round trip and a 4-second wait for nothing on every prompt.

⛔ ★This check never touches real data★ — `BRAIN_HOME` is pointed at a temp folder.
   A check in this same repository once deleted the real `rules.json`
   (lesson_a_test_that_deleted_the_data_it_was_testing).
"""
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

# ⛔ ★Before★ the import — brain.store reads BRAIN_HOME
_TMP = tempfile.mkdtemp(prefix="brain-budget-")
os.environ["BRAIN_HOME"] = _TMP
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
# The walls below are ★Gemini's★ (§engines.JUDGE_RPD) — say which judge this is, instead of inheriting
# whatever this machine's config happens to choose.
os.environ["BRAIN_JUDGE_PROVIDER"] = "gemini"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import i18n, rerank, store                     # noqa: E402

FAIL = []


def check(ok, label, detail=""):
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAIL.append(label)
    return ok


def head(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


CANDS = [{"name": "some_memory", "description": "a description", "body": "a body"}]

# ── mimic the provider's response (the measured body verbatim · 2026-08-26) ──
DAILY_429 = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                       "message": "quota", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
         "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
         "quotaValue": "500"}]},
    # ⛔ ★The provider says wait 32 seconds — for the daily wall that is a lie★
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "32s"}]}}

MINUTE_429 = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                        "message": "quota", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
         "quotaValue": "15"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "59s"}]}}

OK_BODY = {"candidates": [{"content": {"parts": [{"text": '[{"i":0,"s":9}]'}]}}]}


class Fake:
    """Intercepts urlopen — ★the number of calls made★ is this check's ruler."""

    def __init__(self):
        self.calls = 0
        self.real = urllib.request.urlopen
        self.mode = "ok"

    def __enter__(self):
        urllib.request.urlopen = self._call
        return self

    def __exit__(self, *a):
        urllib.request.urlopen = self.real

    def _call(self, req, timeout=None):
        self.calls += 1
        if self.mode == "ok":
            return _Resp(OK_BODY)
        payload = DAILY_429 if self.mode == "daily" else MINUTE_429
        raise urllib.error.HTTPError(
            "http://x", 429, "Too Many Requests", {},
            io.BytesIO(json.dumps(payload).encode("utf-8")))


class _Resp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fresh_db():
    db = store.connect()
    with db:
        for k in ("rerank_day", "rerank_used", "rerank_daily_hit", "rerank_rpd"):
            db.execute("DELETE FROM meta WHERE k=?", (k,))
    return db


def call(db, mode, reserve=0, cache=False, query="a query", cands=None):
    """Calls once and returns ★how many actually went out★.

    ⛔ The budget section is measured ★with the cache off★. The cache sits ahead of the budget (rightly —
       a cache hit is free, so it must answer even behind the wall), so measured with it on the second
       call never even reaches the budget layer. ★One check measures one layer.★
    """
    rerank._LAST[0] = 0.0                                # keep the per-minute interval wait out of the check
    with Fake() as f:
        f.mode = mode
        out = rerank.score(query, cands or CANDS, reserve=reserve, db=db, cache=cache)
    return out, f.calls


def main():
    # ⛔ ★rerank's failure reasons are locale-aware★ (2026-09-08) — pin English, since these
    #    checks assert specific English wording. Restored at the end (never leaves the language changed).
    saved_env, saved_lang = os.environ.get("BRAIN_LANG"), i18n._lang
    os.environ["BRAIN_LANG"] = "en"
    i18n._lang = None
    i18n.lang(refresh=True)

    head("Judge daily budget — does it know ★before★ hitting the wall")
    print("temp BRAIN_HOME: %s" % _TMP)

    # ① counting — count what was sent
    db = fresh_db()
    out, n = call(db, "ok")
    b = rerank.budget(db)
    check(out == [9.0] and n == 1 and b["used"] == 1,
          "a successful call is counted as 1 in the budget", "used=%d left=%d" % (b["used"], b["left"]))

    # ② a daily 429 → records the wall + learns the limit
    db = fresh_db()
    out, n = call(db, "daily")
    b = rerank.budget(db)
    check(out is None and b["wall"] and b["left"] == 0,
          "a daily 429 is recorded as ★the wall★", "wall=%s left=%d" % (b["wall"], b["left"]))
    check(b["limit"] == 500, "the limit is learnt from the provider (quotaValue)", "limit=%d" % b["limit"])
    check("Pacific" in rerank.last_failure(),
          "the failure reason says ★the limit★", rerank.last_failure()[:60])

    # ③ ★nothing goes out at all behind the wall★ — this check's core point
    out, n = call(db, "ok")
    check(out is None and n == 0,
          "a call behind the wall is ★0 HTTP calls★ (no round trip or 4s wait is paid)", "%d requests went out" % n)

    # ④ a per-minute 429 does not kill the day
    db = fresh_db()
    out, n = call(db, "minute")
    b = rerank.budget(db)
    check(out is None and not b["wall"] and n == 1,
          "a per-minute 429 is ★not the wall★ (the day survives)", "wall=%s" % b["wall"])
    out, n = call(db, "ok")
    check(out == [9.0] and n == 1, "the next call still goes out after a per-minute 429", "%d requests went out" % n)

    # ⑤ the reserve — batch starves before interactive
    db = fresh_db()
    with db:
        store.set_meta(db, "rerank_day", rerank._pt_day())
        store.set_meta(db, "rerank_used", str(500 - 100))     # 100 left
    out, n = call(db, "ok", reserve=150)
    check(out is None and n == 0,
          "★batch does not call★ when the 100 left is < the 150 reserve", "%d requests went out" % n)
    check("interactive recall" in rerank.last_failure(),
          "the reason says ★the interactive share★", rerank.last_failure()[:60])
    out, n = call(db, "ok", reserve=0)
    check(out == [9.0] and n == 1,
          "★interactive (reserve 0) still calls★ at the same spot", "%d requests went out" % n)

    # ⑥ the day resets itself when it turns over (Pacific midnight)
    db = fresh_db()
    call(db, "daily")
    with db:
        store.set_meta(db, "rerank_day", "1999-01-01")
        store.set_meta(db, "rerank_daily_hit", "1999-01-01")
    b = rerank.budget(db)
    check(not b["wall"] and b["left"] == b["limit"],
          "the wall lifts on its own when the day turns over", "left=%d" % b["left"])

    # ⑦ the boundary is ★Pacific★ — not our own midnight
    import time as _t
    pt, kst = rerank._pt_day(), _t.strftime("%Y-%m-%d")
    check(len(pt) == 10 and pt[:2] == "20",
          "the day boundary is measured in Pacific time", "PT=%s · local=%s" % (pt, kst))

    # ⑧ the score cache — never asks the same thing twice
    head("Score cache — never asks the same thing twice (and measurement cannot read the cache)")
    db = fresh_db()
    rerank.cache_clear(db)
    out1, n1 = call(db, "ok", cache=True)
    out2, n2 = call(db, "ok", cache=True)
    check(out1 == out2 == [9.0] and n1 == 1 and n2 == 0,
          "the same (query · candidates · model) is ★0 HTTP calls★ from the second time", "%d → %d requests went out" % (n1, n2))

    st = rerank.cache_stats(db)
    check(st["rows"] == 1 and st["hits"] == 1, "a hit is recorded",
          "%d rows · %d hits" % (st["rows"], st["hits"]))

    # ⛔⛔ this check is the reason this cache exists — candidates interfere with each other, so ★a changed list is a different question★
    other = CANDS + [{"name": "another", "description": "d", "body": "b"}]
    out3, n3 = call(db, "ok", cache=True, cands=other)
    check(n3 == 1, "★the cache misses★ when the candidate list changes (a per-pair cache would be wrong)",
          "%d requests went out" % n3)

    # ⛔⛔ ★editing a memory body does not let the old score survive★ — the name stays the same while
    #     the content changes. This repository has already been badly burnt by the same shape (the index
    #     missed an edit, and the edited memory was recalled with its old content all session).
    edited = [dict(CANDS[0], body="the body was edited")]
    _, n4 = call(db, "ok", cache=True, cands=edited)
    check(n4 == 1, "★an edited candidate body invalidates the cache★ (keying on the name alone is wrong)",
          "%d requests went out" % n4)
    _, n5 = call(db, "ok", cache=True, cands=edited)
    check(n5 == 0, "the edited body is answered by the cache from the second time on", "%d requests went out" % n5)

    # ★editing the prompt does not let the old score survive★
    rerank._LAST[0] = 0.0
    with Fake() as f:
        f.mode = "ok"
        rerank.score("a query", CANDS, prompt="a different prompt %s %s", db=db, cache=True)
        check(f.calls == 1, "the cache is invalidated when the prompt changes", "%d requests went out" % f.calls)

    # ⛔⛔ a reproducibility measurement cannot deceive itself
    before = rerank.cache_stats(db)["hits"]
    _, nA = call(db, "ok", cache=False)
    _, nB = call(db, "ok", cache=False)
    check(nA == 1 and nB == 1 and rerank.cache_stats(db)["hits"] == before,
          "★cache=False actually goes out every time★ (a measurement cannot find itself)",
          "%d + %d requests went out" % (nA, nB))

    # ★the cache still answers behind the wall★ — a hit is free, no reason to block it
    db2 = fresh_db()
    call(db2, "ok", cache=True)                          # fill it
    call(db2, "daily", cache=False)                      # raise the wall
    check(rerank.budget(db2)["wall"], "the wall is up")
    out, n = call(db2, "ok", cache=True)
    check(out == [9.0] and n == 0,
          "★the cache still answers behind the wall★ (a hit is free)", "%d requests went out" % n)

    # ⑨ is the threshold placed in ★a valley, not on a peak★ (2026-08-27)
    head("Threshold placement — standing on a peak, a ±1 wobble flips the decision")
    # the measured distribution exactly as it was (180 observations) — the judge ★never once gives a 6★
    H = {0: 33, 1: 7, 2: 36, 3: 8, 4: 2, 5: 4, 6: 0, 7: 9, 8: 30, 9: 12, 10: 39}
    t, why = rerank._place(H, 0.0)
    check(t == 6.0, "with no constraint it chooses ★the valley (6)★", "threshold %.0f · %s" % (t, why))
    check(H[8] + H[7] > H[6] + H[5],
          "the 8.0 in use now has more boundary mass than the valley",
          "8 → %d · 6 → %d" % (H[8] + H[7], H[6] + H[5]))

    t, why = rerank._place(H, 7.0)
    check(t == 8.0 and "clamped to the noise floor" in why,
          "⛔ the noise floor wins — the valley is given up and ★that fact is stated★", why[:70])

    t, why = rerank._place(H, 10.0)
    check(t == 10.0 and "stale control group" in why,
          "a control group at full marks says ★a stale control group, not the threshold★", why[:60])

    flat = {i: 5 for i in range(11)}
    t, why = rerank._place(flat, 0.0)
    check(0.0 <= t <= 10.0, "still produces a value on a flat distribution (no valley does not kill it)",
          "threshold %.0f" % t)

    # ── ★the budget window★ — the day boundary and scheduled spending (added 2026-09-01) ─────────────
    head("Budget window — ⛔ the day boundary is ★Pacific midnight★, not our own")
    import time as _t
    from datetime import datetime as _dt

    # On 09-01 the plan said *"measure before 09:00"*. That was a window computed against our own
    # midnight, ★underestimating it by 14 hours★. The real boundary is 16:00 in Korea.
    when, secs = rerank.next_reset()
    check(0 < secs <= 24 * 3600 + 60, "the next reset is within 24 hours", "%.1fh from now" % (secs / 3600.0))
    check(rerank._pt_day(_t.time() + secs + 60) != rerank._pt_day(),
          "★the PT day actually changes★ once the reset time passes",
          "%s → %s" % (rerank._pt_day(), rerank._pt_day(_t.time() + secs + 60)))
    check(rerank._pt_day(_t.time() + secs - 60) == rerank._pt_day(),
          "★just before★ the reset it is still the same PT day (the boundary is not caught one step early)")
    # measures that it ★differs★ from what our own midnight would compute — that mistake was the cause
    ours = _dt.fromtimestamp(_t.time()).replace(hour=0, minute=0, second=0)
    ours_secs = (ours.timestamp() + 86400) - _t.time()
    check(abs(ours_secs - secs) > 3600,
          "★differs from our own midnight★ — this gap is what set the window wrong by half a day on 09-01",
          "our midnight in %.1fh ↔ Pacific midnight in %.1fh" % (ours_secs / 3600.0, secs / 3600.0))

    due = rerank.scheduled_spend()
    booked = sum(d["cost"] for d in due)
    check(all(d["at"].timestamp() < _t.time() + secs + 1 for d in due),
          "scheduled spending is counted ★only within this window★", "%d entries · %d calls" % (len(due), booked))
    check(booked >= 0, "★it reports scheduled spending too, not only the balance★ — the same wallet is shared",
          "%d calls reserved (%s)" % (booked, ", ".join(d["job"] for d in due) or "none"))

    head("Result")
    os.environ.pop("BRAIN_LANG", None)
    if saved_env:
        os.environ["BRAIN_LANG"] = saved_env
    i18n._lang = saved_lang
    if FAIL:
        print("❌ %d fell short: %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — the judge knows the daily wall, and nothing is sent behind it")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)          # only removes the temp folder (not real data)
    sys.exit(code)
