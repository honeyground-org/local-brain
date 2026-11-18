"""Local, reversible policy selection from independent reading evidence.

No embedding calls, global user profiles, or learned numeric constants ship here. The
baseline stays active until fresh, independent observations support a candidate. Reads
following a brain exposure and turns that edit documents are diagnostic only. A held-out
half confirms the training half; each observation is consumed once. The sign-test risk
bound is a method parameter, not a value fitted to a person's corpus.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time

ARMS = ("baseline", "fusion", "linked")
KEY = "personal_policy_v2"
OBSERVATION_VERSION = 2
ALPHA = 0.05


class RetrievalUnavailable(RuntimeError):
    """A candidate cannot be certified from a degraded semantic route."""


def ensure(db):
    db.execute("CREATE TABLE IF NOT EXISTS personal_events("
               "id INTEGER PRIMARY KEY, identity TEXT UNIQUE, session TEXT, turn INTEGER, query TEXT, "
               "path TEXT, sha TEXT, exposed INTEGER, edited INTEGER DEFAULT 0, measured INTEGER DEFAULT 0, "
               "scores TEXT, costs TEXT, terms TEXT DEFAULT '[]')")
    if "terms" not in {r[1] for r in db.execute("PRAGMA table_info(personal_events)")}:
        db.execute("ALTER TABLE personal_events ADD COLUMN terms TEXT DEFAULT '[]'")


def eligible(args):
    """The measured context: current, unfiltered, default-width retrieval only."""
    return (not any(args.get(k) for k in ("scope", "sources", "kinds", "as_of"))
            and args.get("view", "current") == "current"
            and args.get("k", 8) == 8 and args.get("semantic_k") is None)


def signature():
    from brain import calibrate, textindex, vectors
    return [calibrate.CODE_GENERATION, textindex.TOKENIZER_VERSION, vectors.model_tag()]


def evidence_prefix():
    return "v2:" + hashlib.sha256(json.dumps(signature()).encode()).hexdigest() + ":"


def serving_mode():
    if os.environ.get("BRAIN_PERSONAL_DISABLED"):
        return "off"
    mode = os.environ.get("BRAIN_PERSONAL_MODE", "auto")
    return mode if mode in ("auto", "observe", "off") else "off"


def certified(db, s):
    if s.get("signature") != signature() or not s.get("support") or not s.get("cost_limit"):
        return False
    support = s["support"]
    # Chunk for SQLite's portable bind limit; one query per batch, not one per observation.
    for start in range(0, len(support), 500):
        ids = support[start:start + 500]
        found = db.execute("SELECT COUNT(*) FROM personal_events e JOIN docs d ON d.path=e.path AND d.sha=e.sha "
                           "WHERE e.id IN (" + ",".join("?" for _ in ids) + ") "
                           "AND e.measured=1 AND e.edited=0 AND e.exposed=0 AND e.identity LIKE 'v2:%'",
                           ids).fetchone()[0]
        if found != len(ids):
            return False
    return True


def describe(db):
    """Read-only diagnostics; never expose queries or document paths."""
    modes = {}
    counts = {"current_records": 0, "pending": 0, "measured": 0, "excluded": 0, "legacy_or_other_signature": 0}
    try:
        prefix = evidence_prefix()
        for row in db.execute("SELECT identity, measured, edited, exposed FROM personal_events"):
            if not row[0].startswith(prefix):
                counts["legacy_or_other_signature"] += 1
                continue
            counts["current_records"] += 1
            key = "excluded" if row[1] < 0 or row[2] or row[3] else ("measured" if row[1] == 1 else "pending")
            counts[key] += 1
    except sqlite3.Error:
        pass
    for mode in ("query", "terms"):
        s = state(db, mode)
        valid = s["arm"] != "baseline" and certified(db, s)
        effective = s["arm"] if valid and serving_mode() == "auto" else "baseline"
        modes[mode] = {"selected": s["arm"], "effective": effective,
                       "certified": bool(valid), "history": s.get("history", []),
                       "reason": ("no_validated_winner" if s["arm"] == "baseline" else
                                  "support_invalidated" if not valid else
                                  "serving" if serving_mode() == "auto" else serving_mode())}
    return {"mode": serving_mode(), "observation_version": OBSERVATION_VERSION,
            "contexts": modes, "observations": counts, "eligibility": "current, unfiltered, k=8, automatic semantic width",
            "observation_hosts": ["claude-code"], "native_host_outcome_benefit": "unmeasured"}


def state(db, mode="query"):
    from brain import store
    try:
        s = json.loads(store.get_meta(db, KEY if mode == "query" else KEY + "_terms", "") or "{}")
        return s if s.get("arm") in ARMS else {"arm": "baseline", "after": 0, "history": [], "mode": mode}
    except (ValueError, TypeError):
        return {"arm": "baseline", "after": 0, "history": [], "mode": mode}


def record(db, query, path, session, turn, exposed=False, terms=()):
    """An observation, not a label. Repeated query/document pairs add no confidence."""
    if not query or not session:
        return
    ensure(db)
    doc = db.execute("SELECT sha FROM docs WHERE path=?", (path,)).fetchone()
    if doc is None:
        return
    terms = sorted(set(t for t in terms if isinstance(t, str)))
    identity = evidence_prefix() + hashlib.sha256((query.strip() + json.dumps(terms) + "\0" + path + "\0" + doc[0]).encode()).hexdigest()
    with db:
        db.execute("INSERT OR IGNORE INTO personal_events(identity,session,turn,query,path,sha,exposed,terms) "
                   "VALUES(?,?,?,?,?,?,?,?)", (identity, session, turn, query, path, doc[0], int(exposed), json.dumps(terms)))


def edited(db, session, turn):
    ensure(db)
    with db:
        db.execute("UPDATE personal_events SET edited=1 WHERE session=? AND turn=?", (session, turn))


def candidates(db, query, base, arm, k, terms=()):
    """Reorder bounded candidates; related edges never count as independent votes.

    Cached semantic candidates seed one-hop expansion. An expanded neighbour needs
    its own query evidence. No query embedding is generated by this module.
    """
    from brain import graphstore, search, vectors
    if arm == "baseline":
        return base
    direct = [r for r in base if not r.get("related")]
    pool = {r["path"]: r for r in direct}
    # Existing caller width bounds the search; no person-specific expansion constant.
    width = max(k, len(direct))
    semantic = []
    qvec = None
    try:
        qvec = vectors._qcache_get(db, query)
        if qvec is None:
            raise RetrievalUnavailable("query vector unavailable")
        semantic, why = search.meaning(db, query, width)
        if why:
            raise RetrievalUnavailable(why)
    except sqlite3.Error as exc:
        raise RetrievalUnavailable(str(exc)) from exc
    for row in semantic:
        pool.setdefault(row["path"], row)
    weights = search.query_weights(db, " ".join([query] + list(terms)))
    if arm == "linked" and semantic:
        seeds = []
        for r in semantic[:k]:
            found = search.resolve(db, r["name"])
            if found:
                seeds.append(found["id"])
        links = graphstore.ask(db, "neighbors", seeds)
        seen = set()
        # Inspect no more neighbours than the caller's pool; hubs cannot grow work unboundedly here.
        for seed in seeds:
            for did in sorted(links.get(seed, set())):
                if len(seen) >= width:
                    break
                if did in seen:
                    continue
                seen.add(did)
                row = search._hydrate(db, did)
                if row is None or row["name"] in search._always_in_context():
                    continue
                worth = search.passage_worth(row["body"], weights)
                if worth <= 0:
                    continue
                r = search._present(row, 0.0, [], str(seed), [], db)
                r["why"] = "query evidence in a linked memory (semantic seed %s)" % seed
                if row["path"] not in pool:
                    # The graph supplies candidates, never ranking votes. Score their own
                    # current-model vectors and body evidence by the same rules as other candidates.
                    # Hydration may resolve an old graph ID to a newer revision: score that row.
                    import array
                    cosines = []
                    for held in db.execute("SELECT v.vec FROM vectors v JOIN docs d ON d.id=v.doc_id "
                                           "WHERE v.doc_id=? AND v.sha=d.sha AND v.model=? AND v.dim=?",
                                           (row["id"], vectors.model_tag(), vectors.DIM)):
                        v = array.array("f")
                        v.frombytes(held[0])
                        cosines.append(sum(a*b for a,b in zip(qvec, v)))
                    if cosines:
                        r["cosine"] = max(cosines)
                        semantic.append(r)
                        pool[row["path"]] = r
        semantic.sort(key=lambda r: -r.get("cosine", -1.0))
    worth = {}
    for path in pool:
        row = db.execute("SELECT title,description,body FROM docs WHERE path=?", (path,)).fetchone()
        worth[path] = search.passage_worth("\n".join(row) if row else "", weights)
    order = {p: i for i, p in enumerate(pool)}
    passage = sorted(pool, key=lambda p: (-worth[p], order[p]))
    lists = [[r["path"] for r in direct], [r["path"] for r in semantic], passage]
    score = {}
    for ranked in lists:
        for rank, path in enumerate(ranked):
            score[path] = score.get(path, 0.0) + 1.0 / (search.FUSE_K + rank + 1)
    ranked = sorted(pool, key=lambda p: (-score.get(p, 0.0), order[p]))
    return [pool[p] for p in ranked[:k]]


def apply(db, query, rows, k, terms=()):
    """Read the last measured policy; never train in the request."""
    s = state(db, "terms" if terms else "query")
    arm = s["arm"]
    if serving_mode() != "auto" or arm == "baseline":
        return rows
    if not certified(db, s):
        _save(db, s, "baseline", "support_invalidated", _latest(db))
        return rows
    from brain import vectors
    # Certification uses automatic semantic retrieval with a cached query vector.
    # A cold lexical-only request is a different route; retain its baseline.
    if vectors._qcache_get(db, query) is None:
        return rows
    t = time.monotonic()
    try:
        result = candidates(db, query, rows, arm, k, terms=terms)
        elapsed = time.monotonic() - t
        # A regression in actual serving cost rolls back the next call as well.
        if elapsed > float(s.get("cost_limit", float("inf"))):
            _save(db, s, "baseline", "serving_cost", _latest(db))
            return rows
        return result
    except Exception as exc:
        _save(db, s, "baseline", "serving_" + type(exc).__name__, _latest(db))
        return rows


def _latest(db):
    try:
        return int(db.execute("SELECT MAX(id) FROM personal_events").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def _save(db, s, arm, reason, after, cost_limit=None, support=None):
    from brain import store, observe
    event = {"from": s["arm"], "to": arm, "reason": reason, "at": time.time(), "after": after}
    updated = dict(s, arm=arm, after=after, history=(s.get("history", []) + [event])[-30:])
    if support is not None:
        updated["support"] = list(support)
        updated["signature"] = signature()
    if cost_limit is not None:
        updated["cost_limit"] = cost_limit
    store.set_meta(db, KEY if s.get("mode", "query") == "query" else KEY + "_terms", json.dumps(updated))
    observe.write("personal", event)
    return updated


def _wins(rows, candidate, incumbent):
    # One-sided exact sign test; no losses allowed in either time partition.
    gains = sum(json.loads(r["scores"])[candidate] > json.loads(r["scores"])[incumbent] for r in rows)
    losses = sum(json.loads(r["scores"])[candidate] < json.loads(r["scores"])[incumbent] for r in rows)
    return not losses and gains > 0 and 2.0 ** (-gains) <= ALPHA / (len(ARMS) - 1)


def update(db, mode="query"):
    """Background-only finite evidence window; old evidence cannot repeatedly promote a policy."""
    from brain import search, server, vectors
    ensure(db)
    s = state(db, mode)
    if s["arm"] != "baseline" and not certified(db, s):
        s = _save(db, s, "baseline", "support_invalidated", _latest(db))
    events = db.execute("SELECT * FROM personal_events WHERE measured=0 ORDER BY id").fetchall()
    for ev in events:
        doc = db.execute("SELECT sha FROM docs WHERE path=?", (ev["path"],)).fetchone()
        if not ev["identity"].startswith(evidence_prefix()) or ev["edited"] or ev["exposed"] or doc is None or doc[0] != ev["sha"]:
            with db:
                db.execute("UPDATE personal_events SET measured=-1 WHERE id=?", (ev["id"],))
            continue
        t = time.monotonic()
        terms = json.loads(ev["terms"] or "[]")
        base = []
        args = {"query": ev["query"], "terms": terms, "k": 8}
        # Never certify the automatic semantic route using a lexical-only substitute.
        # Leave cold observations pending; ordinary recall may populate the cache later.
        # Measurement itself must not request a new embedding.
        if vectors._qcache_get(db, ev["query"]) is None:
            continue
        retrieval = {}
        server.call_tool(db, "recall", args, _measure=True, _capture=base, _retrieval=retrieval)
        if retrieval.get("semantic_errors"):
            continue
        baseline_cost = time.monotonic() - t
        scores, costs = {}, {}
        try:
            for arm in ARMS:
                t = time.monotonic()
                rows = candidates(db, ev["query"], base, arm, 8, terms=terms)
                scores[arm] = int(ev["path"] in [r["path"] for r in rows if not r.get("related")][:3])
                costs[arm] = baseline_cost + (time.monotonic() - t)
        except RetrievalUnavailable:
            continue
        with db:
            db.execute("UPDATE personal_events SET measured=1,scores=?,costs=? WHERE id=?",
                       (json.dumps(scores), json.dumps(costs), ev["id"]))
    # Evidence for a removed/edited document cannot promote a policy for its replacement.
    # Independent sessions, not many reads in one conversation, are the sampling units.
    rows = db.execute("SELECT e.* FROM personal_events e JOIN docs d ON d.path=e.path AND d.sha=e.sha "
                      "WHERE measured=1 AND edited=0 AND exposed=0 AND e.identity LIKE 'v2:%' AND e.id>? "
                      "AND NOT EXISTS(SELECT 1 FROM personal_events old WHERE old.session=e.session AND old.id<=?) "
                      "ORDER BY e.id", (s.get("after", 0), s.get("after", 0))).fetchall()
    rows = [r for r in rows if r["identity"].startswith(evidence_prefix()) and bool(json.loads(r["terms"] or "[]")) == (mode == "terms")]
    through = max((r["id"] for r in rows), default=s.get("after", 0))
    unique = {}
    for r in rows:
        unique.setdefault(r["session"], r)
    rows = list(unique.values())
    split = len(rows) // 2
    train, holdout = rows[:split], rows[split:]
    if not train or not holdout:
        return {"arm": s["arm"], "observations": len(rows), "decision": "insufficient"}
    # Candidate selection uses only the earlier half. Confirmation never picks another winner.
    incumbent = s["arm"]
    alternatives = [a for a in ARMS if a != incumbent and _wins(train, a, incumbent)]
    alternatives.sort(key=lambda a: -sum(json.loads(r["scores"])[a] for r in train))
    if alternatives:
        candidate = alternatives[0]
        if _wins(holdout, candidate, incumbent):
            old = sorted(json.loads(r["costs"])[incumbent] for r in holdout)
            new = sorted(json.loads(r["costs"])[candidate] for r in holdout)
            # Relative serving-cost guard is a product budget, never a learned relevance weight.
            at = min(len(old) - 1, math.ceil(0.95 * len(old)) - 1)
            if new[at] <= old[at] * 1.1:
                s = _save(db, s, candidate, "independent_read_holdout", through, new[at] * 1.1, support=[r["id"] for r in rows])
                return {"arm": s["arm"], "observations": len(rows), "decision": "changed"}
        # Confirmation has now been inspected: consume it, even when it rejected the candidate.
        s = _save(db, s, incumbent, "holdout_rejected", through)
    return {"arm": s["arm"], "observations": len(rows), "decision": "retained"}
