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
OBSERVATION_VERSION = 14
ALPHA = 0.05


class RetrievalUnavailable(RuntimeError):
    """A candidate cannot be certified from a degraded semantic route."""


def ensure(db):
    db.execute("CREATE TABLE IF NOT EXISTS personal_events("
               "id INTEGER PRIMARY KEY, identity TEXT UNIQUE, session TEXT, turn INTEGER, query TEXT, "
               "path TEXT, sha TEXT, exposed INTEGER, edited INTEGER DEFAULT 0, measured INTEGER DEFAULT 0, "
               "scores TEXT, costs TEXT, terms TEXT DEFAULT '[]', settled INTEGER DEFAULT 0)")
    if "terms" not in {r[1] for r in db.execute("PRAGMA table_info(personal_events)")}:
        db.execute("ALTER TABLE personal_events ADD COLUMN terms TEXT DEFAULT '[]'")
    if "settled" not in {r[1] for r in db.execute("PRAGMA table_info(personal_events)")}:
        db.execute("ALTER TABLE personal_events ADD COLUMN settled INTEGER DEFAULT 0")
    db.execute("CREATE TABLE IF NOT EXISTS personal_exposures("
               "session TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(session,path))")
    db.execute("CREATE INDEX IF NOT EXISTS personal_events_turn ON personal_events(session,turn)")
    db.execute("CREATE TABLE IF NOT EXISTS personal_edited_turns("
               "session TEXT NOT NULL, turn INTEGER NOT NULL, PRIMARY KEY(session,turn))")


def eligible(args):
    """The measured context: current, unfiltered, default-width retrieval only."""
    from brain import recalljson
    if not isinstance(args, dict) or (args and recalljson.validate(args)):
        return False
    return (not any(args.get(k) for k in ("scope", "sources", "kinds", "as_of"))
            and args.get("view", "current") == "current"
            and args.get("k", 8) == 8 and args.get("semantic_k") is None)


def _event_request(event):
    """Validate persisted inputs before replay, including older malformed rows."""
    from brain import recalljson
    try:
        args = {"query": event["query"], "terms": json.loads(event["terms"]), "k": 8}
    except (ValueError, TypeError):
        return None
    return None if recalljson.validate(args) else args


def signature():
    from brain import calibrate, textindex, vectors
    return [calibrate.CODE_GENERATION, textindex.TOKENIZER_VERSION, vectors.model_tag()]


def evidence_prefix():
    return "v%d:" % OBSERVATION_VERSION + hashlib.sha256(json.dumps(signature()).encode()).hexdigest() + ":"


def serving_mode():
    if os.environ.get("BRAIN_PERSONAL_DISABLED"):
        return "off"
    mode = os.environ.get("BRAIN_PERSONAL_MODE", "auto")
    return mode if mode in ("auto", "observe", "off") else "off"


def _current_ids(db, ids):
    from brain import lifecycle
    with lifecycle.view("current"):
        snap = lifecycle.snapshot(db)
        return {did for did in ids if did in snap.resolve(did)
                and snap.info(did).get("current") is not False}


def certified(db, s):
    if s.get("signature") != signature() or not s.get("support") or not s.get("cost_limit"):
        return False
    support = s["support"]
    # Chunk for SQLite's portable bind limit; one query per batch, not one per observation.
    for start in range(0, len(support), 500):
        ids = support[start:start + 500]
        found = db.execute("SELECT d.id,e.query,e.terms FROM personal_events e JOIN docs d ON d.path=e.path AND d.sha=e.sha "
                           "WHERE e.id IN (" + ",".join("?" for _ in ids) + ") "
                           "AND e.measured=1 AND e.settled=1 AND e.edited=0 AND e.exposed=0 AND e.identity LIKE ?",
                           list(ids) + [evidence_prefix() + "%"]).fetchall()
        current = _current_ids(db, {r[0] for r in found})
        if len(found) != len(ids) or any(r[0] not in current or _event_request({"query": r[1], "terms": r[2]}) is None for r in found):
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
    from brain import recalljson
    terms = list(terms) if isinstance(terms, tuple) else terms
    if not session or recalljson.validate({"query": query, "terms": terms}):
        return
    ensure(db)
    doc = db.execute("SELECT sha FROM docs WHERE path=?", (path,)).fetchone()
    if doc is None:
        return
    terms = sorted(set(t for t in terms if isinstance(t, str)))
    identity = evidence_prefix() + hashlib.sha256((query.strip() + json.dumps(terms) + "\0" + path + "\0" + doc[0]).encode()).hexdigest()
    with db:
        # The lookup is part of the insert, so a concurrent edit either precedes
        # this write or marks its row afterwards; neither order loses the veto.
        db.execute("INSERT OR IGNORE INTO personal_events(identity,session,turn,query,path,sha,exposed,terms,edited) "
                   "VALUES(?,?,?,?,?,?,(? OR EXISTS(SELECT 1 FROM personal_exposures WHERE session=? AND path=?)),?,"
                   "EXISTS(SELECT 1 FROM personal_edited_turns WHERE session=? AND turn=?))",
                   (identity, session, turn, query, path, doc[0], int(exposed), session, path, json.dumps(terms), session, turn))


def expose(db, session, names):
    """Persist earlier presentation independently of the bounded diagnostic rank window."""
    ensure(db)
    with db:
        for start in range(0, len(names), 500):
            group = names[start:start + 500]
            db.execute("INSERT OR IGNORE INTO personal_exposures(session,path) "
                       "SELECT ?,path FROM docs WHERE name IN (" + ",".join("?" for _ in group) + ")",
                       [session] + group)


def settle(db, session, turn):
    """A subsequent user message confirms the prior turn has been fully scanned."""
    ensure(db)
    with db:
        db.execute("UPDATE personal_events SET settled=1 WHERE session=? AND turn=?", (session, turn))


def edited(db, session, turn):
    ensure(db)
    with db:
        db.execute("INSERT OR IGNORE INTO personal_edited_turns(session,turn) VALUES(?,?)", (session, turn))
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
            found = db.execute("SELECT id FROM docs WHERE path=?", (r["path"],)).fetchone()
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


def apply(db, query, rows, k, terms=(), baseline_cost=0.0):
    """Read the last measured policy; never train in the request."""
    t = time.monotonic()
    s = None
    try:
        if serving_mode() != "auto":
            return rows
        s = state(db, "terms" if terms else "query")
        arm = s["arm"]
        if arm == "baseline":
            return rows
        if not certified(db, s):
            _save(db, s, "baseline", "support_invalidated", _latest(db))
            return rows
        from brain import vectors
        # Certification uses the automatic route with a valid cached query vector.
        if vectors._qcache_get(db, query) is None:
            return rows
        result = candidates(db, query, rows, arm, k, terms=terms)
        elapsed = time.monotonic() - t
        if baseline_cost + elapsed > float(s.get("cost_limit", float("inf"))):
            _save(db, s, "baseline", "serving_cost", _latest(db))
            return rows
        return result
    except Exception as exc:
        # A diagnostic rollback must not mask the already-computed baseline.
        if s is not None:
            try:
                _save(db, s, "baseline", "serving_" + type(exc).__name__, _latest(db))
            except Exception:
                pass
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
        args = _event_request(ev)
        if args is None:
            with db:
                db.execute("UPDATE personal_events SET measured=-1 WHERE id=?", (ev["id"],))
            continue
        doc = db.execute("SELECT sha,id FROM docs WHERE path=?", (ev["path"],)).fetchone()
        if not ev["identity"].startswith(evidence_prefix()) or ev["edited"] or ev["exposed"] or doc is None or doc[0] != ev["sha"] or doc[1] not in _current_ids(db, [doc[1]]):
            with db:
                db.execute("UPDATE personal_events SET measured=-1 WHERE id=?", (ev["id"],))
            continue
        if not ev["settled"]:
            continue  # the host may still write later in this user turn
        t = time.monotonic()
        terms = args["terms"]
        base = []
        # Never certify the automatic semantic route using a lexical-only substitute.
        # Leave cold observations pending; ordinary recall may populate the cache later.
        # Measurement itself must not request a new embedding.
        retrieval = {}
        try:
            if vectors._qcache_get(db, ev["query"]) is None:
                continue
            server.call_tool(db, "recall", args, _measure=True, _capture=base, _retrieval=retrieval)
        except (sqlite3.Error, ValueError, TypeError, KeyError, RuntimeError):
            continue  # isolate an unavailable observation; retry it after later evidence
        if retrieval.get("semantic_errors"):
            continue
        baseline_cost = retrieval.get("baseline_seconds")
        if type(baseline_cost) not in (int, float) or not math.isfinite(baseline_cost) or baseline_cost < 0:
            continue  # a degraded replay cannot block later observations or certify itself
        scores, costs = {}, {}
        try:
            for arm in ARMS:
                t = time.monotonic()
                rows = candidates(db, ev["query"], base, arm, 8, terms=terms)
                scores[arm] = int(ev["path"] in [r["path"] for r in rows if not r.get("related")][:3])
                costs[arm] = baseline_cost + (time.monotonic() - t)
        except (sqlite3.Error, ValueError, TypeError, KeyError, RuntimeError):
            continue
        with db:
            db.execute("UPDATE personal_events SET measured=1,scores=?,costs=? WHERE id=?",
                       (json.dumps(scores), json.dumps(costs), ev["id"]))
    # Evidence for a removed/edited document cannot promote a policy for its replacement.
    # Independent sessions, not many reads in one conversation, are the sampling units.
    rows = db.execute("SELECT e.*,d.id AS current_doc_id FROM personal_events e JOIN docs d ON d.path=e.path AND d.sha=e.sha "
                      "WHERE measured=1 AND settled=1 AND edited=0 AND exposed=0 AND e.identity LIKE ? AND e.id>? "
                      "AND NOT EXISTS(SELECT 1 FROM personal_events old WHERE old.session=e.session AND old.id<=?) "
                      "ORDER BY e.id", (evidence_prefix() + "%", s.get("after", 0), s.get("after", 0))).fetchall()
    current = _current_ids(db, {r["current_doc_id"] for r in rows})
    usable = []
    for row in rows:
        args = _event_request(row)
        if args is None:
            with db:
                db.execute("UPDATE personal_events SET measured=-1 WHERE id=?", (row["id"],))
        elif row["current_doc_id"] in current and row["identity"].startswith(evidence_prefix()) and bool(args["terms"]) == (mode == "terms"):
            usable.append(row)
    rows = usable
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
            # Recheck the complete prospective support before promotion and measure
            # the same state/certificate/cache gate that live application pays.
            prospective = dict(s, arm=candidate, support=[r["id"] for r in rows],
                               signature=signature(), cost_limit=1.0)
            new = []
            for r in holdout:
                gate_started = time.monotonic()
                state(db, mode)
                serving_mode()
                ready = certified(db, prospective) and vectors._qcache_get(db, r["query"]) is not None
                gate_cost = time.monotonic() - gate_started
                new.append(json.loads(r["costs"])[candidate] + gate_cost if ready else float("inf"))
            new.sort()
            # Relative serving-cost guard is a product budget, never a learned relevance weight.
            at = min(len(old) - 1, math.ceil(0.95 * len(old)) - 1)
            if new[at] <= old[at] * 1.1:
                s = _save(db, s, candidate, "independent_read_holdout", through, new[at] * 1.1, support=[r["id"] for r in rows])
                return {"arm": s["arm"], "observations": len(rows), "decision": "changed"}
        # Confirmation has now been inspected: consume it, even when it rejected the candidate.
        s = _save(db, s, incumbent, "holdout_rejected", through)
    return {"arm": s["arm"], "observations": len(rows), "decision": "retained"}
