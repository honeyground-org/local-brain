#!/usr/bin/env python3
"""The meaning gate is measured ★without labels★ when a person has none — and never before it may be.

## Why this check exists (2026-11-04)

A fresh installation has no labelled sample (`short.json` is personal and never shipped). The gate was
measured only from that sample, so it stayed at its silent fallback forever: vectors built, the vector
database synced, and meaning-based recall added nothing — with `brain vec calibrate` dying on a missing
file. This check holds the replacement in place:

  ① a corpus too small for a sample → `brain vec calibrate` says so and exits 1 (no traceback)
  ② ★control★ — before any measurement the gate is silent (the state the old code never left)
  ③ ★control★ — with the vectors under the arming coverage, nothing is measured
  ④ with the vectors built and no labels, the proxy sample sets the gate (kind "proxy")
  ⑤ one outlying control does not decide a proxy gate; a labelled gate keeps the plain maximum
  ⑥ a labelled sample, once it exists, takes over

The stub embedder knows no meaning — this checks wiring and arithmetic, never quality.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-vec-proxy-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ["BRAIN_EVAL_DIR"] = os.path.join(_TMP, "eval")
os.environ["BRAIN_EMBED_PROVIDER"] = "stub"
for _k in ("BRAIN_EMBED_MIN_RATIO", "BRAIN_EMBED_MIN_AGREE_RATIO", "BRAIN_EMBED_PROXY_NOISE"):
    os.environ.pop(_k, None)

from brain import cli, store, vectors  # noqa: E402

FAILS = []

# Words for the fixture notes. A note's body is built from them deterministically; the proxy then cuts its
# own fragments out of those bodies (nothing here is an evaluation question).
TOPICS = ["harbour", "ferry", "lantern", "orchard", "glacier", "copper", "violet", "meadow", "falcon",
          "canyon", "ember", "willow", "quartz", "tundra", "saffron", "pebble", "cobalt", "thistle",
          "marble", "juniper", "cedar", "lagoon", "basalt", "heron", "mosaic"]
VERBS = ["checks", "restarts", "measures", "records", "rebuilds", "archives", "compares", "explains"]
THINGS = ["the nightly backup", "every release note", "the shared calendar", "the invoice batch",
          "the sensor readings", "the onboarding guide", "the staging database", "the weekly report"]


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:200]) if detail else ""))
    if not ok:
        FAILS.append(label)


def _write_notes(n: int) -> None:
    os.makedirs(_MEM, exist_ok=True)
    for i in range(n):
        a, b = TOPICS[i % len(TOPICS)], TOPICS[(i * 7 + 3) % len(TOPICS)]
        sents = []
        for j in range(6):
            sents.append("Crew %s %s %s near %s on ticket %d."
                         % (TOPICS[(i + j) % len(TOPICS)], VERBS[(i + j) % len(VERBS)],
                            THINGS[(i * 3 + j) % len(THINGS)], TOPICS[(i * 5 + j * 3) % len(TOPICS)],
                            i * 10 + j))
        name = "note_%02d_%s" % (i, a)
        with open(os.path.join(_MEM, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: fixture note %d about %s and %s\nmetadata:\n  type: project\n---\n\n%s\n"
                     % (name, i, a, b, " ".join(sents)))


def _cli(*args) -> tuple:
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = cli.main(list(args))
    except SystemExit as exc:                            # argparse-style exits
        code = exc.code
    except Exception as exc:                             # noqa: BLE001 — a traceback is the failure we look for
        return "traceback", "%s: %s" % (type(exc).__name__, exc)
    return code, buf.getvalue()


def main() -> int:
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)

    print("=" * 72 + "\n① too small for a sample — a message, not a traceback\n" + "=" * 72)
    _write_notes(3)
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    vectors.build(db)
    code, out = _cli("vec", "calibrate")
    check("`brain vec calibrate` exits 1 with a message", code == 1 and "eval-init" in out, (code, out[:120]))

    print("\n" + "=" * 72 + "\n② ③ controls — silent until it may be measured\n" + "=" * 72)
    _write_notes(60)
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    check("before any measurement the gate is silent",
          vectors.min_ratio(db) >= vectors.MIN_RATIO_FALLBACK and vectors.gate_kind(db) == "none",
          vectors.min_ratio(db))
    vectors.build(db, limit_docs=10)
    cov = vectors.coverage(db)
    got = vectors.recalibrate_if_free(db)
    check("under the arming coverage nothing is measured",
          got is None and vectors.min_ratio(db) >= vectors.MIN_RATIO_FALLBACK,
          "%d/%d embedded" % (cov["embedded"], cov["docs"]))

    print("\n" + "=" * 72 + "\n④ no labels — the proxy sets the gate\n" + "=" * 72)
    vectors.build(db)
    got = vectors.recalibrate_if_free(db)
    check("the gate is measured from the proxy sample",
          bool(got) and got["kind"] == "proxy" and vectors.gate_kind(db) == "proxy",
          {k: (got or {}).get(k) for k in ("kind", "min_ratio", "control_n", "positives_n")})
    check("the measured gate is a real number below the silent fallback",
          vectors.min_ratio(db) < vectors.MIN_RATIO_FALLBACK, vectors.min_ratio(db))
    want_allow = int(got["control_n"] * vectors.PROXY_NOISE_SHARE) if got else -1
    check("a proxy gate allows the configured share of controls above it",
          bool(got) and got["allow_noise"] == want_allow, (got or {}).get("allow_noise"))
    code, out = _cli("vec", "status")
    check("`brain vec status` names the sample that set the gate", '"gate_sample": "proxy"' in out, out[-160:])

    print("\n" + "=" * 72 + "\n⑤ one control does not decide a proxy gate\n" + "=" * 72)
    check("allow 0 is the plain maximum", vectors._ceiling([1.1, 1.9, 1.3], 0) == 1.9)
    check("allow 1 steps over one outlier", vectors._ceiling([1.1, 1.9, 1.3], 1) == 1.3)
    check("control: fewer values than allowed → nothing to step over", vectors._ceiling([1.4], 1) == 0.0)

    print("\n" + "=" * 72 + "\n⑥ a labelled sample takes over\n" + "=" * 72)
    names = [r["name"] for r in db.execute("SELECT name FROM docs ORDER BY id LIMIT 3")]
    os.makedirs(os.environ["BRAIN_EVAL_DIR"], exist_ok=True)
    with open(os.path.join(os.environ["BRAIN_EVAL_DIR"], "short.json"), "w", encoding="utf-8") as fh:
        json.dump({"A_memory_needed": [{"q": "who restarts the nightly backup", "gold": names[0]}],
                   "C_no_memory_needed": ["thanks, that is all", "good morning"]}, fh)
    got = vectors.recalibrate_if_free(db)
    check("the labelled sample is used, with the plain maximum",
          bool(got) and got["kind"] == "labelled" and got["allow_noise"] == 0
          and vectors.gate_kind(db) == "labelled",
          {k: (got or {}).get(k) for k in ("kind", "allow_noise", "control_n")})

    print("\n" + ("❌ %d failed" % len(FAILS) if FAILS else "✅ all passed"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
