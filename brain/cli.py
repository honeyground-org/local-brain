"""CLI — the door where humans and verification scripts touch the brain directly.

It calls ★the same core★ as the MCP server (server.py). Split into two copies and only
one of them gets fixed, and then "it works in the terminal but not in the session" begins.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import i18n   # ★screen text comes from the catalog — one way to call it: `i18n.t(`★

from brain import calibrate as calib
from brain import health, search, store


def _p(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))



# ── ★All commands at a glance★ — grouped by purpose ──────────────────────────
# ⛔ argparse's default help ★dumps 24 names in one line★ — you cannot tell what to type first.
#    What a person looks for is not "a list of commands" but ★"the job I am trying to do"★.
#    So each group is titled by ★the job★, not by command names.
# ★screen text comes from the catalog★ — each entry here is an i18n key, resolved at
# render time in `_help_body()` so it always reflects the current language.
# ★Declared for the key scanner★ — the HELP_GROUPS table below holds keys as data and `t()` is called on a
# variable, so `verify_i18n` cannot see them through `t("...")` (40 keys reported unused on 2026-09-08 —
# hidden behind the check's 5-item display until `calib.noise.*` was claimed). Same shape as dashview's UI_PREFIX.
I18N_PREFIX = ("cli.help.cmd.", "cli.help.group.")

HELP_GROUPS = (
    ("cli.help.group.everyday", (
        ("status", "cli.help.cmd.status"),
        ("dashboard", "cli.help.cmd.dashboard"),
        ("score --compare", "cli.help.cmd.score_compare"),
        ("recall <text>", "cli.help.cmd.recall"),
        ("recent", "cli.help.cmd.recent"),
    )),
    ("cli.help.group.measure", (
        ("score", "cli.help.cmd.score"),
        ("score --axes", "cli.help.cmd.score_axes"),
        ("score --full", "cli.help.cmd.score_full"),
        ("score --history", "cli.help.cmd.score_history"),
        ("budget", "cli.help.cmd.budget"),
        ("i18n --check", "cli.help.cmd.i18n_check"),
    )),
    ("cli.help.group.feed", (
        ("index", "cli.help.cmd.index"),
        ("detect", "cli.help.cmd.detect"),
        ("suggest", "cli.help.cmd.suggest"),
        ("triggers", "cli.help.cmd.triggers"),
        ("neighbors <name>", "cli.help.cmd.neighbors"),
        ("why <file>", "cli.help.cmd.why"),
    )),
    ("cli.help.group.corpus", (
        ("sources", "cli.help.cmd.sources"),
        ("add <path>", "cli.help.cmd.add"),
        ("remove <name>", "cli.help.cmd.remove"),
        ("index-audit", "cli.help.cmd.index_audit"),
    )),
    ("cli.help.group.semantic", (
        ("engines", "cli.help.cmd.engines"),
        ("stores", "cli.help.cmd.stores"),
        ("vec", "cli.help.cmd.vec"),
        ("calibrate", "cli.help.cmd.calibrate"),
        ("eval-init", "cli.help.cmd.eval_init"),
    )),
    ("cli.help.group.rules", (
        ("rules", "cli.help.cmd.rules"),
        ("rules --discover", "cli.help.cmd.rules_discover"),
        ("rules --stale", "cli.help.cmd.rules_stale"),
        ("rules --disable <id>", "cli.help.cmd.rules_disable"),
    )),
    ("cli.help.group.moving", (
        ("export", "cli.help.cmd.export"),
        ("import <bundle>", "cli.help.cmd.import_"),
    )),
    ("cli.help.group.safety", (
        ("privacy", "cli.help.cmd.privacy"),
        ("feedback", "cli.help.cmd.feedback"),
    )),
)


def engines_text(db) -> str:
    """`brain engines` — each role, the engine filling it, where that choice came from, what it is sent.

    ⛔ It never prints a key — only whether one is there. And a key that is merely present is shown as
       ★not chosen★: presence is not consent (§engines).
    """
    from brain import engines as _en, rerank as _rr
    rows = _en.describe()
    out = [i18n.t("cli.engines.title"), ""]
    for r in rows:
        role = r["role"]
        what = (i18n.t("cli.engines.role.embed.what") if role == "embed"
                else i18n.t("cli.engines.role.judge.what"))
        out.append("  %-6s %s" % (role, what))
        if role == "judge":
            out.append("         " + i18n.t("cli.engines.role.judge.used_by"))
        if r["provider"] == "none":
            eng = i18n.t("cli.engines.none")
        else:
            eng = "%s · %s" % (r["provider"], r["model"] or "?")
            if r["model_source"] == "default":
                eng += " " + i18n.t("cli.engines.default_model")
            if r["base_url"] and r["base_url"] != _en.PROVIDERS.get(r["provider"], {}).get("base"):
                eng += " @ " + r["base_url"]
        src = (i18n.t("cli.engines.src.env") if r["source"] == "env"
               else i18n.t("cli.engines.src.config") if r["source"] == "config"
               else i18n.t("cli.engines.src.unset"))
        out.append("         " + i18n.t("cli.engines.engine_line", engine=eng, source=src))
        if r["provider"] not in ("none", "stub") and not r.get("error"):
            where = i18n.t("cli.engines.stays_local") if r["local"] else r["host"]
            out.append("         " + (i18n.t("cli.engines.role.embed.sends", where=where)
                                      if role == "embed"
                                      else i18n.t("cli.engines.role.judge.sends", where=where)))
        if r["provider"] == "none":
            status = i18n.t("cli.engines.status.off")
        elif r.get("error"):
            status = i18n.t("cli.engines.status.unknown", provider=r["provider"])
        elif not r["has_key"]:
            status = i18n.t("cli.engines.status.no_key", provider=r["provider"])
        elif role == "judge":
            ms = _rr.min_score(db)
            status = (i18n.t("cli.engines.status.judge_ready", thr="%.1f" % ms) if ms <= 10.0
                      else i18n.t("cli.engines.status.judge_uncalibrated"))
        else:
            status = i18n.t("cli.engines.status.ready")
        out.append("         " + i18n.t("cli.engines.status_line", status=status))
        out.append("")
    out.append(i18n.t("cli.engines.choices_embed"))
    out.append(i18n.t("cli.engines.choices_judge"))
    out.append(i18n.t("cli.engines.how_to_change"))
    unused = [k for k in _en.keys_present() if all(r["provider"] != k for r in rows)]
    if unused:
        out.append(i18n.t("cli.engines.keys_unused", names=", ".join(unused)))
    return "\n".join(out)


def stores_text(db) -> str:
    """`brain stores` — which database answers each kind of question, what it is sent, and whether it serves.

    ⛔ Never prints a password — only where it is looked for, and only when it is missing.
    """
    import urllib.parse as _up
    from brain import engines as _en, stores as _st
    out = [i18n.t("cli.stores.title"), ""]
    for r in _st.describe(db):
        role = r["role"]
        what = (i18n.t("cli.stores.role.vector.what") if role == "vector"
                else i18n.t("cli.stores.role.graph.what"))
        out.append("  %-6s %s" % (role, what))
        backend = r["backend"] + ((" @ " + r["url"]) if r.get("url") else "")
        source = (i18n.t("cli.stores.src.env") if r["source"] == "env"
                  else i18n.t("cli.stores.src.config") if r["source"] == "config"
                  else i18n.t("cli.stores.src.default"))
        out.append("         " + i18n.t("cli.stores.backend_line", backend=backend, source=source))
        if r.get("error"):
            out.append("         " + i18n.t("cli.stores.unknown", backend=r["backend"]))
        elif r["backend"] == "sqlite":
            out.append("         " + i18n.t("cli.stores.local_copy", n=r["local_count"]))
        else:
            where = (i18n.t("cli.stores.stays_local") if r["local"]
                     else _up.urlsplit(r.get("url") or "").hostname or r.get("url"))
            if role == "vector":
                out.append("         " + i18n.t("cli.stores.sends.vector", where=where))
            else:
                names = i18n.t("cli.stores.sends.names") if (r.get("options") or {}).get("names") else ""
                out.append("         " + i18n.t("cli.stores.sends.graph", where=where, names=names))
            ping = r.get("ping") or {}
            if not ping.get("ok"):
                out.append("         " + i18n.t("cli.stores.status.unreachable", error=ping.get("error", "")))
            else:
                sync = i18n.t("cli.stores.in_sync") if r.get("in_sync") else i18n.t("cli.stores.not_synced")
                out.append("         " + i18n.t("cli.stores.status.reachable", version=ping.get("version", ""),
                                                held=r.get("held", 0), local=r["local_count"], sync=sync))
            spec = _st.BACKENDS.get(r["backend"], {})
            if r["backend"] == "neo4j" and not _st.secret(r):
                out.append("         " + i18n.t("cli.stores.secret_hint", env=spec["secret_env"],
                                                field=spec["secret_field"], path=_en.secrets_path()))
            h = r.get("health") or {}
            if h.get("ok") is False and h.get("error"):
                out.append("         " + i18n.t("cli.stores.last_error", at=h.get("at", ""), error=h["error"][:160]))
        out.append("")
    out.append(i18n.t("cli.stores.choices"))
    out.append(i18n.t("cli.stores.how_to_change"))
    return "\n".join(out)


def stores_sync_text(res: dict) -> str:
    out = [i18n.t("cli.stores.sync.title")]
    for role, r in res.items():
        if r.get("skipped"):
            out.append("  " + i18n.t("cli.stores.sync.on_local_copy", role=role))
        elif r.get("error"):
            out.append("  " + i18n.t("cli.stores.sync.error", role=role, error=r["error"]))
        elif r.get("unchanged"):
            out.append("  " + i18n.t("cli.stores.sync.unchanged", role=role, target=r["target"]))
        elif role == "vector":
            out.append("  " + i18n.t("cli.stores.sync.vector", role=role, docs=r["sent_docs"], chunks=r["sent_chunks"],
                                     deleted=r["deleted_docs"], target=r["target"]))
        else:
            out.append("  " + i18n.t("cli.stores.sync.graph", role=role, nodes=r["sent_nodes"], edges=r["sent_edges"],
                                     gone_nodes=r["deleted_nodes"], gone_edges=r["removed_edges"], target=r["target"]))
    return "\n".join(out) + "\n"


def stores_check_text(res: dict) -> str:
    out = [i18n.t("cli.stores.check.title")]
    same = lambda ok: i18n.t("cli.stores.check.same") if ok else i18n.t("cli.stores.check.differ")  # noqa: E731
    for role, r in res.items():
        if r.get("skipped"):
            out.append("  " + i18n.t("cli.stores.check.on_local_copy", role=role))
        elif r.get("error"):
            out.append("  " + i18n.t("cli.stores.check.error", role=role, error=r["error"]))
        elif role == "vector" and not r.get("queries"):
            out.append("  " + i18n.t("cli.stores.check.no_questions", role=role))
        elif role == "vector":
            out.append("  " + i18n.t("cli.stores.check.vector", role=role, n=r["queries"], top1=r["top1_agree"],
                                     overlap="%.0f%%" % (100 * r["overlap_at_10"]), delta=r["max_score_delta"],
                                     ms_local=r["ms_local"], ms_store=r["ms_store"], backend=r["backend"]))
        else:
            out.append("  " + i18n.t("cli.stores.check.graph", role=role, same=r["neighbors_same"], docs=r["docs"],
                                     edges_store=r["edges_store"], edges_local=r["edges_local"],
                                     targets=same(r["targets_same"]), ms_local=r["ms_local"],
                                     ms_store=r["ms_store"], backend=r["backend"]))
    return "\n".join(out) + "\n"


def _help_tail() -> str:
    return "\n".join([
        "",
        i18n.t("cli.help.tail.jobs_title"),
        i18n.t("cli.help.tail.job_vec"),
        i18n.t("cli.help.tail.job_rules"),
        i18n.t("cli.help.tail.verify"),
        i18n.t("cli.help.tail.launchd"),
        "       <repo>/install/launchd.sh",
        "",
        i18n.t("cli.help.tail.where_title"),
        i18n.t("cli.help.tail.where_code"),
        i18n.t("cli.help.tail.where_data", home=store.brain_home()),
        i18n.t("cli.help.tail.where_page", home=store.brain_home()),
    ])


def _wrap(text: str, width: int = 92) -> list:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out


def _help_text() -> str:
    import os as _os
    import shutil as _sh
    here = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    on_path = _sh.which("brain")
    out = [i18n.t("cli.help.title"), ""]
    if on_path:
        out += [i18n.t("cli.help.usage", path=on_path), ""]
    else:
        # ⛔ ★If it is not on PATH, say so and say how to fix it★ — telling someone who just met
        #    `command not found: brain` to "cd there and run ./bin/brain" is a detour, not an answer.
        out += [i18n.t("cli.help.path_missing"),
                "       %s/install/path.sh" % here,
                i18n.t("cli.help.path_until", path="%s/bin/brain" % here), ""]
    return "\n".join(out + _help_body())


def _help_body() -> list:
    out = []
    for title_key, items in HELP_GROUPS:
        title = i18n.t(title_key)
        out.append("─ %s %s" % (title, "─" * max(0, 62 - len(title) * 2)))
        for cmd, desc_key in items:
            out.append("  %-22s %s" % (cmd, i18n.t(desc_key)))
        out.append("")
    out.append(_help_tail().strip("\n"))
    out.append("")
    out.append(i18n.t("cli.help.footer"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="brain", description="local brain")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="Index (incremental). --full rebuilds everything")
    p.add_argument("--full", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("recall", help="Recall")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=8)
    p.add_argument("-t", "--terms", nargs="*", default=[], help="expansion terms")
    p.add_argument("--kind", nargs="*", default=None)
    p.add_argument("--source", nargs="*", default=None)
    p.add_argument("--no-graph", action="store_true")
    p.add_argument("--brief", action="store_true")

    p = sub.add_parser("why", help="Why this file looks the way it does — purpose, principles, changes")
    p.add_argument("path")
    p.add_argument("-n", type=int, default=3, help="how many commits to show")

    p = sub.add_parser("detect", help="Find what to remember, with evidence. --add registers it")
    p.add_argument("--add", action="store_true", help="register what was found in the config and index it")
    p.add_argument("--min-files", type=int, default=3)

    p = sub.add_parser("sources", help="Registered corpora + measured reality (files · indexed documents)")

    p = sub.add_parser("add", help="Add a corpus — attach a folder of documents or notes")
    p.add_argument("path")
    p.add_argument("--name", default="")
    p.add_argument("--include", nargs="*", default=["*.md"])
    p.add_argument("--exclude", nargs="*", default=[])
    p.add_argument("--depth", type=int, default=0, help="0 means unlimited")
    p.add_argument("--prior", type=float, default=1.0, help="weight (memories 1.45 · rules 1.25)")
    p.add_argument("--no-embed", action="store_true",
                   help="exclude from semantic search (remote embeddings) — never sent out")
    p.add_argument("--no-index", action="store_true", help="register only; index later")

    p = sub.add_parser("remove", help="Remove a corpus (by name or path)")
    p.add_argument("name_or_path")

    p = sub.add_parser("engines",
                       help="AI engines — which outside model does which job, and what it is sent")
    p.add_argument("--set", nargs="+", default=[], metavar="ROLE=PROVIDER",
                   help="choose an engine, e.g. judge=anthropic embed=openai (or =none)")
    p.add_argument("--model", default="", help="the model for the one role being set")
    p.add_argument("--base-url", default="",
                   help="any OpenAI-compatible server, e.g. http://localhost:11434/v1 (nothing leaves)")
    p.add_argument("--effort", default="", help="judge effort, for providers that take one")

    p = sub.add_parser("stores",
                       help="Storage backends — which database serves vector search and the graph")
    p.add_argument("--set", nargs="+", default=[], metavar="ROLE=BACKEND",
                   help="e.g. vector=qdrant graph=neo4j (or =sqlite, the local copy)")
    p.add_argument("--url", default="", help="the database's address, e.g. http://localhost:6333")
    p.add_argument("--collection-prefix", default="", help="qdrant: collection name prefix (default brain)")
    p.add_argument("--exact", action="store_true",
                   help="qdrant: brute-force search inside Qdrant (the same answers as the local scan)")
    p.add_argument("--database", default="", help="neo4j: database name (default neo4j)")
    p.add_argument("--user", default="", help="neo4j: user (default neo4j); the password comes from NEO4J_PASSWORD")
    p.add_argument("--names", action="store_true", help="neo4j: also send document names (off by default)")
    p.add_argument("--sync", action="store_true", help="push the local copy to the chosen databases")
    p.add_argument("--full", action="store_true", help="with --sync: rebuild the targets from scratch")
    p.add_argument("--check", action="store_true",
                   help="ask the local copy and the database the same questions, and compare")

    p = sub.add_parser("vec", help="Semantic search (remote embeddings) — status, ingest, calibrate, query")
    p.add_argument("action", choices=["status", "build", "calibrate", "search", "warm"])
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--limit-docs", type=int, default=0)
    p.add_argument("-k", type=int, default=5)

    p = sub.add_parser("neighbors", help="Graph neighbours")
    p.add_argument("name")

    p = sub.add_parser("status", help="Status — ★what to fix★, separated from the metrics")
    p.add_argument("--json", action="store_true", help="everything as JSON")
    sub.add_parser("calibrate", help="Re-measure the hook threshold against this corpus")
    p = sub.add_parser("recent",
                       help="Chronological view — 'what was I doing' (the question with no keyword)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("eval-init",
                       help="Draft an evaluation set from your own history (someone else's gold is theirs)")
    p.add_argument("--out", default="", help="output path (default <eval folder>/short.draft.json — `BRAIN_EVAL_DIR`, else next to the repository, else the brain home)")
    p.add_argument("--limit", type=int, default=60)

    sub.add_parser("suggest", help="Self-improvement suggestions")

    sub.add_parser("triggers",
                   help="List and check declared triggers (uniqueness · resolution · over-firing) — score-independent")

    p = sub.add_parser("index-audit",
                       help="Judge whether each index entry stays reachable by recall if deleted")
    p.add_argument("--path", default="", help="index file to check (default MEMORY.md)")
    p.add_argument("--facts", action="store_true",
                   help="second pass — is the entry's fact written in the source memory?")
    p.add_argument("--layers", action="store_true",
                   help="three-layer split — which layer can take each instruction")
    p.add_argument("--conditions", action="store_true",
                   help="split by condition clause — a hook needs a 'when' to take it")

    p = sub.add_parser("dashboard",
                       help="Status + ★performance comparison (brain ↔ grep ↔ agent alone)★ as local HTML")
    p.add_argument("-o", "--out", default="", help="output path")
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--classic", action="store_true", help="render the classic dashboard fallback")

    sub.add_parser("help", help="★Show every command, grouped by purpose★")

    sub.add_parser("privacy",
                   help="Audit the outbound door — which memories hold secret patterns (values never shown)")

    p = sub.add_parser("export",
                      help="★What you earned★ in one file, to move to another machine (calibration excluded)")
    p.add_argument("--out", default="", help="bundle path (default ~/brain-bundle.json)")

    p = sub.add_parser("import",
                      help="Merge another machine's bundle — ⛔ dry-run by default")
    p.add_argument("path", help="bundle file")
    p.add_argument("--apply", action="store_true", help="actually apply it")

    p = sub.add_parser("score",
                      help="★The scorecard★ — 7 axes derived from the goal, as numbers (comparable over time)")
    p.add_argument("--full", action="store_true",
                   help="also measure the axis that costs remote calls (accuracy) — spends judge budget")
    p.add_argument("--note", default="", help="one line to attach to this measurement (what changed before it)")
    p.add_argument("--history", action="store_true", help="show it beside previous measurements")
    p.add_argument("--compare", action="store_true",
                   help="★compare approaches directly★ — raw values with units (accuracy, recall, density, speed)")
    p.add_argument("--axes", action="store_true",
                   help="the normalised 7-axis score (⛔ for the brain's ★own★ comparison over time)")

    p = sub.add_parser("i18n",
                       help="Translation catalogs — check the language · ⛔missing keys / placeholder mismatches")
    p.add_argument("--check", action="store_true",
                   help="⛔ count missing keys, unused keys and placeholder mismatches (uncounted, translations rot)")
    p.add_argument("--lang", default="", help="view once in this language (en·de·es·fr·ja·ko)")

    sub.add_parser("budget",
                   help="The judge's daily budget — how much was spent today (the wall lifts at ★Pacific midnight★)")

    p = sub.add_parser("rules",
                       help="Behaviour rules — list · discover · find unused · approve/disable")
    p.add_argument("--discover", action="store_true",
                   help="ask the judge for new rule candidates (remote call)")
    p.add_argument("--stale", action="store_true",
                   help="rules whose actions ★nobody performed in the period★")
    p.add_argument("--approve", default="", help="promote a suggested signal into a rule")
    p.add_argument("--disable", default="", help="turn a rule id off (not delete)")
    p.add_argument("--sync", action="store_true",
                   help="rewrite the signal list the shell pre-filter reads")
    p.add_argument("--limit", type=int, default=20,
                   help="how many signals to ask the judge about this run (they accumulate)")
    p.add_argument("--days", type=int, default=0,
                   help="look only at the last N days of history (0 = all)")
    p.add_argument("--known", action="store_true",
                   help="judge known rules too (for self-testing)")
    p.add_argument("--auto", action="store_true",
                   help="promote suggestions over the threshold ★without a human★ (the daily job calls this)")
    p.add_argument("--all", action="store_true", help="show every suggestion")
    p.add_argument("--top", type=int, default=15, help="how many suggestions to show (default 15)")

    p = sub.add_parser("feedback", help="Rate a recall result (self-improvement)")
    p.add_argument("name")
    p.add_argument("signal", choices=["useful", "wrong", "stale", "open"])

    # ⛔ ★Bare `brain` prints the help★ — argparse's default only says "error: cmd is required".
    #    The first thing a newcomer does is type `brain` on its own and see what happens.
    import sys as _sys
    if not (argv if argv is not None else _sys.argv[1:]):
        print(_help_text())
        return 0
    args = ap.parse_args(argv)
    db = store.connect()

    if args.cmd == "index":
        _p(store.reindex(db, full=args.full, verbose=args.verbose))
    elif args.cmd == "recall":
        res = search.recall(db, args.query, k=args.k, extra_terms=args.terms,
                            kinds=args.kind, sources=args.source,
                            graph=not args.no_graph)
        if args.brief:
            for r in res:
                flag = "⚠︎" if r["stale"] else " "
                print("%s %-58s %6.2f  %s" % (flag, r["name"][:58], r["score"], r["why"][:40]))
        else:
            _p(res)
    elif args.cmd == "why":
        # ★Gathered on the spot, never stored★ — code, bodies and comments belong to the
        # original (git and disk). What the brain holds is ①the way there (pointers) and
        target = args.path
        print(i18n.t("cli.why.git_header"))
        rows = store.why(target, db, limit=args.n)
        if not rows:
            print(i18n.t("cli.why.no_pointers"))
        for r in rows:
            print("  %s %s@%s  %s" % (r["date"], r["repo"], r["sha"], r["subject"]))
            if r["body"]:
                print("     %s" % r["body"][:420])
        # ⛔ Comments are not indexed — the file is canonical, and reading it then is always current
        # If the file is here, read its comments (for a worktree or another repo, just give the path)
        cand = target if os.path.isfile(target) else ""
        if cand:
            print("")
            print(i18n.t("cli.why.principles_header"))
            shown = 0
            # ⛔ ★Whose markers★ — this repository marks its reasons with ⛔ ⚠️ ★; other code says NOTE: or
            #    WARNING:. Both are read by default, and config `why_markers` replaces the list.
            markers = (store.load_config(tolerant=True).get("why_markers")
                       or ["⛔", "⚠️", "★", "NOTE:", "WARNING:", "IMPORTANT:", "WHY:"])
            try:
                with open(cand, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if shown >= 12:
                            break
                        if any(m in line for m in markers):
                            print("  %5d  %s" % (i, line.strip()[:150]))
                            shown += 1
            except OSError:
                pass
            if not shown:
                print(i18n.t("cli.why.no_marked_comments"))
        print("")
        print(i18n.t("cli.why.knows_header"))
        base = os.path.basename(target)
        hits = [r for r in search.recall(db, base, k=4, log=False)
                if not r.get("related")]
        if not hits:
            print(i18n.t("cli.why.nothing"))
        for r in hits[:3]:
            print("  %-52s %5.1f  %s" % (r["name"][:52], r["score"],
                                         (r["description"] or "")[:60]))
    elif args.cmd == "detect":
        from brain import discover
        cfg = store.load_config(tolerant=True)
        cands = discover.detect(min_files=args.min_files)
        configured = discover.already_configured(cfg)
        print(discover.render(cands, configured))
        fresh = [c for c in cands if not discover.covered_by(c.path, configured)]
        print("")
        print(i18n.t("cli.detect.summary", n=len(cands),
                      reg=len(cands) - len(fresh), add=len(fresh)))
        if not args.add:
            print(i18n.t("cli.detect.nothing_changed"))
            print(i18n.t("cli.detect.attach_hint"))
        else:
            added = [store.add_source(c.as_source()) for c in fresh]
            for a in added:
                print("  %s %s" % ({"added": "＋", "exists": "=", "missing": "✗"}
                                   .get(a["action"], "?"), a.get("path")))
            if any(a["action"] == "added" for a in added):
                _p(store.reindex(db))
    elif args.cmd == "sources":
        from brain import discover
        cfg = store.load_config(tolerant=True)
        rows = []
        for s_ in cfg.get("sources", []):
            path = os.path.expanduser(s_.get("path", ""))
            live = discover._count(path, s_.get("include") or ["*.md"],
                                   s_.get("max_depth") or 0) if os.path.isdir(path) else None
            indexed = db.execute("SELECT COUNT(*) c FROM docs WHERE source=?",
                                 (s_.get("name"),)).fetchone()["c"]
            rows.append({"name": s_.get("name"), "path": s_.get("path"),
                         "exists": os.path.isdir(path),
                         "files": int(live["files"]) if live else 0,
                         "indexed_docs": indexed,
                         "prior": s_.get("prior", 1.0),
                         "embed": s_.get("embed", True)})
        _p(rows)
        bad = [r for r in rows if not r["exists"]]
        if bad:
            print(i18n.t("cli.sources.bad_paths", n=len(bad)))
            for r in bad:
                print("   %s → %s" % (r["name"], r["path"]))
    elif args.cmd == "add":
        src = {"name": args.name or os.path.basename(os.path.abspath(
                   os.path.expanduser(args.path))) or "added",
               "path": args.path.replace(os.path.expanduser("~"), "~", 1),
               "include": args.include, "prior": args.prior,
               "note": "a corpus attached by hand with `brain add`"}
        if args.exclude:
            src["exclude"] = args.exclude
        if args.depth:
            src["max_depth"] = args.depth
        if args.no_embed:
            src["embed"] = False          # ★never sent out★ (excluded from semantic search)
        res = store.add_source(src)
        _p(res)
        if res["action"] == "missing":
            print(i18n.t("cli.add.missing_path"))
            return 2
        if res["action"] == "added" and not args.no_index:
            from brain import discover
            live = discover._count(os.path.expanduser(args.path), src["include"],
                                   src.get("max_depth", 0))
            print(i18n.t("cli.add.will_index", files=live["files"], mb="%.1f" % (live["bytes"]/1e6)))
            _p(store.reindex(db))
    elif args.cmd == "remove":
        _p(store.remove_source(args.name_or_path))
        print(i18n.t("cli.remove.cleanup_hint"))
    elif args.cmd == "engines":
        from brain import engines as _en
        if args.set:
            try:
                want = _en.parse_assignments(args.set)
            except ValueError as exc:
                print(str(exc))
                return 2
            if (args.model or args.base_url or args.effort) and len(want) != 1:
                print(i18n.t("cli.engines.one_role_for_model"))
                return 2
            for role, prov in want.items():
                try:
                    e = _en.set_choice(role, prov, args.model, args.base_url, args.effort)
                except ValueError as exc:
                    print(str(exc))
                    return 2
                print(i18n.t("cli.engines.set", role=role, entry=json.dumps(e)))
            if "judge" in want:
                print(i18n.t("cli.engines.recalibrate_hint"))
            if "embed" in want:
                print(i18n.t("cli.engines.rebuild_hint"))
            print()
        print(engines_text(db))
    elif args.cmd == "stores":
        from brain import stores as _st
        if args.set:
            try:
                want = _st.parse_assignments(args.set)
            except ValueError as exc:
                print(str(exc))
                return 2
            has_opts = (args.url or args.collection_prefix or args.exact or args.database or args.user
                        or args.names)
            if has_opts and len(want) != 1:
                print(i18n.t("cli.stores.one_role_for_options"))
                return 2
            for role, backend in want.items():
                opts = ({"collection_prefix": args.collection_prefix, "exact": True if args.exact else None}
                        if role == "vector" else
                        {"database": args.database, "user": args.user, "names": True if args.names else None})
                try:
                    entry = _st.set_choice(role, backend, args.url, opts)
                except ValueError as exc:
                    print(str(exc))
                    return 2
                print(i18n.t("cli.stores.set", role=role, entry=json.dumps(entry)))
            args.sync = True                     # a new choice serves only once it holds the local copy
            print()
        if args.sync:
            print(stores_sync_text(_st.sync(db, full=args.full, progress=True)))
        if args.check:
            print(stores_check_text(_st.check(db)))
        print(stores_text(db))
    elif args.cmd == "vec":
        from brain import vectors
        if args.action == "status":
            st = vectors.coverage(db)
            st.update({"provider": vectors.PROVIDER, "space": vectors.model_tag(),
                       "dim": vectors.DIM, "gate_min_ratio": vectors.min_ratio(db),
                       "ref_min_cos": vectors.min_cos(db)})
            _p(st)
        elif args.action == "warm":
            _p(vectors.warm_control_cache(db))
        elif args.action == "build":
            _p(vectors.build(db, limit_docs=args.limit_docs, progress=True))
        elif args.action == "calibrate":
            # ⛔ Controls and answers come from ★your own evaluation set★ (never invented)
            import os as _os
            from brain import evalinit as _ei, rerank as _rr
            path = _os.path.join(_ei.eval_dir(), "short.json")
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            control = [c["q"] if isinstance(c, dict) else c
                       for c in d.get("C_no_memory_needed", [])]
            pos = [(c["q"], c["gold"]) for c in d.get("A_memory_needed", [])
                   if c.get("gold")]
            res = vectors.calibrate_cos(db, control, pos)
            res.pop("detail", None)
            # ★the judge too★ — its threshold holds only for the judge and question it was measured
            #   with (§rerank.scale_id), so choosing another engine means measuring here again.
            if _rr.available():
                from brain import engines as _en
                res["judge"] = dict(_rr.calibrate(db, control, pos), engine=_en.judge_id())
            _p(res)
        else:
            rows = search.semantic(db, args.query, k=args.k)
            for r in rows:
                print(i18n.t("cli.vec.row", name="%-52s" % r["name"][:52],
                              ratio="%.3f" % r.get("ratio", 0), cosine="%.3f" % r.get("cosine", 0),
                              desc=(r["description"] or "")[:40]))
            if not rows:
                print(i18n.t("cli.vec.nothing_above_gate", thr="%.3f" % vectors.min_ratio(db)))
    elif args.cmd == "neighbors":
        _p(search.neighbors(db, args.name))
    elif args.cmd == "calibrate":
        _p(calib.calibrate(db))
    elif args.cmd == "status":
        st = health.status(db)
        if args.json:
            _p(st)
            return 0
        # ⛔ ★Report issues and metrics separately★ — the old output printed five numbers with no
        #    distinction, and whoever read it (on 2026-08-31, that was me) read "459 things to fix".
        #    The judgement lives in one place, `health.CHECK_KIND`; here we only read it.
        checks = st["distortion_checks"]
        issues = {k: v for k, v in checks.items() if v.get("kind") == "issue"}
        metrics = {k: v for k, v in checks.items() if v.get("kind") != "issue"}
        c = st["corpus"]
        print(i18n.t("cli.status.corpus", docs=format(c["docs"], ","),
                      memories=format(st["by_source"].get("memory", 0), ","),
                      links=format(c["links"], ","), last_index=c["last_index"] or "-"))

        # ⛔ ★Is the guard working on this machine★ — if not, say so ★at the very top★.
        #    (Running with no evaluation sample is the default state on someone else's machine — silence is wrong)
        g = st.get("calibration_guard") or {}
        if g and not g.get("protected", True):
            print("\n%s" % g["why"])
            lu = g.get("last_unguarded") or {}
            if lu.get("deployed"):
                print("   ↳ %s" % i18n.t("guard.off.deployed", before=lu.get("from"),
                                          after=lu.get("deployed"),
                                          at=(lu.get("at") or "")[:16]))

        sch = st.get("schedules") or {}
        if sch.get("jobs"):
            bad = [j for j in sch["jobs"] if not j["ok"]]
            print("\n%s — %s"
                  % (i18n.t("cli.status.jobs_title"),
                     i18n.t("cli.status.jobs_all_ok") if not bad
                     else i18n.t("cli.status.jobs_stopped", n=len(bad))))
            for j in sch["jobs"]:
                # ⛔ ★Green means the scheduler ran it★, not that the log is fresh —
                #    so ★who★ is printed next to 'when' (scheduled / a person / never).
                cal = j.get("cal_days")
                age = (i18n.t("cli.status.job_days_ago", days="%.1f" % cal) if cal is not None
                       else i18n.t("cli.status.job_no_record"))
                by = {"scheduled": i18n.t("cli.status.job_by.scheduled"),
                      "unknown": i18n.t("cli.status.job_by.unknown"),
                      "wired-only": i18n.t("cli.status.job_by.wired_only"),
                      "never": i18n.t("cli.status.job_by.never")}.get(j.get("by", ""), "")
                print("  %s %-20s %-10s %-16s %s"
                      % ("✅" if j["ok"] else "❌", j["job"], age, by, j["what"]))
                if j.get("why"):
                    print("     %s" % j["why"])

        for role, sr in (st.get("stores") or {}).items():
            if not sr.get("ok"):
                print("\n" + i18n.t("cli.status.store_fallback", role=role, backend=sr["backend"],
                                     error=(sr.get("error") or "")[:120]))
            elif not sr.get("in_sync"):
                print("\n" + i18n.t("cli.status.store_unsynced", role=role, backend=sr["backend"]))

        print("\n%s" % i18n.t("cli.status.to_fix"))
        if not issues:
            print("  %s" % i18n.t("cli.status.nothing"))
        for k, v in sorted(issues.items(), key=lambda kv: -kv[1].get("count", 0)):
            print("  · %-22s %5s — %s" % (k, v.get("count"), (v.get("meaning") or "")[:52]))
            if v.get("action"):
                print("      → %s" % v["action"])
            sp = v.get("split")
            if sp:
                print("      %s" % i18n.t("cli.status.fixable",
                                           n=len(sp.get("typo") or []),
                                           m=len(sp.get("missing") or [])))
                for x in (sp.get("typo") or [])[:3]:
                    print("        %s" % i18n.t("cli.status.near_hint",
                                                  to=x["to"], near=", ".join(x["near"])[:56]))

        print("\n%s" % i18n.t("cli.status.metrics_title"))
        for k, v in metrics.items():
            print("  · %-22s %5s — %s" % (k, v.get("count"), (v.get("meaning") or "")[:52]))
            if v.get("why_metric"):
                for line in _wrap(v["why_metric"], 92):
                    print("      %s" % line)
        print("\n%s" % i18n.t("cli.status.json_footer"))
    elif args.cmd == "dashboard":
        from brain import dashboard
        path = dashboard.write(db, args.out, open_browser=not args.no_open, classic=args.classic)
        print(path)
    elif args.cmd == "eval-init":
        from brain import evalinit
        print(evalinit.render(evalinit.write(db, args.out, args.limit)))
    elif args.cmd == "recent":
        from brain import timeline
        print(timeline.render(timeline.recent(db, args.days, args.limit)))
    elif args.cmd == "triggers":
        from brain import triggers
        print(triggers.render(triggers.audit(db)))
    elif args.cmd == "index-audit":
        from brain import indexaudit
        # ⛔ Never name a local variable _p — it shadows the module function _p() and
        #    ★kills the index subcommand you never touched★ (2026-08-18, for real).
        target = args.path or indexaudit.MEMORY_INDEX()
        if args.conditions:
            print(indexaudit.render_conditions(
                indexaudit.classify_by_condition(target)))
        elif args.layers:
            print(indexaudit.render_layers(indexaudit.classify_directives(target)))
        elif args.facts:
            print(indexaudit.render_facts(indexaudit.fact_check(target)))
        else:
            print(indexaudit.render(indexaudit.audit(target)))
    elif args.cmd == "suggest":
        _p(health.suggestions(db))
    elif args.cmd == "privacy":
        from . import privacy as _priv, vectors as _vec
        rep = _priv.audit()
        print(i18n.t("cli.privacy.summary", n=rep["files"], flagged=len(rep["flagged"])))
        print(i18n.t("cli.privacy.no_values"))
        for row in rep["flagged"]:
            print("  · %-52s %s%s" % (row["name"][:52], ", ".join(row["patterns"]),
                                      i18n.t("cli.privacy.embed_false_tag") if row["local_only"] else ""))
        print(i18n.t("cli.privacy.doors_header"))
        print(i18n.t("cli.privacy.doors_list"))
        print(i18n.t("cli.privacy.blocked_per_source",
                      sources=_vec.no_embed_sources() or i18n.t("cli.privacy.none_goes_out")))
        print(i18n.t("cli.privacy.cannot_recall"))
    elif args.cmd == "export":
        from . import portable as _pt
        r = _pt.export(db, args.out)
        print(i18n.t("cli.export.bundle", path=r["path"], mb="%.1f" % (r["bytes"] / 1e6)))
        print(i18n.t("cli.export.contents", chunks=r["vectors"], docs=r["docs_with_vectors"],
                      lex=r["lexicon"], rj=r["rule_judged"], rc=r["rerank_cache"]))
        print(i18n.t("cli.export.usage", usage=r["usage"], qc=r["vec_qcache"]))
        print(i18n.t("cli.export.not_included", note=r["meta_note"]))
        print(i18n.t("cli.export.no_memory_files"))
    elif args.cmd == "import":
        from . import portable as _pt
        r = _pt.imp(db, args.path, dry_run=not args.apply)
        if r.get("error"):
            print(i18n.t("cli.import.error", error=r["error"]))
            return 1
        print(i18n.t("cli.import.bundle_header", path=args.path, at=r.get("made_at"),
                      dry="" if args.apply else i18n.t("cli.import.dry_run_tag")))
        print(i18n.t("cli.import.vectors_summary", arrived=r["vectors_in"], no_doc=r["vectors_no_doc"],
                      stale=r["vectors_stale_sha"], have=r["vectors_have"]))
        if r["vectors_no_doc"]:
            print(i18n.t("cli.import.index_hint"))
        if r["vectors_stale_sha"]:
            print(i18n.t("cli.import.stale_hint"))
        print(i18n.t("cli.import.tables_summary", tables=r["tables"], usage=r["usage"],
                      meta=r["meta"], note=r["meta_note"]))
        if not args.apply:
            print(i18n.t("cli.import.apply_hint"))
    elif args.cmd == "help":
        print(_help_text())
        return 0
    elif args.cmd == "score":
        from . import scorecard as _sk
        if args.history:
            rows = _sk.history()
            if not rows:
                print(i18n.t("cli.score.no_records"))
            print("%-20s %6s  %s" % (i18n.t("cli.score.history_col_time"),
                                      i18n.t("cli.score.history_col_total"),
                                      i18n.t("cli.score.history_col_axis")))
            prev_v = None
            for r in rows:
                # ⛔ ★Mark where the ruler itself changed★ (2026-09-01) — without this, ★a change
                #    in definition reads as a change in performance★. On 09-01 the automatic axis
                #    went from 'hits only' to 'hits × quiet', and the total moved 74.1 → 73.6 on
                #    that row — the brain did not get worse, ★the ruler got honest★.
                v = r.get("axis_version") or 1
                if prev_v is not None and v != prev_v:
                    print("%-20s %6s  %s" % ("─" * 20, "─" * 6,
                          i18n.t("cli.score.version_marker", prev=prev_v, new=v)))
                prev_v = v
                short = {"recall": "reach", "precision": "prec", "efficiency": "effic",
                         "automatic": "auto", "stability": "stable", "autonomy": "autonm",
                         "liveness": "live"}
                ax = " ".join("%s%s" % (short.get(k, k[:2]),
                                        ("—" if v2 is None else "%.0f" % v2))
                              for k, v2 in (r.get("axes") or {}).items())
                pl = {"deployed": "deployed", "word": "lexical"}.get(r.get("pipeline") or "", "?")
                print("%-20s %6s  %s v%s[%s] %s" % (r["at"][:19],
                                                    "—" if r["total"] is None else "%.1f" % r["total"],
                                                    ax, v, pl, r.get("note", "")))
            print(i18n.t("cli.score.version_note"))
            print(i18n.t("cli.score.pipeline_note"))
            return 0
        raw = _sk.collect(db, cheap=not args.full)
        sc = _sk.compute(raw)
        if args.compare:
            # ⛔ ★Approaches are compared only on raw values★ — the composite does not add up
            #    across different measurement scopes (user's point, 2026-08-31). The agent-alone
            #    "stability 100" is true because it does nothing; the brain's same axis is unmeasured.
            print(_sk.render_bench(_sk.bench(db), i18n.t("cli.score.compare_title")))
        elif args.axes:
            print(_sk.render(sc, i18n.t("cli.score.axes_title")))
        else:
            print(_sk.render_bench(_sk.bench(db), i18n.t("cli.score.compare_title")))
            print()
            print(_sk.render(sc, i18n.t("cli.score.default_title")))
        path = _sk.save(sc, args.note)
        print(i18n.t("cli.score.recorded_at", path=path))
    elif args.cmd == "i18n":
        if args.lang:
            i18n._lang = args.lang
        print(i18n.t("i18n.check.title"))
        print("  " + i18n.t("i18n.check.lang_now", code=i18n.lang()))
        a = i18n.audit()
        en_total = len(i18n.catalog("en"))
        print("  " + i18n.t("i18n.check.used", n=a["used"]))
        bad = 0
        if a["en_missing"]:
            bad += len(a["en_missing"])
            print("  ⛔ " + i18n.t("i18n.check.en_missing", n=len(a["en_missing"])))
            for k in a["en_missing"][:12]:
                print("       %s" % k)
        if a["en_unused"]:
            print("  ⚠️  " + i18n.t("i18n.check.en_unused", n=len(a["en_unused"])))
        print()
        for code, row in a["langs"].items():
            print("  " + i18n.t("i18n.check.lang_row", code=code, have=row["have"],
                                 total=en_total, missing=len(row["missing"]),
                                 extra=len(row["extra"]),
                                 bad=len(row["placeholder_mismatch"])))
            # ⛔ A placeholder mismatch means ★the translation is there but never shows★ — say it loudly
            for k in row["placeholder_mismatch"][:8]:
                bad += 1
                print("       ⛔ %s  (placeholders differ from English — silently falls back to English)" % k)
            for k in row["extra"][:5]:
                print("       ⚠️  %s  (key not in English — a typo, or a key that was removed)" % k)
        print()
        print("  " + (i18n.t("i18n.check.fail", n=bad) if bad else i18n.t("i18n.check.ok")))
        return 1 if (bad and args.check) else 0

    elif args.cmd == "budget":
        from . import rerank as _rr
        b = _rr.budget(db)
        print(i18n.t("cli.budget.header", day=b["day"]))
        if b["left"] is None and not b["wall"]:
            # ⛔ no daily wall we know of for this judge (§engines.JUDGE_RPD) — no bar out of a made-up limit
            from . import engines as _en
            print(i18n.t("cli.budget.no_limit", used=b["used"], judge=_en.judge_id()))
            return 0
        bar = int(20 * min(1.0, b["used"] / max(1, b["limit"])))
        print(i18n.t("cli.budget.bar", filled="█" * bar, empty="·" * (20 - bar),
                      used=b["used"], limit=b["limit"], left=b["left"]))
        if b["wall"]:
            print(i18n.t("cli.budget.wall"))
            print(i18n.t("cli.budget.wall_lie"))
        elif b["left"] <= _rr.RESERVE:
            print(i18n.t("cli.budget.reserve_only", reserve=_rr.RESERVE))
        try:
            when, secs = _rr.next_reset()
            print(i18n.t("cli.budget.next_reset", when=when.strftime("%m-%d %H:%M"),
                          hours="%.1f" % (secs / 3600.0)))
            due = _rr.scheduled_spend()
            if due:
                booked = sum(d["cost"] for d in due)
                print(i18n.t("cli.budget.booked", n=booked,
                              detail=" · ".join("%s %s(~%d)"
                                                % (d["job"], d["at"].strftime("%m-%d %H:%M"), d["cost"])
                                                for d in due)))
                print(i18n.t("cli.budget.share", left=b["left"],
                              share=max(0, b["left"] - booked - _rr.RESERVE), reserve=_rr.RESERVE))
        except Exception:                                # noqa: BLE001
            pass
        cs = _rr.cache_stats(db)
        print(i18n.t("cli.budget.rate_limits", rpm=_rr.RPM, limit=b["limit"], reserve=_rr.RESERVE))
        print(i18n.t("cli.budget.cache_stats", rows=cs["rows"], hits=cs["hits"]))
        print(i18n.t("cli.budget.cache_key_why"))
        print(i18n.t("cli.budget.used_caveat"))
        if _rr.last_failure():
            print(i18n.t("cli.budget.last_failure", msg=_rr.last_failure()))
        from . import calibrate as _cal, lexicon as _lx, store as _st
        print(i18n.t("cli.budget.thresholds_header"))
        _ung = _cal.unguarded(db) if hasattr(_cal, "unguarded") else {}
        _gst = _cal.guard_status(db) if hasattr(_cal, "guard_status") else {}
        if not _gst.get("protected", True):
            print("  %s" % _gst["why"])
            if _ung.get("deployed"):
                print("     ↳ %s" % i18n.t("guard.off.deployed", before=_ung.get("from"),
                                            after=_ung.get("deployed"),
                                            at=(_ung.get("at") or "")[:16]))
        _blk = _cal.blocked(db) if hasattr(_cal, "blocked") else {}
        if _blk.get("why"):
            print(i18n.t("cli.budget.calib_refused", at=_blk.get("at", "?")[:16], why=_blk["why"]))
            print(i18n.t("cli.budget.calib_refused_hint", kept=_blk.get("kept")))
        print(i18n.t("cli.budget.lexical_threshold",
                      value=_st.get_meta(db, "hook_threshold", i18n.t("cli.budget.not_yet"))))
        blocked = _st.get_meta(db, "rerank_calibration_blocked", "")
        print(i18n.t("cli.budget.judge_threshold",
                      value=_st.get_meta(db, "rerank_min_score", i18n.t("cli.budget.not_yet")),
                      detail=blocked or _st.get_meta(db, "rerank_thr_why",
                                                      i18n.t("cli.budget.never_calibrated"))))
        if blocked:
            print(i18n.t("cli.budget.calib_not_deployed"))
        _lst = _lx.decision_stale(db) if hasattr(_lx, "decision_stale") else {}
        if _lst.get("stale") is True:
            print(i18n.t("cli.budget.stale_true", why=_lst["why"]))
            print(i18n.t("cli.budget.stale_hint"))
        elif _lst.get("stale") is None and _lst.get("why"):
            print(i18n.t("cli.budget.stale_unknown", why=_lst["why"]))
        print(i18n.t("cli.budget.lexicon_bridge",
                      state=i18n.t("cli.budget.state_on") if _lx.enabled(db) else i18n.t("cli.budget.state_off"),
                      knobs=_st.get_meta(db, "lexicon_knobs", "") or i18n.t("cli.budget.none_chosen"),
                      at=_st.get_meta(db, "lexicon_decided_at", i18n.t("cli.budget.none_value"))))
        ev = _st.get_meta(db, "lexicon_evidence", "")
        if ev:
            print(i18n.t("cli.budget.evidence", text=ev[:150]))
    elif args.cmd == "rules":
        from . import ruledisc
        if args.sync:
            _p(ruledisc.sync_shell())
        elif args.approve:
            d = ruledisc.approve(args.approve)
            ruledisc.sync_shell()
            _p({"approved": args.approve, "learned rules": len(d["learned"])})
        elif args.disable:
            d = ruledisc.disable(args.disable)
            ruledisc.sync_shell()
            _p({"disabled": args.disable, "active rules": len(ruledisc.all_rules())})
        elif args.auto:
            res = ruledisc.auto_approve()
            print(i18n.t("cli.rules.auto.header",
                         min_score="%.0f" % res["min_score"], cap="%.0f" % res["cap"]))
            for a in res["added"]:
                print("  " + i18n.t("cli.rules.auto.turned_on", signal="%-26s" % a["signal"],
                                    top="%.0f" % a["top"], uses=a["uses"]))
            if not res["added"]:
                print("  " + i18n.t("cli.rules.auto.none"))
            for sig, why in res["skipped"]:
                print("  " + i18n.t("cli.rules.auto.skipped_line", signal="%-24s" % sig, reason=why))
        elif args.stale:
            r = ruledisc.stale(since_days=args.days)
            sc = r["scope"]
            print(i18n.t("cli.rules.scope", files=sc["files"], calls=sc["calls"],
                         start=sc["from"], end=sc["to"]))
            print(i18n.t("cli.rules.stale.zero_note"))
            for x in r["rules"]:
                cold = i18n.t("cli.rules.stale.all_cold") if x["all_cold"] else i18n.t("cli.rules.stale.some_cold")
                print("  %s %-20s %s" % (cold, x["id"], ", ".join(x["cold"])))
            if not r["rules"]:
                print("  " + i18n.t("cli.rules.stale.none"))
        elif args.discover:
            r = ruledisc.discover(limit=args.limit, db=db,
                                  include_known=args.known, since_days=args.days)
            sc = r["scope"]
            print(i18n.t("cli.rules.scope", files=sc["files"], calls=sc["calls"],
                         start=sc["from"], end=sc["to"]))
            if r.get("local_only"):
                print(i18n.t("cli.rules.discover.local_only"))
            print(i18n.t("cli.rules.discover.summary", candidates=r["candidates"], judged=r["judged"],
                         failed=r["failed"], skipped=r["skipped"]))
            bg = r.get("budget") or {}
            if bg:
                print(i18n.t("cli.rules.discover.budget", used=bg.get("used", 0), limit=bg.get("limit", 0),
                             left=bg.get("left", 0),
                             wall=i18n.t("cli.rules.discover.wall_suffix") if bg.get("wall") else ""))
            if r.get("stopped_early"):
                # ⛔ ★No hedging with "it is likely"★ — we know the reason now.
                print(i18n.t("cli.rules.discover.stopped",
                             reason=r.get("stop_reason") or i18n.t("cli.rules.discover.stop_reason_unknown")))
                print("   " + i18n.t("cli.rules.discover.retry_tomorrow"))
            if r["skipped"]:
                print("   " + i18n.t("cli.rules.discover.retry_now"))
            if r.get("absorbed"):
                print("   " + i18n.t("cli.rules.discover.absorbed", n=r["absorbed"]))
            shown = r["proposals"] if args.all else r["proposals"][:args.top]
            for pr in shown:
                warn = ""
                if pr.get("broader_than"):
                    warn = "  " + i18n.t("cli.rules.discover.warn_contains",
                                        names=", ".join(pr["broader_than"][:3]))
                elif pr.get("rate", 0) >= 5.0:
                    warn = "  " + i18n.t("cli.rules.discover.warn_broad", rate="%.0f" % pr["rate"])
                prefix = i18n.t("cli.rules.discover.already_rule") if pr["known"] else ""
                stats = i18n.t("cli.rules.discover.row_stats", uses=pr["uses"],
                               rate="%.1f" % pr.get("rate", 0.0), top="%.0f" % pr["top"])
                print("\n  %s%-24s  %s%s" % (prefix, pr["signal"], stats, warn))
                for m, sc2 in zip(pr["memories"], pr["scores"]):
                    print("      %4.0f  %s" % (sc2, m))
            if not r["proposals"]:
                print("  " + i18n.t("cli.rules.discover.none", min_score="%.0f" % ruledisc.MIN_SCORE))
            elif len(shown) < len(r["proposals"]):
                print("\n  " + i18n.t("cli.rules.discover.more", shown=len(shown), total=len(r["proposals"])))
            print("\n" + i18n.t("cli.rules.discover.approve_hint"))
        else:
            from . import guard as _guard
            _d = ruledisc.load()
            _autos = {r["id"] for r in _d["learned"]
                      if "auto-approved" in (r.get("why") or "")}
            _code = {r["id"] for r in _guard.RULES}
            for x in ruledisc.all_rules():
                # ⛔ "built-in" only for what the code ships (nothing, §guard.RULES) — a rule a person
                #    wrote into their own rules.json is theirs, not the program's.
                src = (i18n.t("cli.rules.src.builtin") if x["id"] in _code
                       else i18n.t("cli.rules.src.auto") if x["id"] in _autos
                       else i18n.t("cli.rules.src.learned") if x["id"].startswith("auto-")
                       else i18n.t("cli.rules.src.manual"))
                print("  " + i18n.t("cli.rules.list.row", src=src, id="%-20s" % x["id"],
                                    match=", ".join(x["match"])))
            print("\n" + i18n.t("cli.rules.list.signal_file", path=ruledisc.signals_file()))
    elif args.cmd == "feedback":
        _p(health.feedback(db, args.name, args.signal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
