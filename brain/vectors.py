"""Remote embedding vectors — ★they join by meaning where words cannot★.

## Why (and why exactly now)

This brain's search is a lexical (character n-gram) index. The measured scorecard (2026-08-20, 43 questions):

    called with synonyms (terms)      42/43 (98%) · reading only 3
    asked nothing at all (the hook)   16/43 (37%)

The gap has one identity — **the question's words are simply absent from the memory** (two spellings of
the same colour · two words for the same cap). Synonyms solve it, but ★the hook has nobody to give them★

(it runs before I intervene). What the corpus itself could fill has already been filled — the
transliteration bridge (English spelt in Korean ↔ the original) took 28→36/45. What is left is out of
reach in principle: the evidence for a pair is "both spellings in one document", and our documents do not

## It is remote — the user's decision (2026-08-21)

*"We use a remote key."* The same way as the parent project (its `embed.py` is also an OpenAI/Gemini
remote call, and local embedding is still on its backlog). ⛔ Therefore ★memory bodies leave for the
provider★ — a price chosen knowingly. The key is read from `secrets.json` (0600) in the brain home, outside
the repository, and never lands in code or git.

## Why LanceDB is not used — at our scale it is not needed (measured 2026-08-21)

    dim  256 · 5,847 chunks → cosine ★full scan 32ms★ · 6MB stored
    dim  512 →  59ms · 12MB
    dim 1536 → 176ms · 36MB   ← this alone eats the hook's whole budget (200ms)

The parent project uses LanceDB and its own measurement was **259MB and 7,355 files for 2,424 items**,
recorded as a problem by itself. We get 32ms from a SQLite BLOB and a pure-Python dot product — ★while
keeping 0 dependencies★. So the dimension is **256** (OpenAI can truncate via `dimensions` — Matryoshka).

## What it does not do (learnt from the graph the day before)

- ★It does not take the place of a direct (lexical) match★ — vectors are candidates appended behind. The
  graph was given a 34% reserved share and that broke the recall contract (top-8 9→8)
- ★The hook calls the remote only when words are silent★ — burning a round trip on every prompt makes the
  session slow and costs money. Where words already added the answer, vectors are not needed (precision there is already 98%).
"""
from __future__ import annotations

from brain import evalinit as _ei  # ★gold judging lives in exactly one place★
from brain import i18n

import array
import collections
import json
import math
import operator
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple

# ★The provider is the person's choice★ (§engines — `brain engines` shows and changes it).
#   openai : text-embedding-3-small · dimension truncation via `dimensions` (Matryoshka) · any
#            OpenAI-compatible server through `base_url` (a local one sends nothing off the machine)
#   gemini : gemini-embedding-2 · `output_dimensionality` (128~3072) · header x-goog-api-key
#   stub   : ⛔ a deterministic hash — ★it knows no meaning★. For wiring and contract checks only (never quality).
#   none   : nothing chosen — meaning-based search is off, and says so.
from brain import engines as _engines                     # noqa: E402
_CHOICE = _engines.choice("embed")
PROVIDER = _CHOICE["provider"]
MODEL = _CHOICE["model"] or "none"
_BASE = _CHOICE["base_url"]

# ★Task prefixes★ — this model family assumes documents and queries go in through ★different prompts★
# (gemini-embedding-2 uses a `task: ...` phrase in front of the prompt rather than a taskType parameter).
# Without it the model does not know the asymmetry between a short query and a long document — measured
# 2026-08-24: with no prefix at dim 256 the right answer was 1st in 9/34 · top-3 in 14/34, while the
# control group rose to 0.76, so ★no absolute threshold could be placed★ (2/34 right answers above it).
# ⛔ Change the prefix and the vector space changes → it must go into the stored tag (model), or they mix meaninglessly.
TASK = os.environ.get("BRAIN_EMBED_TASK", "search").strip()   # "" | "search"
_DOC_PREFIX = {"search": "task: search result\n"}
_QUERY_PREFIX = {"search": "task: question answering\n"}


def model_tag() -> str:
    """The name of the vector space used for storing and lookup. A different model or prefix scheme is a different space."""
    return MODEL + ("+" + TASK if TASK else "")
# ★Dimension 768 · task prefix★ — measured A/B (2026-08-24, same pool of 215 documents):
#     A 256, no prefix   : 1st 13/34 · top-3 21/34
#     B 768, task prefix : 1st ★18/34★ · top-3 ★27/34★
# The first version (256, no prefix) was ★a misconfiguration★. It was found by splitting a small sample
# before burning four more days of quota — this is why measurement comes before volume.
# Cost: full scan 88ms (6,112 chunks) · 19MB stored. The hook only calls when silent, so it is affordable.
DIM = int(os.environ.get("BRAIN_EMBED_DIM", "768") or 768)
CHUNK_CHARS = int(os.environ.get("BRAIN_EMBED_CHUNK", "1200") or 1200)
CHUNK_OVERLAP = 200
# ★Batch 32★ (measured 2026-08-21) — Gemini starts returning 429 from 48. 16/24/32 pass and throughput
# is 10~20 items/s. For 5,847 chunks that is 5~10 minutes (it is incremental, so later runs do only what changed).
BATCH = int(os.environ.get("BRAIN_EMBED_BATCH", "20") or 20)

# ★The limit was learnt by asking the provider directly★ (2026-08-21 · the QuotaFailure in a 429 response)
#
#   quotaId    EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier
#   quotaValue 100          retryDelay 51s
#
# ⛔ ★The decisive detail★ — `batchEmbedContents` is one HTTP request, but the limit counts ★as many items
#    as the batch holds★. That is why batch 48 was an instant 429 (one request, counted as 48).
#    ⇒ Growing the batch cannot grow the total. The ceiling is ★100 chunks per minute★.
#
# ⛔ So the thing to do is not "reduce" but ★fill up to the ceiling★ — it was running at a measured 48
#    chunks/min, throwing half away. The window pacer below pushes it up against 100/min.
def _engines_rpm_items() -> int:
    try:
        from brain import engines as _e
        return int(_e.EMBED_RPM_ITEMS.get(_e.choice("embed")["provider"], 0))
    except Exception:                                    # noqa: BLE001
        return 0


RPM_ITEMS = int(os.environ.get("BRAIN_EMBED_RPM", "")
                or _engines_rpm_items())                 # 0 = not paced (§engines.EMBED_RPM_ITEMS)
RATE_WAITS = (2.0, 5.0, 12.0, 30.0)   # on a 429, wait this long and retry
#
# ⛔ ★This is not a reduction of the total★ — the number of chunks (= the amount measured) stays, and only
#    ★the request interval★ is matched to the destination's ★measured inability★ (429). Measured 2026-08-21:
#    batch 48+ is an instant 429 · 32 passes · sustained, the wall comes at 96 chunks/31s. So pass → shorten the interval, 429 → lengthen it.
PACE_MIN, PACE_MAX = 0.0, 8.0
# ⛔ 20 seconds was short for a batch of 20 items (1,200 characters each) — the cron died on a socket timeout (2026-08-25).
TIMEOUT = float(os.environ.get("BRAIN_EMBED_TIMEOUT", "60") or 60)
# ★Cosine threshold★ — ⛔ never invented. `brain vec-calibrate` places it above the maximum of the control
# group (real talk that needs no memory) and stores it here; absent that, this value is 1.01, meaning
# ★add nothing★ (cosine cannot exceed 1). Not knowing the scale, it chooses silence.
MIN_COS_FALLBACK = 1.01     # cosine cannot exceed 1 = ★add nothing★
MIN_RATIO_FALLBACK = 99.0   # the same meaning — the silent default for the relative rule
TOPN_FOR_MEAN = 40          # the relative rule's denominator: the mean of this query's top N cosines


def _meta_float(db, key: str, env: str, fallback: float) -> float:
    v = os.environ.get(env, "").strip()
    if v:
        return float(v)
    if db is not None:
        try:
            from brain import store
            got = store.get_meta(db, key)
            if got:
                return float(got)
        except Exception:                                # noqa: BLE001
            pass
    return fallback


def min_cos(db=None) -> float:
    """Absolute cosine threshold — ⛔ in this model family it is ★barely usable★ (kept for diagnosis).

    Measured 2026-08-24: real talk needing no memory reaches cosine 0.73~0.76. Place the threshold above
    that and only ★4★ of 34 right answers survive. An absolute cosine is not "the strength of relatedness"
    but ★that model's distribution★. So the canonical gate is `min_ratio`.
    """
    return _meta_float(db, "vec_min_cos", "BRAIN_EMBED_MIN_COS", MIN_COS_FALLBACK)


def min_ratio(db=None) -> float:
    """★The canonical gate★ — top1 ÷ (the mean of that query's top TOPN_FOR_MEAN).

    Why a ratio: an absolute cosine has a different scale per query (a short query pulls everything up).
    Ask instead "for this query, ★is this document unusually close★" and the scale disappears.
    Measured (right answers ranked 1st that survive under the same control group): absolute 4 · gap 7 · z 8 · ★ratio 13★.
    """
    return _meta_float(db, "vec_min_ratio", "BRAIN_EMBED_MIN_RATIO", MIN_RATIO_FALLBACK)


MAX_CHUNKS_PER_DOC = 12        # so a very long document does not swallow the corpus (one is 38,000 characters)


def endpoint_host() -> str:
    """The host this provider will actually talk to — "" when nothing goes out (stub).

    ⛔ ★Asked, never copied★. A scheduled job waits for ★this★ host to resolve before deciding the
    network is dead (§jobs._await_network); write the name there instead and switching provider
    leaves the gate watching a host we never call — green while the real one is unreachable.
    """
    return _engines.endpoint_host("embed")


class NoKey(RuntimeError):
    pass


_LAST_SENT = 0.0        # time of the last send — for an even interval
_WINDOW: collections.deque = collections.deque()   # times of items sent in the last 60s


def _wait_for_quota(n: int) -> float:
    """Fill the per-minute item limit ★as far as possible without exceeding it★. Returns: seconds waited.

    Two things are watched together (either alone leaked in measurement · 2026-08-21~23):
      ① ★an even interval★ — 60/RPM seconds per item. With the window alone it fires 5 batches and sleeps 50s.
      ② ★a 60-second window★ — with the interval alone, ★a bunch at the start★ exceeds the ceiling (measured 111~122/min → 429).
    One 429 costs 20 seconds — not exceeding it is faster (this removes waste, it does not reduce the amount).
    """
    global _LAST_SENT
    waited = 0.0
    if RPM_ITEMS <= 0:                                   # a provider with no limit we know of — the 429 path paces it
        return waited
    need = n * (60.0 / max(1, RPM_ITEMS))
    now = time.time()
    if _LAST_SENT and now - _LAST_SENT < need:          # ① interval
        pause = need - (now - _LAST_SENT)
        time.sleep(pause)
        waited += pause
    while True:                                          # ② window
        now = time.time()
        while _WINDOW and now - _WINDOW[0] > 60.0:
            _WINDOW.popleft()
        if len(_WINDOW) + n <= RPM_ITEMS or not _WINDOW:
            break
        pause = max(0.2, 60.0 - (now - _WINDOW[0]) + 0.1)
        time.sleep(pause)
        waited += pause
    return waited


def _mark_sent(n: int) -> None:
    global _LAST_SENT
    _LAST_SENT = time.time()
    for _ in range(n):
        _WINDOW.append(_LAST_SENT)


class DailyQuotaReached(RuntimeError):
    """Today's share is spent — ★a state, not a failure★ (measured 2026-08-21).

    The real wall of Gemini's free tier is not the per-minute one (100) but ★1,000 per day★
    (`EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier`).
    Our corpus is 6,112 chunks, so on the free tier it fills over 5~6 days (the user's choice 2026-08-21).
    Indexing is incremental on sha, so ★running it again resumes★ — which is why this is not raised as an
    exception but returned as "this far". Raised, a daily automatic run would look red every day.
    """


class EmbedError(RuntimeError):
    """★Carry the status and the provider's own words as they are★ (measured 2026-08-21).

    The first version mashed every exception into `RuntimeError("embedding request failed: HTTPError")`.
    So when the first call failed ★the cause could not be known★ — a wrong key, a wrong parameter, or the
    limit. The real cause was a 429 `insufficient_quota` (0 credit), and that is ★an error that retrying
    can never clear★, yet it was being retried.
    It builds a failure that can be diagnosed — learnt repeatedly elsewhere in this project.
    """

    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        self.retry_after = 0.0            # the wait the provider told us (seconds) — used when present
        self.daily = False                # is it ★the daily limit★ (a different prescription from per-minute)
        super().__init__("HTTP %s %s — %s" % (status, code or "-", message[:200]))

    @property
    def retryable(self) -> bool:
        if self.code in ("insufficient_quota", "credit_balance_exhausted",
                         "invalid_api_key", "model_not_found",
                         "INVALID_ARGUMENT", "PERMISSION_DENIED", "UNAUTHENTICATED",
                         "NOT_FOUND", "400", "401", "403", "404"):
            return False
        return self.status >= 500 or self.status == 429




def api_key(provider: str = "") -> str:
    """The key per provider. Environment variable → secrets.json (0600) outside the repository, in that order.

    ⛔ One reader for every role (§engines.api_key) — the judge and the embedder used to read keys
       through two copies of this rule.
    """
    provider = provider or PROVIDER
    if provider == "stub":
        return "stub"
    if provider == "none":
        raise NoKey("no embedding engine is chosen — `brain engines` shows how to pick one")
    c = (_CHOICE if provider == PROVIDER else
         {"provider": provider, "base_url": _engines.PROVIDERS.get(provider, {}).get("base", "")})
    return _engines.api_key(c)


def embed(texts: Sequence[str], model: str = "", dim: int = 0,
          side: str = "doc") -> List[List[float]]:
    """Sentences → vectors. ⛔ Standard library only (no `requests` is added).

    ⛔ ★This is an outbound door — mask the secrets before sending★ (§privacy). Measured 2026-08-26: 21 of
       619 memories held credential patterns and every one of them was going out through this door. The only
       means of stopping it was a per-source `no_embed`, so nobody turned it off. The key names survive and
       only the values are masked, so search quality is unchanged.
    """
    if not texts:
        return []
    from . import privacy
    texts, _hidden = privacy.scrub_all(texts)      # ★right before the remote★ — the stub path included
    model, dim = model or MODEL, dim or DIM
    if TASK:
        pre = (_DOC_PREFIX if side == "doc" else _QUERY_PREFIX).get(TASK, "")
        texts = [pre + t for t in texts]
    if PROVIDER == "stub":
        return [_stub_vec(t, dim) for t in texts]
    if PROVIDER not in ("gemini", "openai"):
        raise NoKey("no embedding engine is chosen — `brain engines` shows how to pick one")
    if PROVIDER == "gemini":
        url = "%s/models/%s:batchEmbedContents" % (_BASE, model)
        body = {"requests": [{"model": "models/%s" % model,
                              "content": {"parts": [{"text": t}]},
                              "output_dimensionality": dim} for t in texts]}
        headers = {"x-goog-api-key": api_key(), "Content-Type": "application/json"}
        data = _post(url, headers, body)
        rows = data.get("embeddings") or []
        if len(rows) != len(texts):
            raise EmbedError(0, "shape", "%d responses ≠ %d requests" % (len(rows), len(texts)))
        return [r["values"] for r in rows]
    url = "%s/embeddings" % _BASE
    body = {"model": model, "input": list(texts), "dimensions": dim}
    key = api_key()
    headers = {"Content-Type": "application/json"}
    if key:                                          # a local OpenAI-compatible server needs none
        headers["Authorization"] = "Bearer %s" % key
    data = _post(url, headers, body)
    rows = sorted(data["data"], key=lambda d: d["index"])
    return [r["embedding"] for r in rows]


def _stub_vec(text: str, dim: int) -> List[float]:
    """⛔ A deterministic vector that knows no meaning — for wiring checks only."""
    import hashlib
    out: List[float] = []
    seed = text.encode("utf-8")
    while len(out) < dim:
        h = hashlib.sha256(seed).digest()
        out.extend((b - 127.5) / 127.5 for b in h)
        seed = h
    return out[:dim]


def parse_quota(err: dict) -> Dict[str, float]:
    """Read ★which wall it is★ from the provider's 429 body — per-minute or per-day, and what the limit is.

    ⛔ ★If this parsing becomes two copies they drift apart in silence★ (2026-08-26). There are two outbound
       doors (embedding · the judge) and two walls, but the code that knew the daily wall lived only on the
       embedding side. The judge had no concept of `PerDay` at all, so it mashed a 429 into None ★exactly
       like a network error★ — and so on an afternoon past the daily limit, two-stage search fell entirely
       silent with no trace in the log or the checks.

    ⛔ ★`retryDelay` lies about the daily wall★ — a measured response offered `32s` while the real release
       is ★Pacific midnight★. Believe it and you hammer the wall all day long.
       So when `daily` is true, `retry_after` is not used.

    A measured body (2026-08-26 · gemini-3.5-flash-lite free tier):
        quotaId    GenerateRequestsPerDayPerProjectPerModel-FreeTier
        quotaValue 500            retryDelay 32s
    """
    out = {"daily": 0.0, "limit": 0.0, "retry_after": 0.0}
    for d in (err.get("details") or []):
        kind = str(d.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for v in (d.get("violations") or []):
                if "PerDay" in str(v.get("quotaId", "")):
                    out["daily"] = 1.0
                    try:
                        out["limit"] = float(v.get("quotaValue") or 0)
                    except (TypeError, ValueError):
                        pass
        elif kind.endswith("RetryInfo"):
            try:
                out["retry_after"] = float(str(d.get("retryDelay", "0s")).rstrip("s") or 0)
            except ValueError:
                pass
    return out


def _post(url: str, headers: Dict[str, str], body: dict) -> dict:
    """POST + ★a failure that can be diagnosed★ (§EmbedError). It retries once, and only what can clear."""
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=raw, headers=headers)
    last: Optional[Exception] = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                err = (json.loads(exc.read().decode("utf-8", "replace"))
                       .get("error") or {})
            except Exception:                              # noqa: BLE001
                err = {}
            code = err.get("code") or err.get("status") or ""
            e = EmbedError(exc.code, str(code), err.get("message") or "(no body)")
            q = parse_quota(err)                    # ★dig it in one place only★ (§parse_quota)
            e.daily = bool(q["daily"])
            e.retry_after = q["retry_after"]
            if not e.retryable or attempt == 1:
                raise e from None
            last = e
            time.sleep(0.6)
        # ⛔ ★Catch OSError★ (measured 2026-08-25) — in Python 3.9 `socket.timeout` is ★not★ a subclass of
        #    `TimeoutError` (that alias arrives in 3.10). So the scheduled cron ★died with a traceback★ on
        #    its second batch, on a socket timeout — filling only 20 chunks of that day's share.
        #    Network failures come in many kinds and all of them sit under OSError.
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            last = exc
            if attempt == 1:
                raise EmbedError(0, type(exc).__name__, str(exc)) from None
            time.sleep(0.6)
    raise EmbedError(0, "unknown", str(last))


def _unit(v: Sequence[float]) -> array.array:
    """L2 normalisation — done once at storage time, search needs ★only a dot product★."""
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array("f", [x / n for x in v])


def chunks(text: str, head: str = "") -> List[str]:
    """Cut a long document into overlapping windows.

    ⛔ One vector per document ★buries a specific fact in the average★ — some of our project memories are
    38,000 characters and hold dozens of different facts.
    The first chunk carries the title and description (without knowing what the document is, each chunk loses its context).
    """
    text = (text or "").strip()
    if not text:
        return []
    out, i = [], 0
    step = max(200, CHUNK_CHARS - CHUNK_OVERLAP)
    while i < len(text) and len(out) < MAX_CHUNKS_PER_DOC:
        piece = text[i:i + CHUNK_CHARS]
        out.append((head + "\n" + piece).strip() if not out and head else piece)
        i += step
    return out


def ensure_table(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS vectors("
               " doc_id INTEGER NOT NULL, chunk_no INTEGER NOT NULL,"
               " sha TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,"
               " vec BLOB NOT NULL, PRIMARY KEY(doc_id, chunk_no))")
    db.execute("CREATE INDEX IF NOT EXISTS idx_vec_doc ON vectors(doc_id)")


def no_embed_sources() -> List[str]:
    """★Sources that are never sent out★ — `"embed": false` in config.

    Why an installed tool needs this: meaning-search is ★a remote API★, so document bodies leave. Everyone
    has material they want "searchable but never sent out" (client documents, internal policy, a private diary).
    Lexical search and the graph are entirely local, so such a source ★is still findable★ — only meaning-search drops.
    """
    try:
        from brain import store
        cfg = store.load_config(tolerant=True)
    except Exception:                                    # noqa: BLE001
        return []
    return [s.get("name", "") for s in (cfg.get("sources") or [])
            if s.get("embed") is False and s.get("name")]


def stale_docs(db: sqlite3.Connection) -> List[sqlite3.Row]:
    """Documents with no vector or ★whose content changed★. The sha is checked too, so incremental is free."""
    ensure_table(db)
    skip = no_embed_sources()
    sql = ("SELECT d.id, d.name, d.source, d.title, d.description, d.body, d.sha "
           "FROM docs d LEFT JOIN vectors v ON v.doc_id=d.id AND v.chunk_no=0 "
           "WHERE (v.doc_id IS NULL OR v.sha<>d.sha OR v.model<>? OR v.dim<>?)")
    args: List = [model_tag(), DIM]
    if skip:
        sql += " AND d.source NOT IN (%s)" % ",".join("?" * len(skip))
        args.extend(skip)
    return db.execute(sql, args).fetchall()


def build(db: sqlite3.Connection, limit_docs: int = 0,
          progress: bool = False, only_ids: Optional[Sequence[int]] = None) -> dict:
    """Embed and store only the changed documents. Returns: statistics."""
    ensure_table(db)
    todo = stale_docs(db)
    # ★Memory files first★ — every gold answer in the evaluation is a memory, so this order lets quality
    #   be measured before the whole corpus is done. The total is unchanged (only the order is).
    todo = sorted(todo, key=lambda r: 0 if r["source"] == "memory" else 1)
    if only_ids:
        keep = set(only_ids)
        todo = [r for r in todo if r["id"] in keep]
    if limit_docs:
        todo = todo[:limit_docs]
    n_docs = n_chunks = n_calls = 0
    waited = 0.0
    hit_daily_flag = [False]
    t0 = time.time()
    pend: List[Tuple[int, int, str, str]] = []       # (doc_id, chunk_no, sha, text)

    def flush() -> None:
        nonlocal n_chunks, n_calls, waited
        if not pend:
            return
        # ★Fill up to the ceiling★ — the window pacer pushes as fast as it can without passing 100/min.
        # On a 429, rest for the provider's retryDelay and empty the window (that window is already full).
        vecs = None
        for attempt in range(5):
            waited += _wait_for_quota(len(pend))
            try:
                vecs = embed([p[3] for p in pend])
                _mark_sent(len(pend))
                break
            except EmbedError as exc:
                if getattr(exc, "daily", False):
                    raise DailyQuotaReached(str(exc)) from None
                if exc.status != 429 or attempt == 4:
                    raise
                # ⛔ ★The limit is not used by this process alone★ (measured 2026-08-21) — when interactive
                #    queries and the checks eat the same 100/min, indexing repeats 429→wait and
                #    ★looks like it has stopped★ (the log went empty and it seemed genuinely dead).
                #    So it must always print that it is waiting.
                wait = exc.retry_after or 20.0
                if progress:
                    print("  " + i18n.t("vectors.rate_limit_wait", wait="%.0f" % wait,
                                        attempt=attempt + 1), flush=True)
                time.sleep(wait)
        if vecs is None:
            raise EmbedError(429, "429", "the limit keeps hitting")
        n_calls += 1
        rows = [(p[0], p[1], p[2], model_tag(), DIM, _unit(v).tobytes())
                for p, v in zip(pend, vecs)]
        with db:
            db.executemany(
                "INSERT OR REPLACE INTO vectors(doc_id,chunk_no,sha,model,dim,vec)"
                " VALUES(?,?,?,?,?,?)", rows)
        n_chunks += len(rows)
        pend.clear()

    for row in todo:
        if hit_daily_flag[0]:
            break
        head = "%s — %s" % (row["title"] or row["name"], row["description"] or "")
        cs = chunks(row["body"] or "", head=head.strip(" —"))
        if not cs:
            continue
        with db:
            db.execute("DELETE FROM vectors WHERE doc_id=?", (row["id"],))
        for i, c in enumerate(cs):
            pend.append((row["id"], i, row["sha"], c))
            if len(pend) >= BATCH:
                try:
                    flush()
                except DailyQuotaReached:
                    hit_daily_flag[0] = True
                    break
        n_docs += 1
        if progress and n_docs % 10 == 0:
            el = time.time() - t0
            print("  " + i18n.t("vectors.progress", done=n_docs, total=len(todo), chunks=n_chunks,
                                sec="%.0f" % el, rate="%.0f" % (n_chunks / max(1e-9, el / 60.0)),
                                waited="%.0f" % waited), flush=True)
    try:
        flush()
    except DailyQuotaReached:
        hit_daily_flag[0] = True
    el = time.time() - t0
    left = len(stale_docs(db))
    # a chosen vector store gets what was just embedded (§stores — nothing happens with the default)
    from brain import stores
    pushed = stores.sync_if_chosen(db, ("vector",)).get("vector")
    return {"docs": n_docs, "chunks": n_chunks, "calls": n_calls,
            "daily_quota_reached": hit_daily_flag[0], "docs_left": left,
            "seconds": round(el, 1), "chunks_per_min": round(n_chunks / max(1e-9, el / 60.0), 1),
            "waited_sec": round(waited, 1), "model": model_tag(), "dim": DIM,
            "provider": PROVIDER, **({"store": pushed} if pushed else {})}


def coverage(db: sqlite3.Connection) -> dict:
    ensure_table(db)
    tot = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    have = db.execute("SELECT COUNT(DISTINCT doc_id) FROM vectors WHERE model=? AND dim=?",
                      (model_tag(), DIM)).fetchone()[0]
    ch = db.execute("SELECT COUNT(*) FROM vectors WHERE model=? AND dim=?",
                    (model_tag(), DIM)).fetchone()[0]
    return {"docs": tot, "embedded": have, "chunks": ch,
            "stale": len(stale_docs(db))}


def _qcache_get(db: sqlite3.Connection, query: str) -> Optional[array.array]:
    """Query-embedding cache — the same question is never sent twice.

    ★It is what makes the checks repeatable★ — a harness making 91 round trips every time burns the limit
    and is too slow for anyone to run. And in the hook, saying the same thing again saves 500ms.
    The cache key holds the model and dimension, so changing the model re-measures automatically.
    """
    import hashlib
    db.execute("CREATE TABLE IF NOT EXISTS vec_qcache("
               " qsha TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL,"
               " vec BLOB NOT NULL, PRIMARY KEY(qsha, model, dim))")
    qsha = hashlib.sha1(query.encode("utf-8")).hexdigest()
    row = db.execute("SELECT vec FROM vec_qcache WHERE qsha=? AND model=? AND dim=?",
                     (qsha, model_tag(), DIM)).fetchone()
    if row is None:
        return None
    v = array.array("f")
    v.frombytes(row[0] if not hasattr(row, "keys") else row["vec"])
    return v


def _qcache_put(db: sqlite3.Connection, query: str, vec: array.array) -> None:
    import hashlib
    qsha = hashlib.sha1(query.encode("utf-8")).hexdigest()
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO vec_qcache(qsha,model,dim,vec) "
                       "VALUES(?,?,?,?)", (qsha, model_tag(), DIM, vec.tobytes()))
    except sqlite3.Error:
        pass                                             # a failed cache must not block search


def search(db: sqlite3.Connection, query: str, k: int = 5,
           qvec: Optional[Sequence[float]] = None) -> List[Tuple[int, float, float]]:
    """Documents close in meaning [(doc_id, cosine, ★ratio★)] — a full scan.

    ratio = cosine ÷ (the mean of this query's top TOPN_FOR_MEAN cosines). The gate uses this value
    (§min_ratio) — an absolute cosine cannot separate, because of this model's distribution.
    When several chunks point at the same document, ★the closest chunk's★ score becomes the document's
    (using the mean would always lose long documents — every one of our project memories has that shape).
    """
    ensure_table(db)
    if qvec is not None:
        q = _unit(qvec)
    else:
        q = _qcache_get(db, query)
        if q is None:
            q = _unit(embed([query], side="query")[0])
            _qcache_put(db, query, q)
    # ★Where the similarity search runs is the chosen vector store's★ (§stores · §vecstore) — the local
    # scan by default. Every backend returns the same thing: documents by their closest chunk, best first.
    from brain import stores, vecstore
    n = max(k, TOPN_FOR_MEAN)
    local = vecstore.SqliteVectors()
    c = stores.choice("vector")
    if c["backend"] == "sqlite" or c.get("error"):
        order = local.search(db, q, model_tag(), DIM, n)
    else:
        be = vecstore.make(c)
        order = stores.serve(db, "vector", be.target(model_tag(), DIM),
                             lambda: be.search(db, q, model_tag(), DIM, n),
                             lambda: local.search(db, q, model_tag(), DIM, n))
    if not order:
        return []
    head = [c for _, c in order[:TOPN_FOR_MEAN]]
    mean = (sum(head) / len(head)) or 1e-9
    return [(did, cos, cos / mean) for did, cos in order[:k]]


def calibrate_cos(db: sqlite3.Connection, control: Sequence[str],
                  positives: Sequence[Tuple[str, str]],
                  margin: float = 0.01, save: bool = True) -> dict:
    """Set the gate ★from a control group★ — the same method used for the BM25 threshold.

    control    real talk needing no memory (surfacing here is noise) → the maximum of their ★ratio★
    positives  (question, gold memory name) → how many survive above that threshold
    ⛔ The absolute cosine is reported too, but ★the gate is the ratio★.
    """
    ctrl = []
    for q in control:
        hits = search(db, q, k=1)
        ctrl.append((hits[0][2] if hits else 0.0, hits[0][1] if hits else 0.0))
    noise_ratio = max((r for r, _ in ctrl), default=0.0)
    noise_cos = max((c for _, c in ctrl), default=0.0)
    thr = round(noise_ratio + margin, 4)

    top1 = top3 = 0
    for q, gold in positives:
        hits = search(db, q, k=3)
        rows = []
        for did, cos, ratio in hits:
            r = db.execute("SELECT name FROM docs WHERE id=?", (did,)).fetchone()
            rows.append((r["name"] if r else "?", cos, ratio))
        if rows and _ei.gold_hit(gold, rows[0][0]) and rows[0][2] >= thr:
            top1 += 1
        if any(_ei.gold_hit(gold, n) and rt >= thr for n, _, rt in rows):
            top3 += 1
    if save:
        from brain import store
        store.set_meta(db, "vec_min_ratio", str(thr))
        store.set_meta(db, "vec_min_cos", str(round(noise_cos + 0.01, 4)))
        store.set_meta(db, "vec_model", "%s/%s/%d" % (PROVIDER, model_tag(), DIM))
    return {"gate": "ratio", "min_ratio": thr,
            "noise_ratio_max": round(noise_ratio, 4),
            "noise_cos_max": round(noise_cos, 4),
            "positives_top1": top1, "positives_top3": top3,
            "positives_n": len(positives), "control_n": len(control),
            "model": model_tag(), "dim": DIM}


# ---------------------------------------------------------------------------
# ★The gate must be re-measured when the corpus grows★ (measured 2026-08-24)
#
# With the same questions and settings, growing only the document pool 215 → 294:
#     rank    1st 18/34 held · top-3 27→24   ← ★search quality holds★
#     gate    above threshold 13 → 8         ← ★the yield falls★
# Why: the ratio's denominator is "the mean of this query's top 40", so more neighbours raise the mean and lower the ratio.
# So it needs the same treatment as the BM25 threshold — ★re-measure at indexing time★ (§calibrate.calibrate).
#
# ⛔ But nobody re-calibrates if it costs money. Happily ★the control-group queries do not change★ — what
#    changes is the corpus. So if the query embeddings are in the cache, re-calibration is ★0 API calls★.
#    If they are not cached it silently skips (indexing spends no money).
# ⛔ It does not store a value while loading is incomplete — if the hook starts speaking from a threshold
#    measured on a half-filled pool, the principle "never add on an unknown scale" is broken.
MIN_COVERAGE_TO_ARM = float(os.environ.get("BRAIN_EMBED_ARM_AT", "0.9") or 0.9)


def recalibrate_if_free(db: sqlite3.Connection) -> Optional[dict]:
    """Re-measure the gate from the cached control group alone — ★it spends no money★. None if it cannot."""
    import json as _json
    import os as _os
    from brain import evalinit as _evalinit
    path = _os.path.join(_evalinit.eval_dir(), "short.json")
    try:
        with open(path, encoding="utf-8") as fh:
            d = _json.load(fh)
    except (OSError, ValueError):
        return None
    ctrl = [c["q"] if isinstance(c, dict) else c
            for c in d.get("C_no_memory_needed", [])]
    pos = [(c["q"], c["gold"]) for c in d.get("A_memory_needed", []) if c.get("gold")]
    if not ctrl or not pos:
        return None
    # ★All★ of them must be cached — using only some narrows the control group and sets the threshold low
    if any(_qcache_get(db, q) is None for q in ctrl):
        return None
    cov = coverage(db)
    if not cov["docs"] or cov["embedded"] / cov["docs"] < MIN_COVERAGE_TO_ARM:
        return None
    have_pos = [(q, g) for q, g in pos if _qcache_get(db, q) is not None]
    return calibrate_cos(db, ctrl, have_pos, save=True)


def warm_control_cache(db: sqlite3.Connection, limit: int = 80) -> dict:
    """Fill the cache with control-group and gold query embeddings — ★the precondition for arming itself★.

    `recalibrate_if_free` works only when ★all★ of the control group is cached (using only some narrows the
    control group and sets the threshold low). That cache is filled once and is free forever after — the
    queries do not change, only the corpus does. A few % of one day's share ★makes re-calibration free forever★.
    """
    import json as _json
    import os as _os
    from brain import evalinit as _evalinit
    try:
        with open(_os.path.join(_evalinit.eval_dir(), "short.json"), encoding="utf-8") as fh:
            d = _json.load(fh)
    except (OSError, ValueError):
        return {"warmed": 0, "missing": 0}
    qs = [c["q"] if isinstance(c, dict) else c for c in d.get("C_no_memory_needed", [])]
    qs += [c["q"] for c in d.get("A_memory_needed", []) if c.get("gold")]
    todo = [q for q in dict.fromkeys(qs) if _qcache_get(db, q) is None][:limit]
    done = 0
    for q in todo:
        try:
            _wait_for_quota(1)
            _qcache_put(db, q, _unit(embed([q], side="query")[0]))
            _mark_sent(1)
            done += 1
        except EmbedError as exc:
            return {"warmed": done, "missing": len(todo) - done,
                    "stopped": str(exc)[:80]}
    return {"warmed": done, "missing": 0}
