"""A side lane — a small index of its own whose answers are offered as reference, never ranked with memories.

## Why

Some documents describe state that lives somewhere else and is decided by someone else: a team's work ledger
and the reports its agents send (§team). Put into the main index they change the scale every memory is scored
on — a word's rarity (IDF) is counted over every document, so about 150 ledger documents moved the attachment
of one of six measured prompts in a lead session (2026-11-25), and the threshold is calibrated on that scale.
The person asked for the existing answers to stay exactly as they are, and for the ledger to be offered next
to them "for reference only — the ledger and the lead's judgement decide".

So a lane is a separate SQLite file. It uses the same tokenizer and the same field weights as the main index
(§textindex.term_frequencies, §store._W_*) and the same per-word scoring (§search._discriminative, §search._idf,
BM25 with §search.K1 and §search.B, the coverage factor), so a lane hit means what a main hit means. It is
searched after the main answer is final and shown in a block of its own.

## Its own ruler

A lane is small, so a word common in it is cheap, and a sentence about nothing in particular can still score.
The lane is calibrated the way the main ruler is, on its own corpus: the best score that a domain-neutral
sentence (the six-language `calib.noise.*` set plus `calibrate._NEUTRAL_EN`) or a query made of the lane's
commonest words reaches, times the main ruler's margin (`calibrate.MARGIN`), never below `calibrate.FLOOR_MIN`.
Naming an identifier a document carries (`ids`, e.g. a ledger item's id or alias) is a lookup, not a search,
and passes regardless of score — the same rule as a declared trigger in the main hook.

## Rules

- ⛔ A lane never writes to the main index and the main index never reads it.
- ⛔ The file is replaced whole (`os.replace`), so a reader never sees a half-built lane.
- The lane says what it measured (`meta`): documents, average length, floor, threshold, which probes set it.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import sqlite3
import time
from typing import Callable, Dict, Iterable, List, Optional, Sequence

FORMAT = 2                  # bump when the lane file or its ruler changes (forces a rebuild): 2 = controls in the lane's own languages
COMMON_QUERY_WORDS = 8      # words per common-word control query
COMMON_QUERIES = 4          # common-word control queries
EXCERPT_CHARS = 220         # the part of a hit shown in a reference block

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _fields(rel: str, text: str):
    """(name, title, description, body) of one Markdown document."""
    desc = ""
    body = text
    m = _FRONT.match(text)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if line.startswith("description:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    desc = json.loads(raw) if raw.startswith('"') else raw
                except ValueError:
                    desc = raw
    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    name = os.path.splitext(rel)[0].replace(os.sep, "/")
    return name, title, desc, body


def _tf(name: str, title: str, desc: str, body: str) -> Dict[str, float]:
    from brain import store, textindex
    return textindex.term_frequencies((
        (name.replace("/", " ").replace("_", " "), store._W_NAME),
        (title, store._W_TITLE),
        (desc, store._W_DESC),
        (body, store._W_BODY),
    ))


def _schema(db: sqlite3.Connection) -> None:
    db.executescript(
        "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);"
        "CREATE TABLE docs(id INTEGER PRIMARY KEY, rel TEXT NOT NULL UNIQUE, name TEXT NOT NULL,"
        " title TEXT NOT NULL, description TEXT NOT NULL, body TEXT NOT NULL, doclen REAL NOT NULL,"
        " ids TEXT NOT NULL DEFAULT '');"
        "CREATE TABLE postings(term TEXT NOT NULL, doc_id INTEGER NOT NULL, tf REAL NOT NULL,"
        " PRIMARY KEY(term, doc_id)) WITHOUT ROWID;"
        "CREATE TABLE terms(term TEXT PRIMARY KEY, df INTEGER NOT NULL) WITHOUT ROWID;"
        "CREATE TABLE ids(id TEXT NOT NULL, doc_id INTEGER NOT NULL, PRIMARY KEY(id, doc_id)) WITHOUT ROWID;")


def build(folder: str, path: str, ids_of: Optional[Callable[[str, str], Iterable[str]]] = None) -> dict:
    """Index every `*.md` under `folder` into a new lane file at `path`, calibrate it, and swap it in.

    `ids_of(rel, text)` names the identifiers a document answers to exactly (optional)."""
    t0 = time.time()
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    try:
        _schema(db)
        df: Dict[str, int] = {}
        total = 0.0
        n = 0
        for full in sorted(glob.glob(os.path.join(folder, "**", "*.md"), recursive=True)):
            rel = os.path.relpath(full, folder).replace(os.sep, "/")   # groups are read off it with "/"
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            name, title, desc, body = _fields(rel, text)
            tf = _tf(name, title, desc, body)
            doclen = sum(tf.values()) or 1.0
            ids = sorted({str(x) for x in (ids_of(rel, text) if ids_of else ()) if x})
            cur = db.execute("INSERT INTO docs(rel,name,title,description,body,doclen,ids) VALUES(?,?,?,?,?,?,?)",
                             (rel, name, title, desc, body, doclen, " ".join(ids)))
            did = cur.lastrowid
            db.executemany("INSERT INTO postings(term,doc_id,tf) VALUES(?,?,?)", [(g, did, v) for g, v in tf.items()])
            db.executemany("INSERT OR IGNORE INTO ids(id,doc_id) VALUES(?,?)", [(i.lower(), did) for i in ids])
            for g in tf:
                df[g] = df.get(g, 0) + 1
            total += doclen
            n += 1
        db.executemany("INSERT INTO terms(term,df) VALUES(?,?)", list(df.items()))
        meta = {"format": FORMAT, "docs": n, "avg_len": round(total / n, 3) if n else 1.0,
                "folder": folder, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _put(db, meta)
        db.commit()
        meta.update(_calibrate(db))
        _put(db, meta)
        db.commit()
    finally:
        db.close()
    os.replace(tmp, path)
    meta["seconds"] = round(time.time() - t0, 3)
    return meta


def _put(db: sqlite3.Connection, meta: dict) -> None:
    db.executemany("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)",
                   [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])


def info(path: str) -> dict:
    """What the lane at `path` measured, or {} when there is none (or it is from another format)."""
    try:
        db = _open(path)
    except sqlite3.Error:
        return {}
    try:
        out = {r[0]: json.loads(r[1]) for r in db.execute("SELECT k,v FROM meta")}
    except (sqlite3.Error, ValueError):
        return {}
    finally:
        db.close()
    return out if out.get("format") == FORMAT else {}


def _open(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise sqlite3.OperationalError("no lane at %s" % path)
    from pathlib import Path
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)
    db.execute("PRAGMA query_only=ON")
    return db


def _languages(db: sqlite3.Connection, n_docs: int) -> List[str]:
    """The languages the lane is written in, read off its corpus the way the main ruler reads its own
    (§calibrate.language_shares / corpus_languages). ⛔ Not every language the brain speaks: measured
    2026-11-25 on a Korean ledger, the Spanish control sentence scored 5.18 on `es` and `en` (locale codes
    in the ledger) and set the floor — a control in a language the corpus is not written in measures nothing."""
    from brain import calibrate, i18n, langdata
    share = {}
    for code, words in langdata.LANG_MARKERS.items():
        pcts = sorted(100.0 * _df(db, w) / max(1, n_docs) for w in words)
        mid = len(pcts) // 2
        share[code] = (pcts[mid] if len(pcts) % 2 else (pcts[mid - 1] + pcts[mid]) / 2) if pcts else 0.0
    present = [c for c in i18n.LANGS if share.get(c, 0.0) >= calibrate.LANG_PRESENT_PCT]
    if present:
        return present
    best = max(i18n.LANGS, key=lambda c: share.get(c, 0.0))
    return [best] if share.get(best, 0.0) > 0 else [i18n.DEFAULT]


def _df(db: sqlite3.Connection, term: str) -> int:
    row = db.execute("SELECT df FROM terms WHERE term=?", (term.lower(),)).fetchone()
    return int(row[0]) if row else 0


def _probes(langs: Sequence[str]) -> List[str]:
    """The domain-neutral control sentences of the lane's languages, plus four English (§calibrate.neutral_probes)."""
    from brain import calibrate, i18n
    out: List[str] = []
    for code in langs:
        cat = i18n.catalog(code)
        for i in range(1, 7):
            val = cat.get("calib.noise.%d" % i)
            if val and val not in out:
                out.append(val)
    return out + [q for q in calibrate._NEUTRAL_EN if q not in out]


def _common_queries(db: sqlite3.Connection, n_docs: int) -> List[str]:
    """Queries made of the lane's commonest words — the worst case of zero discrimination (§calibrate)."""
    from brain import calibrate
    floor = max(2, int(n_docs * calibrate.COMMON_DF_RATIO))
    words = [r[0] for r in db.execute("SELECT term FROM terms WHERE df >= ? ORDER BY df DESC, term LIMIT ?",
                                      (floor, COMMON_QUERY_WORDS * COMMON_QUERIES))]
    return [" ".join(words[i::COMMON_QUERIES]) for i in range(COMMON_QUERIES) if words[i::COMMON_QUERIES]]


def _calibrate(db: sqlite3.Connection) -> dict:
    from brain import calibrate
    n = int(json.loads(db.execute("SELECT v FROM meta WHERE k='docs'").fetchone()[0]))
    if not n:
        return {"floor": 0.0, "threshold": None, "probe": ""}
    langs = _languages(db, n)
    best, which = 0.0, ""
    for q in _probes(langs) + _common_queries(db, n):
        hits = _score(db, q, 1)
        if hits and hits[0]["score"] > best:
            best, which = hits[0]["score"], q
    thr = max(calibrate.FLOOR_MIN, round(best * calibrate.MARGIN, 2))
    return {"floor": round(best, 3), "threshold": thr, "margin": calibrate.MARGIN, "probe": which[:120],
            "languages": langs}


def _score(db: sqlite3.Connection, query: str, k: int) -> List[dict]:
    """Per-word BM25 with the main index's rules: grams of one word keep their best, coverage by rarity."""
    from brain import search, textindex
    meta = {r[0]: json.loads(r[1]) for r in db.execute("SELECT k,v FROM meta WHERE k IN ('docs','avg_len')")}
    n_docs = max(1, int(meta.get("docs") or 0))
    avg_len = max(1.0, float(meta.get("avg_len") or 1.0))
    pairs = textindex.query_terms(query)
    grams = sorted({g for _, gs in pairs for g in gs})
    if not grams:
        return []
    df_cache: Dict[str, int] = {}
    for i in range(0, len(grams), 500):
        part = grams[i:i + 500]
        for term, d in db.execute("SELECT term, df FROM terms WHERE term IN (%s)" % ",".join("?" * len(part)), part):
            df_cache[term] = d
    stop_df = max(2, int(n_docs * search.STOP_DF_RATIO))
    doc_score: Dict[int, float] = {}
    doc_words: Dict[int, List[str]] = {}
    word_idf: Dict[str, float] = {}
    for word, gs in pairs:
        disc = search._discriminative(gs, df_cache, stop_df)
        if not disc:
            continue
        word_idf[word] = max(search._idf(df_cache[g], n_docs) for g in disc)
        per_doc: Dict[int, float] = {}
        for g in disc:
            idf = search._idf(df_cache[g], n_docs)
            for did, tf, doclen in db.execute(
                    "SELECT p.doc_id, p.tf, d.doclen FROM postings p JOIN docs d ON d.id=p.doc_id WHERE p.term=?", (g,)):
                val = idf * tf / (tf + search.K1 * (1 - search.B + search.B * (doclen / avg_len)))
                if val > per_doc.get(did, 0.0):
                    per_doc[did] = val
        for did, val in per_doc.items():
            doc_score[did] = doc_score.get(did, 0.0) + val
            doc_words.setdefault(did, []).append(word)
    total = sum(word_idf.values()) or 1.0
    for did in list(doc_score):
        cov = sum(word_idf.get(w, 0.0) for w in doc_words[did]) / total
        doc_score[did] *= (search.COVERAGE_FLOOR + (1 - search.COVERAGE_FLOOR) * cov)
    ranked = sorted(doc_score.items(), key=lambda kv: -kv[1])[:k]
    return [{"doc_id": did, "score": round(s, 3), "words": doc_words[did]} for did, s in ranked]


def search(path: str, query: str, k: int = 3, groups: Optional[Sequence[str]] = None,
           ids: Iterable[str] = ()) -> List[dict]:
    """The lane's answers: documents named by an identifier in `ids` first, then those that clear the lane's
    own threshold. `groups` keeps only documents under those top folders (None = all). [] when there is no lane."""
    try:
        db = _open(path)
    except sqlite3.Error:
        return []
    try:
        meta = {r[0]: json.loads(r[1]) for r in db.execute("SELECT k,v FROM meta")}
        if meta.get("format") != FORMAT or not meta.get("docs"):
            return []
        thr = meta.get("threshold")

        def allowed(rel: str) -> bool:
            return groups is None or rel.split("/", 1)[0] in groups

        out: List[dict] = []
        seen = set()
        wanted = sorted({str(i).lower() for i in ids if i})
        if wanted:
            for did, ident in db.execute(
                    "SELECT doc_id, id FROM ids WHERE id IN (%s) ORDER BY doc_id" % ",".join("?" * len(wanted)), wanted):
                if did not in seen:
                    seen.add(did)
                    out.append({"doc_id": did, "score": None, "named": ident})
        for hit in _score(db, query, max(k * 4, 8)):
            if hit["doc_id"] in seen or thr is None or hit["score"] < thr:
                continue
            seen.add(hit["doc_id"])
            out.append(dict(hit, named=""))
        rows = []
        for hit in out:
            row = db.execute("SELECT rel,name,title,description,body FROM docs WHERE id=?", (hit["doc_id"],)).fetchone()
            if row is None or not allowed(row[0]):
                continue
            rows.append({"rel": row[0], "name": row[1], "group": row[0].split("/", 1)[0], "title": row[2],
                         "description": row[3], "excerpt": _excerpt(row[4]), "score": hit["score"],
                         "named": hit["named"], "threshold": thr})
            if len(rows) >= k:
                break
        return rows
    except (sqlite3.Error, ValueError):
        return []
    finally:
        db.close()


def _excerpt(body: str) -> str:
    """The first lines after the heading — for a ledger item that is its status and next step."""
    lines = [l for l in body.splitlines() if l.strip() and not l.startswith("# ")]
    return " · ".join(lines)[:EXCERPT_CHARS]
