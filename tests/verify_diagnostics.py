"""Diagnostics check — ★is what can be fixed separated from what can't★.

## Why this check exists

This repository's rule: *"A diagnostic that counts what cannot be fixed becomes a wolf within a
day — a person checks it a few times, finds nothing to do, and stops looking after that."*

⛔ That distinction lived ★only inside the dashboard★ (2026-08-10). `brain status` printed five
   numbers with no distinction, and on 2026-08-31 whoever read them — the model that built this
   repository itself — read it as "459 things to fix". ★A rule in two places gets fixed in one.★

Now the judgement lives in one place, `health.CHECK_KIND`, and this check verifies ★the two screens don't drift apart★.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import health, store                           # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    db = store.connect()
    st = health.status(db)
    checks = st["distortion_checks"]
    print("=" * 72 + "\ndiagnostics — issues and metrics\n" + "=" * 72)

    check("every diagnostic is classified as an ★issue/metric★",
          all("kind" in v for v in checks.values()),
          "not classified: %s" % [k for k, v in checks.items() if "kind" not in v])

    issues = {k: v for k, v in checks.items() if v["kind"] == "issue"}
    metrics = {k: v for k, v in checks.items() if v["kind"] == "metric"}
    check("both issues and metrics exist", issues and metrics,
          "issues %d · metrics %d" % (len(issues), len(metrics)))

    # ⛔ ★an issue must always carry an action★ — without one it's a metric, not an issue.
    noact = [k for k, v in issues.items() if not v.get("action")]
    check("★every issue carries a 'what to do'★", not noact, "missing: %s" % noact)

    # ⛔ ★a metric must always carry the grounds for 'why it's not an issue'★ — measurement, not taste.
    nowhy = [k for k, v in metrics.items() if not v.get("why_metric")]
    check("★every metric carries 'why it's not an issue'★", not nowhy, "missing: %s" % nowhy)

    # blocks reversal — does the grounds cite a measurement
    for k, v in metrics.items():
        check("metric `%s`'s grounds cite a ★measurement★" % k,
              any(t in v["why_metric"] for t in ("%", "reached", "denominator", "log=False")),
              v["why_metric"][:56])

    # does a big number leak in as an issue — where wolves get called
    big = [(k, v["count"]) for k, v in issues.items() if v.get("count", 0) > 300]
    check("★nothing over 300 is caught as an issue★ (the size that calls a wolf)",
          not big, "%s" % big)

    print("\n" + "=" * 72 + "\ndo the two screens read the same source\n" + "=" * 72)
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "brain", "dashboard.py"), encoding="utf-8").read()
    check("the dashboard has the canonical source written as `health.CHECK_KIND`", "CHECK_KIND" in src)

    # ⛔ ★this check also used to carry its own constant★ (2026-08-31) — it hard-coded
    #    `{"dangling","stale","dup"}` and asked "do the two screens agree". A check that doesn't
    #    read the canonical source can't catch drift (it calls the side the source changed to wrong). Now it reads ★what actually gets drawn★.
    from brain import dashboard                            # noqa: E402
    data = dashboard.collect(db)
    dash_issues = {c["key"] for c in data["issues"]}
    dash_metrics = {c["key"] for c in data["metrics"]}
    want = {"dangling_links": "dangling", "stale_evidence": "stale",
            "duplicate_candidates": "dup", "orphans": "orphans",
            "never_recalled": "never"}
    mapped = {want[k] for k in issues if k in want}
    check("★the issues the dashboard draws match health's issues★",
          mapped == dash_issues, "health→%s ↔ dashboard→%s" % (sorted(mapped),
                                                               sorted(dash_issues)))
    mapped_m = {want[k] for k in metrics if k in want}
    check("★the metrics the dashboard draws match health's metrics★",
          mapped_m == dash_metrics, "health→%s ↔ dashboard→%s" % (sorted(mapped_m),
                                                                  sorted(dash_metrics)))
    check("★every diagnostic is drawn on one screen or the other★ (nothing disappears silently)",
          set(checks) == set(want), "missing: %s" % sorted(set(checks) - set(want)))

    # a dangling link splits further on its own
    dl = checks.get("dangling_links", {})
    check("a dangling link splits further into ★can be fixed / needs a person★",
          "split" in dl and "typo" in dl["split"],
          "name issue %d · no memory found %d" % (len(dl.get("split", {}).get("typo", [])),
                                    len(dl.get("split", {}).get("missing", []))))

    print("=" * 72)
    if FAIL:
        print("❌ %d short: %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ all passed — the report keeps 'what to do' and 'metrics' apart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
