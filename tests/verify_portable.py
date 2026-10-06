"""Device-transfer check — ★does what was earned move across, and calibration doesn't★.

## Why this check exists

Two failures are silent:

① **Calibration moves across too.** `hook_threshold` is ★a value measured on this corpus★ (IDF depends
   on the document count N). Plant it on someone else's machine and the hook either goes silent forever
   or floods with noise — ★with no error★. This repo has already been burned by this twice.
   ⛔ And the first draft actually leaked — it used a "keys not to move" block-list, and
   `hook_threshold_detail` · `vec_min_cos` · `last_index` weren't on the list, so they went out
   as-is. ★A block-list leaks quietly every time a new key appears★ → it was inverted to an allow-list.

② **A key keyed to a rowid moves across.** `vectors` uses `doc_id` (=`docs.rowid`), but
   on a different device that number is ★guaranteed to differ★. This repo has already lost usage
   history for 42 of the top 52 entries in this exact trap (no error, no log).

So this check looks not at "is there code that moves things" but at ★what is actually inside the
bundle★ and ★does it actually come alive on a clean machine★.

⛔ Never touches real data — reads the real DB, writes to a temporary `BRAIN_HOME`.
"""
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAIL = []


def check(ok, label, detail=""):
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAIL.append(label)
    return ok


def head(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


def main():
    from brain import portable, store

    head("① what is inside the bundle (exported from the real DB — read-only)")
    tmpdir = tempfile.mkdtemp(prefix="brain-portable-")
    bundle = os.path.join(tmpdir, "bundle.json")
    db = store.connect()
    r = portable.export(db, bundle)
    db.close()
    if not r["vectors"]:
        from tests import _needs
        _needs.skip("needs notes that were embedded (it carries real vectors across machines)",
                    "choose an embedding engine (`brain engines --set embed …`) and run `brain vec ingest`")
    check(r["vectors"] > 0, "carries vectors (days' worth of remote embedding)",
          "%d chunk(s) / %d doc(s)" % (r["vectors"], r["docs_with_vectors"]))

    with open(bundle, encoding="utf-8") as fh:
        b = json.load(fh)

    check(b["meta"] == {}, "⛔ ★carries no meta (calibration · index state · decisions)★",
          "keys carried %s" % (sorted(b["meta"]) or "none"))

    raw = json.dumps(b, ensure_ascii=False)
    for bad in ("hook_threshold", "rerank_min_score", "lexicon_knobs", "vec_min_cos",
                "noise_floor"):
        check(('"%s"' % bad) not in raw, "calibration `%s` is not in the bundle" % bad)

    users = sorted(set(re.findall(r"/Users/[A-Za-z0-9_.-]+", raw)))
    check(not users, "⛔ no absolute path with a person's name is in the bundle (carried as `~`)",
          "leaked %s" % (users[:3] or "none"))

    check("doc_id" not in raw, "⛔ ★never keys by rowid★ (carried by name)")
    check(all("name" in v for v in b["vectors"][:50]), "a vector carries the document's ★name★")

    head("② does it come alive on a clean machine (a temporary BRAIN_HOME)")
    home = os.environ.get("BRAIN_HOME")
    os.environ["BRAIN_HOME"] = os.path.join(tmpdir, "home")
    try:
        for m in [k for k in list(sys.modules) if k.startswith("brain")]:
            del sys.modules[m]
        from brain import portable as pt2, store as st2, vectors as vc2
        db2 = st2.connect()

        # ★before★ indexing — the file comes first
        pre = pt2.imp(db2, bundle, dry_run=True)
        check(pre["vectors_in"] == 0 and pre["vectors_no_doc"] > 0,
              "before indexing, ★not a single★ vector comes in (the file is the source of truth)",
              "no-doc count %d" % pre["vectors_no_doc"])

        st2.reindex(db2, recalibrate=False)
        got = pt2.imp(db2, bundle, dry_run=False)
        check(got["vectors_in"] > 0, "vectors come in after indexing",
              "%d chunk(s)" % got["vectors_in"])
        check(got["vectors_stale_sha"] == 0,
              "no sha mismatch, since the body is the same", "%d" % got["vectors_stale_sha"])

        n_doc = db2.execute("SELECT COUNT(DISTINCT doc_id) c FROM vectors").fetchone()["c"]
        check(n_doc == r["docs_with_vectors"],
              "★the count of documents with a vector matches the other side★ (found by name even with a different rowid)",
              "%d ↔ %d" % (n_doc, r["docs_with_vectors"]))

        for t, want in (("lexicon", r["lexicon"]), ("rule_judged", r["rule_judged"])):
            n = db2.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
            check(n == want, "%s carried across completely" % t, "%d ↔ %d" % (n, want))

        # ★does it reject a stale vector★ — shakes the sha and imports again
        db2.execute("UPDATE vectors SET sha='deadbeef' WHERE chunk_no=0")
        db2.commit()
        stale = pt2.imp(db2, bundle, dry_run=True)
        check(stale["vectors_stale_sha"] > 0,
              "⛔ ★discards a chunk whose body differs★ (never picks a document based on content that doesn't exist)",
              "would discard %d chunk(s)" % stale["vectors_stale_sha"])

        db2.close()
    finally:
        if home is None:
            os.environ.pop("BRAIN_HOME", None)
        else:
            os.environ["BRAIN_HOME"] = home
        shutil.rmtree(tmpdir, ignore_errors=True)

    head("result")
    if FAIL:
        print("❌ %d failure(s): %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — what was earned moves across, and calibration doesn't")
    return 0


if __name__ == "__main__":
    sys.exit(main())
