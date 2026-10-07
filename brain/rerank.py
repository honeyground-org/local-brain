"""The judge (reranker) — ★finding and choosing are different jobs★.

## Why this is needed (the measurement said it first, and the industry says the same)

Our meaning-search ★does find★. Measured 2026-08-25: over 8 questions where the lexical path was
silent, the right answer was 1st in 2 · top-3 in 4 · top-10 in 6. But those answers' ratios were
1.03~1.10 while the control group (real talk needing no memory) peaked at 1.12 — ★the right answer
lies beneath the noise ceiling.★ So a ratio gate kept only 3 of 34, and rescued ★0★ of the silent 8.

The cause is not the threshold but the ★instrument★. Cosine encodes query and document ★separately★
(bi-encoder). That value is not a ruler for "is this document the answer to this question" but for
"are the two in the same neighbourhood". It cannot see negation, qualification, or a subtle topic miss.

The standard prescription is ★two stages★ — draw candidates cheaply (bi-encoder), then let ★a model that
reads query and candidate together★ choose (cross-encoder/reranker). A reranker score does not shift its
scale per query, so ★a threshold can be placed on it★. Anthropic's contextual-retrieval measurement runs
the same order: contextual embeddings −35% failures → plus contextual BM25 −49% → ★with reranking −67%★.

## Our implementation

The Gemini API has no dedicated rerank model (50 models checked, 0 in the rank family). So a cheap judging
model is asked to ★score every candidate 0~10 in a single call★. It stands in the same place as a cross
encoder, and shares the property that the score's scale does not shift with the query.

⛔ This ★does not produce answers★ — it only chooses among candidates. Letting a generative model invent
   a memory would destroy the brain's purpose (being right).
⛔ It is called only when the lexical path is silent — where words already answered, precision is already
   high, and a call costs money and latency (§hook).
"""
from __future__ import annotations

from brain import evalinit as _ei  # ★gold judging lives in exactly one place★
from brain import i18n

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Sequence

# ★Which model judges is the person's choice★ (§engines — `brain engines`). This is only what is in
#   use right now, read once, for the places that print it.
from brain import engines as _engines                     # noqa: E402
MODEL = _engines.choice("judge")["model"]
TIMEOUT = float(os.environ.get("BRAIN_RERANK_TIMEOUT", "25") or 25)
# ★20 candidates★ — Anthropic's contextual-retrieval recommendation (top-20 beats top-5 and top-10).
# Our measurement pointed the same way: several right answers looked like 0 only because they sat outside top-8.
MAX_CAND = int(os.environ.get("BRAIN_RERANK_MAX", "20") or 20)
# ⛔ The limit we learnt by asking the provider (2026-08-25): GenerateRequestsPerMinutePerProjectPerModel
#    -FreeTier = ★15/min★ · retryDelay 59s. Firing 150/min gets 56 of 91 refused, and counting those
#    failures as 0 produces the false conclusion "the reranker is useless" (we came within an inch of it).
#    ⛔ That number is ★one provider's free tier★ — other judges are not paced unless the person says so.
RPM = int(os.environ.get("BRAIN_RERANK_RPM", "")
          or _engines.JUDGE_RPM.get(_engines.choice("judge")["provider"], 0))
# ⛔⛔ ★There were two walls and we knew only one★ (measured 2026-08-26 · the provider's own words)
#       GenerateRequestsPerMinutePerProjectPerModel-FreeTier =    15/min
#       GenerateRequestsPerDayPerProjectPerModel-FreeTier    = ★500/day★  ← this layer was missing
#    Respect only the per-minute wall and the 500th call of the day hits the other one. But this file had
#    no concept of `PerDay`, so it mashed that 429 into None ★exactly like a network error★. The result:
#    on an afternoon past the limit, two-stage search falls entirely silent ★with no trace in the log or the checks★.
#    (It was in exactly that state on the afternoon of 2026-08-26, and 5 of 6 calls meant to measure reproducibility died.)
#    · A different unit cannot be blocked — an absent value does not mean unlimited.
RPD = int(os.environ.get("BRAIN_RERANK_RPD", "")
          or _engines.JUDGE_RPD.get(_engines.choice("judge")["provider"], 0))   # 0 = no wall we know of
# ★Interactive comes before batch★ — recall (the hook) is ★where a human is waiting★, while rule discovery
# (the cron) is a relay that picks up tomorrow what it could not finish today. So batch stops ★leaving★ this much.
# ⛔ This is ★not★ a reduction of the total — all 500 a day are still spent. What it decides is ★the order★
#    (who starves first). Because batch runs first at 09:30, without this reserve the interactive path
#    starves every morning.
RESERVE = int(os.environ.get("BRAIN_RERANK_RESERVE", "150") or 150)
_LAST = [0.0]
_LAST_FAIL = [""]           # ★why it failed★ — a bare None cannot say limit from network
EXCERPT = 420

MIN_SCORE_FALLBACK = 99.0     # before calibration, ★add nothing★ (an unreachable value on the 0~10 scale)


def endpoint_host() -> str:
    """The host the judge talks to — the same contract as `vectors.endpoint_host` (§jobs._await_network)."""
    return _engines.endpoint_host("judge")

# ★The judging question★ — English, for any language (2026-10-06). It was written in Korean; measured
# against the Korean original on the same 91 labelled questions and the same candidates (gemini-flash-lite):
# hits at 8.0 the same (11), control false fires 24%→26%, label-free 12%→20%, and the marks moved up
# (English gives 10s). Same judge, different scale — which is why the threshold is stamped (§scale_id).
_PROMPT = """You are a search judge. Read the person's words together with the candidate memories, and
score 0~10 how far each candidate ★actually answers★ those words.

Scoring
- 10: this memory is the direct answer to the question or situation, or a rule that must be known
- 6~9: related and helpful
- 3~5: near the same topic, but not the answer
- 0~2: unrelated. ★If the person's words need no memory — small talk, or an instruction such as
  "commit it" or "do it again" — give every candidate 0~2.★

The words and the memories may be in any language, and not necessarily the same one — judge the
meaning, not the wording.
⛔ Do not invent an answer. Do not mention anything outside the candidates.
Output a JSON array only: [{"i": 0, "s": 7}, {"i": 1, "s": 2}, ...]

The person's words:
%s

Candidates:
%s"""


def available() -> bool:
    """Is a judge chosen, and can it be reached (a key where one is needed)."""
    return _engines.available("judge")


def scale_id() -> str:
    """★The scale a judge score is on★ — which judge, asked which question.

    ⛔ A threshold measured on one judge is not a threshold on another (2026-10-06): the same 91 labelled
       questions scored by the same model under the Korean and the English wording of one prompt put
       the controls on different marks (the English one gave 10s the Korean one never gave). A different
       model or provider moves it further. So the calibration is stamped with this, and read only back.
    """
    return _engines.fingerprint(_engines.judge_id(), _PROMPT)


def min_score(db=None) -> float:
    """Rerank score threshold — ⛔ never nailed into the code. Calibrated against a control group into meta.

    ⛔ ★Only on the scale it was measured on★ — a value stamped for another judge (or for none: written
       before the stamp existed) is treated as never calibrated, and the judge adds nothing until
       `brain vec calibrate` measures it again. Silence, not another judge's number.
    """
    from brain import store, vectors
    if os.environ.get("BRAIN_RERANK_MIN"):
        return vectors._meta_float(db, "rerank_min_score", "BRAIN_RERANK_MIN", MIN_SCORE_FALLBACK)
    own = db is None
    try:
        dbx = db or store.connect()
        stamped = store.get_meta(dbx, "rerank_calibrated_for", "")
        if own:
            dbx.close()
    except Exception:                                    # noqa: BLE001
        return MIN_SCORE_FALLBACK
    if stamped != scale_id():
        return MIN_SCORE_FALLBACK
    return vectors._meta_float(db, "rerank_min_score", "BRAIN_RERANK_MIN",
                               MIN_SCORE_FALLBACK)


def last_failure() -> str:
    """The ★reason★ for the last failure. `score()` still contracts to None, but the checks and the cron
    must tell 'the limit' from 'the network' from 'the reserve' — all three have different prescriptions."""
    return _LAST_FAIL[0]


def _pt_day(now: float = 0.0) -> str:
    """The daily limit's boundary is ★Pacific midnight★ — not ours.

    Measured in Korean time the day is off, so a wall already lifted looks 'still blocked' or the reverse
    (a 16~17 hour error). Only the standard library `zoneinfo` is used.
    """
    from datetime import datetime
    t = now or time.time()
    return datetime.fromtimestamp(t, _pacific(t)).strftime("%Y-%m-%d")


def _pacific(t: float):
    """The Pacific time zone at instant `t` · `zoneinfo` when it can answer, otherwise the US rule.

    ⛔ `zoneinfo` is Python 3.9+, and on Windows it also needs the `tzdata` package — without either,
       `brain dashboard` crashed (CI on Python 3.8, 2026-10-07). The fallback is the rule itself: daylight
       time from the second Sunday of March, 02:00 PST, to the first Sunday of November, 02:00 PDT. It is
       a fixed offset for that instant, so a span across a switch can be an hour off twice a year.
    """
    from datetime import datetime, timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:                                    # noqa: BLE001 — ImportError or no tz database
        pass
    u = datetime.fromtimestamp(t, timezone.utc)

    def sunday(month: int, nth: int):
        first = datetime(u.year, month, 1, tzinfo=timezone.utc)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))

    summer = sunday(3, 2) + timedelta(hours=10) <= u < sunday(11, 1) + timedelta(hours=9)
    return timezone(timedelta(hours=-7 if summer else -8))


def next_reset(now: float = 0.0):
    """When the next reset (Pacific midnight) falls ★in our own clock★ · seconds remaining.

    ⛔ Why this is needed (half a day was spent believing the wrong thing on 2026-09-01): the plan said
    *"the measurement must happen before 09:00"*. Wrong — that value mistook the day boundary for
    ★our midnight★. The real boundary is Pacific midnight, which is ★16:00 in Korea★, so the empty
    window is not a few small hours but ★17.5 hours from 16:00 until the next cron★.

    ★The tool did not say this, so a human computed it by hand and got it wrong.★ So now it says it.
    """
    from datetime import datetime, timedelta
    t = now or time.time()
    pt = _pacific(t)
    here = datetime.fromtimestamp(t).astimezone()
    nxt = (datetime.fromtimestamp(t, pt) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    local = nxt.astimezone(here.tzinfo)
    return local, max(0.0, nxt.timestamp() - t)


def scheduled_spend(now: float = 0.0) -> list:
    """★Who besides me is going to spend inside this budget window.★

    ⛔ Its absence threw the plan off twice on 2026-09-01. The budget showed only 'what is left', while in
    fact ★tomorrow morning's cron had 300 calls reserved★. So 'the remaining 466' was not mine to spend.
    **Self-improvement and self-measurement share one wallet** — and then the wallet
    ★must not report only the balance; it must report the scheduled spending too.★
    """
    import os as _os
    from datetime import datetime, timedelta
    t = now or time.time()
    _when, secs = next_reset(t)
    horizon = t + secs
    out = []
    try:
        from brain import health
        jobs = health.schedules()["jobs"]
    except Exception:                                    # noqa: BLE001
        return out
    costs = {"brain-rules-daily": int(_os.environ.get("BRAIN_RULES_DAILY_LIMIT", "300") or 300)}
    for j in jobs:
        cost = costs.get(j.get("job"))
        if not cost or not j.get("at"):
            continue                                     # skip jobs that do not use the judge
        h, m = j["at"][0], j["at"][1]
        nxt = datetime.fromtimestamp(t).replace(hour=h, minute=m, second=0, microsecond=0)
        if nxt.timestamp() <= t:
            nxt += timedelta(days=1)
        if nxt.timestamp() < horizon:
            out.append({"job": j["job"], "at": nxt, "cost": cost})
    return out


def _open_db():
    from brain import store
    return store.connect()


def budget(db=None) -> dict:
    """How much was spent today (Pacific) and how much is left. ⛔ The point is knowing ★before★ the 429.

    It returns three things — `used` (what we counted) · `wall` (the provider actually blocked us) ·
    `left`. ★If `wall` is true it is 0 no matter what `used` says★: our counter can undercount when
    another process or another tool uses the same project key, and only the provider's 429 is
    ★measured inability★.
    """
    own = db is None
    db = db or _open_db()
    try:
        from brain import store
        day = _pt_day()
        limit = int(float(store.get_meta(db, "rerank_rpd", "") or RPD))
        # ⛔ `limit` 0 = ★no daily wall we know of★ (§engines.JUDGE_RPD) — `left` is then None, not a number
        #    we made up; a wall actually hit still answers 0.
        if store.get_meta(db, "rerank_day", "") != day:
            return {"day": day, "used": 0, "limit": limit, "left": limit if limit > 0 else None,
                    "wall": False}
        used = int(float(store.get_meta(db, "rerank_used", "0") or 0))
        wall = store.get_meta(db, "rerank_daily_hit", "") == day
        return {"day": day, "used": used, "limit": limit,
                "left": 0 if wall else (max(0, limit - used) if limit > 0 else None), "wall": wall}
    finally:
        if own:
            db.close()


def _cache_key(model: str, sent_text: str) -> str:
    """⛔⛔ ★The key is the very body that will be sent★ — not a list of names.

    It was nearly wrong twice.

    ① ★It must not be cached per (query, document) pair.★ The reason this cache exists at all is the
       measurement that ★candidates interfere with each other★ (same query, same candidates: give 20 and
       the top score wobbles, give 5 and it is stable). Cached per pair, a score given in one list gets
       reused in a different list, and the cache erases exactly that interference — you no longer know what you measure.

    ② ★Nor may it hold only candidate 'names'.★ Edit a memory's body and the name stays, so the edited
       memory is judged with the old score — a shape this repository has already been badly burnt by (the
       index missed an edit entirely, and the edited memory was recalled with its old content all session).

    So ★the whole string that actually went into the prompt★ (query + candidate names + descriptions +
    excerpts + prompt template) is hashed together with the model. If the content changes the answer may
    change, so the cache must change too. That is why this key is right even though it looks expensive.
    """
    h = hashlib.sha256()
    h.update(model.encode("utf-8", "replace"))
    h.update(b"\x1f")
    h.update(sent_text.encode("utf-8", "replace"))
    return h.hexdigest()


def _cache_ensure(db) -> None:
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS rerank_cache("
                   " k TEXT PRIMARY KEY, model TEXT NOT NULL, n INTEGER NOT NULL,"
                   " scores TEXT NOT NULL, at TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0)")


def cache_get(db, key: str) -> Optional[List[float]]:
    try:
        _cache_ensure(db)
        r = db.execute("SELECT scores FROM rerank_cache WHERE k=?", (key,)).fetchone()
    except Exception:                                    # noqa: BLE001
        return None                                      # a broken cache must not block recall
    if not r:
        return None
    try:
        out = [float(x) for x in json.loads(r["scores"])]
    except (TypeError, ValueError):
        return None
    try:
        with db:
            db.execute("UPDATE rerank_cache SET hits=hits+1 WHERE k=?", (key,))
    except Exception:                                    # noqa: BLE001
        pass
    return out


def cache_put(db, key: str, model: str, scores: Sequence[float]) -> None:
    try:
        _cache_ensure(db)
        with db:
            db.execute("INSERT OR REPLACE INTO rerank_cache(k, model, n, scores, at, hits)"
                       " VALUES(?,?,?,?,?,COALESCE((SELECT hits FROM rerank_cache WHERE k=?),0))",
                       (key, model, len(scores), json.dumps(list(scores)),
                        time.strftime("%Y-%m-%d"), key))
    except Exception:                                    # noqa: BLE001
        pass


def cache_stats(db=None) -> dict:
    own = db is None
    db = db or _open_db()
    try:
        _cache_ensure(db)
        r = db.execute("SELECT COUNT(*) c, COALESCE(SUM(hits),0) h FROM rerank_cache").fetchone()
        return {"rows": r["c"], "hits": r["h"]}
    except Exception:                                    # noqa: BLE001
        return {"rows": 0, "hits": 0}
    finally:
        if own:
            db.close()


def cache_clear(db=None) -> int:
    """Change the model or the prompt and the key changes, so there is normally nothing to clear. Manual only."""
    own = db is None
    db = db or _open_db()
    try:
        _cache_ensure(db)
        n = db.execute("SELECT COUNT(*) c FROM rerank_cache").fetchone()["c"]
        with db:
            db.execute("DELETE FROM rerank_cache")
        return n
    finally:
        if own:
            db.close()


def _note_call(db) -> None:
    """Count a request as sent. ⛔ Count it even on failure — the provider counts ★what was sent★, not what succeeded."""
    from brain import store
    day = _pt_day()
    with db:
        if store.get_meta(db, "rerank_day", "") != day:
            store.set_meta(db, "rerank_day", day)
            store.set_meta(db, "rerank_used", "0")
            store.set_meta(db, "rerank_daily_hit", "")
        used = int(float(store.get_meta(db, "rerank_used", "0") or 0))
        store.set_meta(db, "rerank_used", str(used + 1))


def _note_wall(db, limit: float) -> None:
    """The provider told us about the daily wall — ★no more calls★ today."""
    from brain import store
    with db:
        store.set_meta(db, "rerank_daily_hit", _pt_day())
        if limit:                                        # the ceiling is learnt from the provider
            store.set_meta(db, "rerank_rpd", str(int(limit)))


# ★Ask in small groups of candidates★ — 0 turns it off (current: 20 in one list).
#
# ⛔ Why this knob exists (measured 2026-08-31 · 8 samples · 2 runs each)
#
#     20 at once     hits 4·5 / 8   ★same decision 62%★   (the same input splits the answer)
#     4 groups of 5  hits 5·5 / 8   ★same decision 100%★
#
#    What was observed on 2026-08-25 (20 → fires 1/5, 5 → 4/5) reproduced ★on the lexical path too★.
#    The grounds for "top-20" assumed ★a dedicated reranker (cross-encoder)★, while we hand a general
#    LLM one list — the candidates interfere with each other.
#
# ⛔ But it is ★still not on by default★: it quadruples the calls and so presses the daily limit (§RPD)
#    four times harder, and no budget was left that day to measure that effect. ★Only what is verified is turned on.★
#    How to turn it on: `BRAIN_RERANK_CHUNK=5` · decide the default after measuring the full sample.
CHUNK = int(os.environ.get("BRAIN_RERANK_CHUNK", "0") or 0)


def score_chunked(query: str, candidates: Sequence[dict], size: int = 0,
                  db=None, cache: bool = True) -> Optional[List[float]]:
    """Use it where `score` is used, but ★ask in groups of `size`★ candidates.

    What comes back is ★a score list in the original order★ (the caller need not change).
    ⛔ If even one group fails, that slot is ★None★, not 0.0 — counting failure as a low score
       mixes up "there is no answer" with "we could not ask".
    """
    n = size or CHUNK
    if n <= 0 or len(candidates) <= n:
        return score(query, candidates, db=db, cache=cache)
    out: List[Optional[float]] = []
    for i in range(0, len(candidates), n):
        part = list(candidates[i:i + n])
        sc = score(query, part, db=db, cache=cache)
        if sc is None:
            return None                              # ★a failed call is not silence★
        # ⛔ The judge may answer with fewer than the candidates — take the shorter side, rest None
        for j in range(len(part)):
            out.append(sc[j] if j < len(sc) else None)
    return [(0.0 if v is None else v) for v in out]


def score(query: str, candidates: Sequence[dict],
          model: str = "", prompt: str = "", reserve: int = 0,
          db=None, cache: bool = True) -> Optional[List[float]]:
    """0~10 per candidate. ⛔ Failure is ★None★ — it must be distinguishable from a score of 0.

    ★The prompt can be swapped★ (2026-08-26) — behaviour-layer rule discovery (§ruledisc) asks the same
    judge ★a different question★. Writing HTTP, the per-minute limit and the "failure is None" contract
    twice would let them drift apart in silence, so the only thing that differs is the prompt.

    The first version returned `[]` on failure, and the evaluation script ★counted that as a score of 0★.
    That nearly produced the false conclusion "29 of 34 right answers scored 0" (in reality the call
    succeeded and the right answer was outside the candidates). Failure and irrelevance are different facts.
    """
    from brain import vectors
    cands = list(candidates)[:MAX_CAND]
    if not cands:
        return []
    # ★Know before hitting the wall★ — one 429 wastes a round trip and a 4-second wait. And going on
    # calling after the wall makes the hook pay that cost on every prompt (a human is waiting).
    own_db = db is None
    db = db or _open_db()
    try:
        # ★Never ask the same thing twice★ — the free tier is 500 a day, and the checks re-ask the
        # same few dozen queries every time. The cache makes that free.
        #
        # ⛔⛔ But ★a place that measures reproducibility must not read the cache.★ A measurement that
        #    repeats one input R times to gauge wobble will, reading the cache, get the same value all R
        #    times and reach the false conclusion ★"perfectly reproducible"★ — the measurement finds itself.
        #    This repository has been burnt by that same trap twice (a calibration probe found its own
        #    sentence quoted in a document, and the scorecard measured the gain two-stage with the cost off).
        #    So the `cache=False` door is kept open, and measurement scripts must use it.
        # ★Build the body to be sent first★ — because the cache key is that body (§_cache_key).
        #   It is pure string work, so it spends neither network nor budget.
        from . import privacy
        lines = []
        for i, c in enumerate(cands):
            _body = (c.get("excerpt") or c.get("body") or "")[:EXCERPT]
            _body, _ = privacy.scrub(_body)
            _desc, _ = privacy.scrub((c.get("description") or "")[:160])
            lines.append("[%d] %s — %s\n%s" % (i, c.get("name", "?"), _desc, _body))
        safe_query, _ = privacy.scrub(query[:600])      # a query can carry secrets too
        sent_text = (prompt or _PROMPT) % (safe_query, "\n\n".join(lines))

        key = ""
        the_model = model or _engines.choice("judge")["model"]
        if cache:
            # ⛔ ★which judge★ is part of the key — another engine's score is not this one's.
            #    Gemini keeps its bare model name so the cache it already built stays readable.
            tag = (the_model if _engines.choice("judge")["provider"] == "gemini"
                   else _engines.judge_id(the_model))
            key = _cache_key(tag, sent_text)
            hit = cache_get(db, key)
            if hit is not None and len(hit) == len(cands):
                _LAST_FAIL[0] = ""
                return hit
        b = budget(db)
        if b["wall"]:
            _LAST_FAIL[0] = i18n.t("rerank.fail.daily_wall",
                                   day=b["day"], used=b["used"], limit=b["limit"])
            return None
        if reserve and b["left"] is not None and b["left"] <= reserve:
            _LAST_FAIL[0] = i18n.t("rerank.fail.reserve", reserve=reserve, left=b["left"])
            return None
        # ⛔ ★This is an outbound door — mask the secrets before sending★ (§privacy). Real credentials
        #    went out through this path (2026-08-26, a 420-character excerpt of a production-DB memory).
        if RPM:
            gap = 60.0 / RPM
            wait = gap - (time.time() - _LAST[0])
            if _LAST[0] and wait > 0:
                time.sleep(wait)
        _LAST[0] = time.time()
        _note_call(db)                                   # ⛔ count what was sent (before success)
        try:
            text = _engines.judge_text(sent_text, timeout=TIMEOUT, model=model)
        except _engines.JudgeError as exc:
            if exc.daily:                                # ★one parser only★ (§vectors.parse_quota)
                _note_wall(db, exc.limit)
                _LAST_FAIL[0] = i18n.t("rerank.fail.daily_lie",
                                       limit=int(exc.limit or RPD), retry=int(exc.retry_after))
            else:
                _LAST_FAIL[0] = "HTTP %s %s %s" % (exc.status, exc.code, exc.message[:120])
            return None
        except vectors.NoKey as exc:
            _LAST_FAIL[0] = str(exc)[:160]
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            _LAST_FAIL[0] = i18n.t("rerank.fail.network",
                                   kind=type(exc).__name__, detail=str(exc)[:120])
            return None
        _LAST_FAIL[0] = ""                               # ★success erases the previous failure★
        out = _parse(text, len(cands))
        if out is None:
            return None
        # ⛔ The cache holds ★only successes★ — holding a failure kills that slot forever.
        if key:
            cache_put(db, key, the_model, out)
        return out
    finally:
        if own_db:
            db.close()


def _parse(text: str, n: int) -> Optional[List[float]]:
    """Pull per-candidate scores out of the judge's text. ⛔ If it cannot, the answer is ★None★ (not an array of zeros)."""
    if not isinstance(text, str) or not text.strip():
        _LAST_FAIL[0] = i18n.t("rerank.fail.bad_shape")
        return None
    out = [0.0] * n
    try:
        rows = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            _LAST_FAIL[0] = i18n.t("rerank.fail.no_json_array")
            return None
        try:
            rows = json.loads(m.group(0))
        except ValueError:
            _LAST_FAIL[0] = i18n.t("rerank.fail.json_parse_failed")
            return None
    for row in rows if isinstance(rows, list) else []:
        try:
            i, sc = int(row["i"]), float(row["s"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(out):
            out[i] = sc
    return out


def calibrate(db, control, positives, min_gap: float = 0.0) -> dict:
    """Set the threshold ★from a control group★ — the same method as for cosine, a different scale.

    ⛔ A stale control group makes it ★count the right answer as noise★ and the brain falls silent where
       it should speak. That actually happened on 2026-08-25: "everything has been merged" was labelled
       'no memory needed', so the rule ★merging three at once drops one commit from the deploy★ was
       counted as noise. Labels are fixed ★by a human reading them★, not by a score (moving those 10 took false fires to 0).
    """
    from brain import store, vectors
    def top_of(q):
        cands = []
        for did, cos, ratio in vectors.search(db, q, k=MAX_CAND):
            r = db.execute("SELECT name, description, body FROM docs WHERE id=?",
                           (did,)).fetchone()
            if r:
                cands.append({"name": r["name"], "description": r["description"],
                              "body": (r["body"] or "")[:500]})
        s = score(q, cands)
        if s is None or not s:
            return None, ""
        i = max(range(len(s)), key=lambda j: s[j])
        return s[i], cands[i]["name"]
    hist: Dict[int, int] = {}                            # ★the distribution of observed scores★
    def note(v):
        hist[int(round(v))] = hist.get(int(round(v)), 0) + 1
    noise = 0.0
    for q in control:
        sc, _ = top_of(q)
        if sc is not None:
            noise = max(noise, sc)
            note(sc)
    pos_scores = []
    for q, gold in positives:
        sc, name = top_of(q)
        if sc is not None:
            note(sc)
            pos_scores.append((sc, _ei.gold_hit(gold, name)))
    thr, why = _place(hist, noise)
    kept = sum(1 for sc, ok in pos_scores if ok and sc >= thr)
    # ⛔⛔ ★If the calibration cannot trust its own result, it does not ship it★ (2026-08-31 forced this)
    #
    #    The first run raised the threshold 8.0 → 10.0 and positive retention fell to ★8/56★.
    #    Its reason read: "the control group scored full marks (10)". But ★reading that control group★
    #    showed it was not a stale label but ★the judge being wrong★ —
    #      "check whether there is anything else to tidy up" → a specific handoff document (10 points)
    #      "880 is not our work"                            → an admin audit-screen document (10 points)
    #    It gives full marks to common Korean phrases. The scale itself is not trustworthy.
    #
    #    ⛔ Ship such a value quietly and two-stage search goes ★nearly silent★ with nobody knowing.
    #       This repository's discipline is "do not call the unmeasured green", and the same holds here:
    #       ★a calibration that cannot be trusted is an alarm, not a result.★ Keep the old value and record why.
    trusted = not why.startswith("⛔")
    if trusted:
        store.set_meta(db, "rerank_min_score", str(thr))
        store.set_meta(db, "rerank_calibrated_for", scale_id())   # ★the scale it holds on★
        store.set_meta(db, "rerank_calibration_blocked", "")
    else:
        store.set_meta(db, "rerank_calibration_blocked", why)
    store.set_meta(db, "rerank_model", _engines.judge_id())
    store.set_meta(db, "rerank_hist", json.dumps(hist))
    store.set_meta(db, "rerank_thr_why", why)
    store.set_meta(db, "rerank_calibrated_at", time.strftime("%Y-%m-%d"))
    return {"min_score": thr, "applied": trusted, "noise_max": noise,
            "why": why, "hist": hist,
            "positives_kept": kept, "positives_n": len(positives),
            "control_n": len(control), "model": MODEL}


def _place(hist: Dict[int, int], noise: float) -> "tuple":
    """★Place the threshold in a valley, not on a peak★ (measured 2026-08-27 forced this).

    The judge emits ★integers★. Over 180 observations the distribution was:

        0:33 · 1:7 · 2:36 · 3:8 · 4:2 · 5:4 · ★6:0★ · 7:9 · 8:30 · 9:12 · 10:39

    It ★never once gives a 6★ — the distribution splits into two masses, {0~5} and {7~10}, with 6 as the
    valley. Yet the threshold stood at ★8.0★, that is, on top of a peak of 30 observations. Then every time
    the model wobbles ★one step★ between 7 and 8, fire/silence flips wholesale:

        cells touching 7~8 flipped 60%  ↔  cells not touching them 5%   (12×)

    So a place with no mass on its boundary is chosen — `hist[t] + hist[t-1]` is ★the number of scores for
    which a ±1 wobble crosses the boundary★. That is what gets minimised.

    ⛔ But ★the noise constraint wins★ — it must sit above the control group's highest score. When the two
       conflict (the valley lies below the noise), the valley is given up and ★that fact is recorded★.
       Hide it and the next session works out "why is it on a peak" from scratch all over again.
    """
    def boundary(t):
        return hist.get(t, 0) + hist.get(t - 1, 0)
    lo = int(noise) + 1 if noise else 0
    # ⛔ ★If noise fills the scale to its end the candidates go empty★ — leaving that unguarded produced the
    #    meaningless reason "no candidates" (a defect of the first version). Clamp at 10 and ★state the fact★:
    #    a control group scoring full marks is not a threshold problem but a signal that ★the control group is stale★.
    capped = lo > 10
    cands = list(range(max(0, min(lo, 10)), 11))
    best = min(cands, key=lambda t: (boundary(t), t))   # on a tie, the lower one (recall)
    unbound = min(range(0, 11), key=lambda t: (boundary(t), t))
    if capped:
        why = ("⛔ the control group scored full marks (%.0f) — not the threshold but ★a stale control "
               "group★. Clamped to 10" % noise)
    elif boundary(best) == 0:
        why = "a valley — mass 0 on the boundary (above the noise floor %.0f)" % noise
    elif best != unbound:
        why = ("★clamped to the noise floor★ %.0f — boundary mass %d. Unconstrained it would be %d "
               "(mass %d)" % (noise, boundary(best), unbound, boundary(unbound)))
    else:
        why = "the shallowest place — boundary mass %d (the constraint did not push this)" % boundary(best)
    return float(best), why
