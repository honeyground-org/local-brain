"""A small corpus for the storage-backend checks — notes with links, and synthetic embeddings.

Synthetic on purpose: no person's notes, no embedding engine, no key. What the store checks measure is
★whether two databases give the same answers to the same questions★, not whether the answers are good.

The graph has every shape a store must get right: a chain, a link back, a self-link, a link to a note
that does not exist, and a note nothing touches.
"""
from __future__ import annotations

import json
import os
import random

NOTES = {
    "alpha_overview": "See [[beta_details]] and [[gamma_notes]].",
    "beta_details": "Back to [[alpha_overview]]; continues in [[gamma_notes]].",
    "gamma_notes": "Leads to [[delta_plan]].",
    "delta_plan": "Mentions itself: [[delta_plan]].",
    "epsilon_draft": "Points at [[missing_note_that_does_not_exist]].",
    "zeta_alone": "Nothing links here and it links nowhere.",
    "eta_index": "[[alpha_overview]] [[delta_plan]] [[zeta_alone]]",
    "theta_log": "Linked from nothing, links to [[eta_index]].",
}


def write_corpus(root: str) -> str:
    """Write the notes and a config pointing at them; return the memory folder."""
    mem = os.path.join(root, "memory")
    os.makedirs(mem, exist_ok=True)
    for name, body in NOTES.items():
        write_note(mem, name, body)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": mem, "include": ["*.md"], "max_depth": 1}]}, fh)
    return mem


def write_note(mem: str, name: str, body: str) -> None:
    with open(os.path.join(mem, name + ".md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: fixture note %s\n---\n\n%s\n" % (name, name, body))


def fill_vectors(db) -> int:
    """Deterministic unit vectors for every document (1–3 chunks each) under the current model tag."""
    from brain import vectors
    vectors.ensure_table(db)
    rows = []
    for did, name, sha in db.execute("SELECT id, name, sha FROM docs ORDER BY id"):
        rnd = random.Random(name)
        for c in range(1 + rnd.randrange(3)):
            v = vectors._unit([rnd.uniform(-1, 1) for _ in range(vectors.DIM)])
            rows.append((did, c, sha, vectors.model_tag(), vectors.DIM, v.tobytes()))
    with db:
        db.execute("DELETE FROM vectors")
        db.executemany("INSERT INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES(?,?,?,?,?,?)", rows)
    return len(rows)


def fill_questions(db, n: int = 12) -> None:
    """Cached query vectors — what `stores.check` compares on (no embedding call)."""
    from brain import vectors
    vectors._qcache_get(db, "")                      # creates the cache table (a put alone would fail quietly)
    rnd = random.Random("questions")
    for i in range(n):
        vectors._qcache_put(db, "fixture question %d" % i,
                            vectors._unit([rnd.uniform(-1, 1) for _ in range(vectors.DIM)]))
