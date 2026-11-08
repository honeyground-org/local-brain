"""A rejected calibration must not repeat on every prompt. (local · budget 0 · a throwaway index)

Measured 2026-11-08: the stored ruler (7.32) was measured on 290 documents; the corpus had grown to 347
(+20%, over the 10% tolerance), so `threshold()` — which the prompt hook calls — distrusted it and
calibrated again. The guard kept 7.32 (the new 7.61 was worse) and returned without recording the corpus,
so the next prompt calibrated again: ~3.5 s on every prompt against a 5 s hook timeout.

This check pins the measurement and the bench so only the bookkeeping is measured:
  ① a rejected calibration records today's corpus, and the next `threshold()` call does not calibrate
  ② control: a deployed calibration still records the corpus as before
  ③ control: with the corpus still drifted and no rejection, `threshold()` does calibrate (the drift check stays)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BRAIN_HOME"] = tempfile.mkdtemp(prefix="brain-restamp-")
os.environ["BRAIN_ADAPTIVE_LEARN"] = "0"

from brain import adaptive, calibrate as cal, store  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    db = store.connect()
    docs = {"n": 347}
    calls = {"measure": 0}
    new_value = {"t": 7.61}

    def fake_measure(_db, **kw):
        calls["measure"] += 1
        return {"threshold": new_value["t"], "docs": docs["n"], "real_prompts": 120, "fire_rate_pct": 50.0}

    bench = {7.61: {"hit": 33, "ff": 0, "n_a": 60, "n_c": 38, "auto": 0.55},
             7.32: {"hit": 36, "ff": 0, "n_a": 60, "n_c": 38, "auto": 0.60},
             7.00: {"hit": 40, "ff": 0, "n_a": 60, "n_c": 38, "auto": 0.70}}
    cal.measure = fake_measure
    cal._bench = lambda _db, cands, *a, **k: {c: bench[c] for c in cands}
    cal.bench_sample = lambda _db: ([], [], "gold")
    cal.older_ruler = lambda _db, mine: ""
    adaptive.step = lambda _db, floor: {"in_use": 1.0}
    cal.store.corpus_stats = lambda _db: {"docs": docs["n"], "avg_doclen": 1.0}

    # the state found on 2026-11-08
    store.set_meta(db, "hook_threshold", "7.32")
    store.set_meta(db, "hook_threshold_detail", json.dumps({"threshold": 7.32, "docs": 290}))
    check("setup: the stored ruler is distrusted (290 → 347 documents)", cal._measured_on_another_corpus(db))

    got = cal.threshold(db)
    check("① the worse value is rejected and 7.32 kept", abs(got - 7.32) < 1e-9 and calls["measure"] == 1,
          "threshold %.2f · measured %d×" % (got, calls["measure"]))
    detail = json.loads(store.get_meta(db, "hook_threshold_detail", "{}"))
    check("① the rejection records today's corpus", detail.get("docs") == 347 and detail.get("revalidated_at"),
          str(detail))
    for _ in range(3):
        cal.threshold(db)
    check("① the next prompts do not calibrate again", calls["measure"] == 1, "measured %d×" % calls["measure"])

    # ② control: a better value is deployed and records its corpus as before
    docs["n"] = 420
    new_value["t"] = 7.00
    got = cal.threshold(db)
    detail = json.loads(store.get_meta(db, "hook_threshold_detail", "{}"))
    check("② control: a better calibration is deployed with its corpus", abs(got - 7.00) < 1e-9
          and detail.get("docs") == 420 and "revalidated_at" not in detail, "%.2f %s" % (got, detail))

    # ③ control: the drift check still triggers a calibration when the corpus moves again
    before = calls["measure"]
    docs["n"] = 520
    cal.threshold(db)
    check("③ control: a corpus that moved again is calibrated again", calls["measure"] == before + 1,
          "measured %d×" % (calls["measure"] - before))

    print("\n%s" % ("PASS" if not FAIL else "FAIL: " + ", ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
