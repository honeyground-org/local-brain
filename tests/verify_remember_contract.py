#!/usr/bin/env python3
"""remember contract — ★a retry never duplicates, never overwrites what it did not read★ (orbit-1 O3, 2026-10-29).

## Why this check exists

A client that keeps an outbox (Cosmos Orbit) retries a save after a timeout. If the second attempt
created a second memory, or quietly replaced a memory a person wrote in between, the outbox would
make the brain worse with every hiccup. The contract's six rows (§remembercontract.decide) are each
exercised here against the disk, and default calls use the same protection.

## What is held down

  ① defaults: JSON, generated retry key, and no unversioned overwrite
  ② new: a keyed save is `new`, `stored`, `indexed_words`, with a 40-hex `sha` equal to the file's, the
     contract fields in the frontmatter, and `verified_at` = `observed_at`
  ③ noop: the same key and payload again changes nothing on disk and answers the same (minus timing)
  ④ sha_mismatch: same key, other body, no proof of the version read → refused, file untouched
  ⑤ updated: the same with `expected_sha` = the current sha → written, new sha, still indexed
  ⑥ name_taken: another key, or a memory written by a person (no key) → refused without proof; with
     `expected_sha` → updated (control: the refused call must have left the file byte-identical)
  ⑦ partial success: the index fails after the file is written → `stored: true, indexed_words: false`
     with the error named; the retry is a `noop` whose `indexed_words` is true
  ⑧ arguments: a bad `expected_sha`, `observed_at`, a 129-char key, an empty body → `invalid_argument`
     (control: the valid calls above passed)
  ⑨ the save path indexes the words and re-measures nothing — no threshold, no vector gate
     (control: a default index pass does re-measure)

Seeds its own home; runs in a clean room.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="brain-remember-contract-")
_MEM = os.path.join(_TMP, "memory")
os.environ["BRAIN_HOME"] = os.path.join(_TMP, "home")
os.environ["BRAIN_CONFIG"] = os.path.join(_TMP, "config.json")
os.environ.pop("BRAIN_LEXICON", None)

from brain import remembercontract as rc, server, store  # noqa: E402

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + str(detail)[:220]) if detail else ""))
    if not ok:
        FAILS.append(label)


def _build():
    os.makedirs(_MEM)
    with open(os.environ["BRAIN_CONFIG"], "w", encoding="utf-8") as fh:
        json.dump({"sources": [{"name": "memory", "path": _MEM, "include": ["*.md"], "max_depth": 1}]}, fh)
    db = store.connect()
    store.reindex(db, full=True, recalibrate=False, refresh_history=False)
    return db


def _rem(db, **args):
    return server.call_tool(db, "remember", args, _measure=True)


def _j(db, **args):
    return json.loads(_rem(db, **args))


def _read(name):
    with open(os.path.join(_MEM, name + ".md"), encoding="utf-8") as fh:
        return fh.read()


def _strip(d):
    return {k: v for k, v in d.items() if k not in ("meta", "index")}


def revision_checks(db):
    """Controlled everyday changes: the same query, explicit revision vs unrelated saves."""
    from brain import capabilities
    langs = [
        ("en", "Orchid watering on Monday", "Orchid watering on Friday"),
        ("ko", "\ub09c\ucd08 \ubb3c\uc8fc\uae30\ub294 \uc6d4\uc694\uc77c", "\ub09c\ucd08 \ubb3c\uc8fc\uae30\ub294 \uae08\uc694\uc77c"),
        ("ja", "\u862d\u306e\u6c34\u3084\u308a\u306f\u6708\u66dc\u65e5", "\u862d\u306e\u6c34\u3084\u308a\u306f\u91d1\u66dc\u65e5"),
        ("ar", "\u0633\u0642\u064a \u0627\u0644\u0623\u0648\u0631\u0643\u064a\u062f \u064a\u0648\u0645 \u0627\u0644\u0627\u062b\u0646\u064a\u0646", "\u0633\u0642\u064a \u0627\u0644\u0623\u0648\u0631\u0643\u064a\u062f \u064a\u0648\u0645 \u0627\u0644\u062c\u0645\u0639\u0629"),
        ("ru", "\u041f\u043e\u043b\u0438\u0432 \u043e\u0440\u0445\u0438\u0434\u0435\u0438 \u0432 \u043f\u043e\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u0438\u043a", "\u041f\u043e\u043b\u0438\u0432 \u043e\u0440\u0445\u0438\u0434\u0435\u0438 \u0432 \u043f\u044f\u0442\u043d\u0438\u0446\u0443"),
        ("hi", "\u0911\u0930\u094d\u0915\u093f\u0921 \u0915\u094b \u0938\u094b\u092e\u0935\u093e\u0930 \u092a\u093e\u0928\u0940", "\u0911\u0930\u094d\u0915\u093f\u0921 \u0915\u094b \u0936\u0941\u0915\u094d\u0930\u0935\u093e\u0930 \u092a\u093e\u0928\u0940"),
        ("th", "\u0e23\u0e14\u0e19\u0e49\u0e33\u0e01\u0e25\u0e49\u0e27\u0e22\u0e44\u0e21\u0e49\u0e27\u0e31\u0e19\u0e08\u0e31\u0e19\u0e17\u0e23\u0e4c", "\u0e23\u0e14\u0e19\u0e49\u0e33\u0e01\u0e25\u0e49\u0e27\u0e22\u0e44\u0e21\u0e49\u0e27\u0e31\u0e19\u0e28\u0e38\u0e01\u0e23\u0e4c"),
        ("es", "Regar la orqu\xeddea el lunes", "Regar la orqu\xeddea el viernes"),
        ("el", "\u03a0\u03cc\u03c4\u03b9\u03c3\u03bc\u03b1 \u03bf\u03c1\u03c7\u03b9\u03b4\u03ad\u03b1\u03c2 \u03c4\u03b7 \u0394\u03b5\u03c5\u03c4\u03ad\u03c1\u03b1", "\u03a0\u03cc\u03c4\u03b9\u03c3\u03bc\u03b1 \u03bf\u03c1\u03c7\u03b9\u03b4\u03ad\u03b1\u03c2 \u03c4\u03b7\u03bd \u03a0\u03b1\u03c1\u03b1\u03c3\u03ba\u03b5\u03c5\u03ae"),
        ("he", "\u05d4\u05e9\u05e7\u05d9\u05d9\u05ea \u05e1\u05d7\u05dc\u05d1 \u05d1\u05d9\u05d5\u05dd \u05e9\u05e0\u05d9", "\u05d4\u05e9\u05e7\u05d9\u05d9\u05ea \u05e1\u05d7\u05dc\u05d1 \u05d1\u05d9\u05d5\u05dd \u05e9\u05d9\u05e9\u05d9"),
    ]
    def prep(**args):
        return json.loads(server.call_tool(db, "prepare_revision", args, _measure=True))
    def names(query, scope, mode="current", at="2026-11-12"):
        result = json.loads(server.call_tool(db, "recall", dict(query=query, scope=scope,
                            view=mode, as_of=at, semantic_k=0, k=8), _measure=True))
        return {r["name"] for r in result["results"]}
    wins = 0
    for lang, old, new in langs:
        base_name, old_name, new_name = lang+"_baseline", lang+"_prior", lang+"_changed"
        base_scope, scope = lang+"-control", lang+"-revision"
        _j(db, name=base_name, description=old, type="user", body=old, project_id=base_scope)
        _j(db, name=base_name+"_new", description=new, type="user", body=new, project_id=base_scope)
        query = old + " " + new
        baseline = names(query, base_scope)
        check(lang+" control: independent saves expose both arrangements",
              {base_name, base_name+"_new"} <= baseline)
        saved = _j(db, name=old_name, description=old, type="user", body=old, project_id=scope)
        original = _read(old_name)
        request = dict(predecessor=old_name, name=new_name, description=new, type="user", body=new,
                       project_id=scope, topic=old, facet="watering-day", status="active", valid_from="2026-11-01",
                       change_reason="User changed the watering arrangement", evidence=["Explicit user correction"],
                       replace_whole_record=True)
        denied = prep(**dict(request, replace_whole_record=False))
        check(lang+" control: no implicit whole-note retirement", denied.get("ok") is False)
        changes_before = db.total_changes
        preview = prep(**request)
        check(lang+" preparation does not write the index", db.total_changes==changes_before)
        check(lang+" preparation preserves the old file and creates no successor",
              preview.get("ok") is True and preview.get("stored") is False and _read(old_name)==original
              and not os.path.exists(os.path.join(_MEM,new_name+".md")), preview)
        if not preview.get("ok"):
            continue
        write = preview["save_arguments"]
        check(lang+" source SHA is pinned", write["lifecycle"]["legacy_whole_record_sha"]=={old_name:saved["sha"]})
        result = _j(db, **write)
        current = names(query, scope)
        history = names(query, scope, "history")
        past = names(query, scope, at="2026-10-15")
        passed = (result.get("stored") is True and new_name in current and old_name not in current
                  and {old_name,new_name} <= history and old_name in past and new_name not in past)
        check(lang+" explicit revision: current, history and as-of retrieval", passed, result.get("error"))
        wins += int(passed)
        check(lang+" retry is a noop and old bytes remain", _j(db, **write).get("result")=="noop" and _read(old_name)==original)
        next_request = dict(request, predecessor=new_name, name=new_name+"_proposal", status="proposed")
        for key in ("topic", "facet", "replace_whole_record"):
            next_request.pop(key)
        next_preview = prep(**next_request)
        check(lang+" tracked revision inherits identity without copying old evidence or SHA pins",
              next_preview.get("ok") and next_preview["save_arguments"]["lifecycle"]["topic"]==old
              and "legacy_whole_record_sha" not in next_preview["save_arguments"]["lifecycle"])
        if next_preview.get("ok"):
            _j(db, **next_preview["save_arguments"])
            check(lang+" a proposal does not become current", new_name+"_proposal" not in names(query,scope))
        check(lang+" other scopes cannot supply a predecessor", not prep(**dict(next_request,project_id="unrelated")).get("ok"))
    print("Controlled current/history/as-of improvements: %d/%d; not a general-language benchmark" % (wins,len(langs)))
    check("all script fixtures pass", wins==len(langs))
    check("capabilities advertise preparation", capabilities.tool_flags(server.TOOLS)["remember"]["prepare_revision"])
    # Two clients prepare the same tracked predecessor. Only the first may supersede it.
    request = dict(predecessor="en_changed", name="race_first", description="New arrangement", type="user",
                   body="Water orchids on Saturday", project_id="en-revision", status="active",
                   valid_from="2026-11-02", change_reason="Confirmed change", evidence=["User said Saturday"])
    a, b = prep(**request), prep(**dict(request,name="race_second"))
    check("two clients can preview the same fact snapshot", a.get("ok") and b.get("ok"))
    if a.get("ok") and b.get("ok"):
        _j(db, **a["save_arguments"])
        failed = _j(db, **b["save_arguments"])
        check("concurrent successor invalidates the second snapshot", failed.get("error",{}).get("code")=="fact_changed"
              and not os.path.exists(os.path.join(_MEM,"race_second.md")), failed)
    future = prep(**dict(request, predecessor="race_first", name="future_fact", valid_from="2026-12-01"))
    check("future revision can be prepared", future.get("ok"), future)
    if future.get("ok"):
        _j(db, **future["save_arguments"])
        current = names("Water orchids Saturday", "en-revision")
        later = names("Water orchids Saturday", "en-revision", at="2026-12-02")
        check("scheduled change waits until its effective date", "race_first" in current and "future_fact" not in current
              and "future_fact" in later and "race_first" not in later)
    db.execute("UPDATE docs SET name='race_first' WHERE name='en_prior' AND project_id='en-revision'")
    try:
        check("ambiguous same-scope predecessors are refused", prep(**dict(request, predecessor="race_first", name="ambiguous_new"))
              .get("error",{}).get("code")=="predecessor_unavailable")
    finally:
        db.execute("UPDATE docs SET name='en_prior' WHERE path=?", (os.path.join(_MEM,"en_prior.md"),))
        db.commit()
    for tracked in (False,True):
        name = "race_first" if tracked else "legacy_note"
        r = dict(request, predecessor=name, name="edited_"+str(tracked), valid_to="2026-12-01")
        if not tracked:
            r.pop("project_id");r.update(topic="legacy",facet="fact",replace_whole_record=True)
        prepared = prep(**r)
        check("prepare before source edit "+str(tracked), prepared.get("ok"), prepared)
        path = os.path.join(_MEM,name+".md")
        with open(path,"a",encoding="utf-8") as fh: fh.write("\nA person changed this source.\n")
        stale = prep(**r)
        check("stale indexed source is refused "+str(tracked), stale.get("error",{}).get("code")=="index_pending")
        if prepared.get("ok"):
            denied = _j(db, **prepared["save_arguments"])
            check("write refuses a source changed after prepare "+str(tracked), not denied.get("ok")
                  and not os.path.exists(os.path.join(_MEM,r["name"]+".md")), denied)
        store.reindex(db,recalibrate=False)
    cross_old = "\ub09c\ucd08\ub294 \uc6d4\uc694\uc77c\uc5d0 \ubb3c\uc744 \uc900\ub2e4"
    _j(db, name="cross_old", description=cross_old, body=cross_old, type="user", project_id="cross",
       lifecycle=dict(topic="\ub09c\ucd08",facet="\uae09\uc218",status="active",valid_from="2026-10-01"))
    cross = prep(predecessor="cross_old", name="cross_new", description="Water the orchid on Friday",
                 body="Water the orchid on Friday", type="user", project_id="cross", status="active",
                 valid_from="2026-11-01", change_reason="Confirmed in another language", evidence=["User correction"])
    check("cross-language preparation keeps the original identity", cross.get("ok") and
          cross["save_arguments"]["lifecycle"]["topic"]=="\ub09c\ucd08")
    if cross.get("ok"):
        _j(db, **cross["save_arguments"])
        check("query in predecessor language resolves to the new-language fact",
              names(cross_old,"cross")=={"cross_new"})
    # Different languages can assign different identifiers to the SAME untracked root.
    # The root's competing replacements, not only same-identity facts, are the guard.
    _j(db, name="shared_legacy", description="Orchid watering", body="Water on Monday orchidlegacyanchor", type="user", project_id="shared")
    common = dict(predecessor="shared_legacy", name="owner_a", description="Water Friday", body="Water Friday",
                  type="user", project_id="shared", topic="orchid", facet="watering", status="active",
                  valid_from="2026-11-01", change_reason="User correction", evidence=["User confirmed"], replace_whole_record=True)
    first = prep(**common)
    second_request = dict(common,name="owner_b",topic="\u862d",facet="\u6c34\u3084\u308a")
    second = prep(**second_request)
    check("different-identity clients may prepare before any owner exists", first.get("ok") and second.get("ok"))
    if first.get("ok") and second.get("ok"):
        _j(db, **first["save_arguments"])
        from brain import lifecycle
        fresh = lifecycle.prepare(db,"owner_b",second["save_arguments"])
        check("legacy-family snapshot changes across fact identities", fresh["expected_fact_sha"]!=second["save_arguments"]["expected_fact_sha"])
        denied = _j(db, **second["save_arguments"])
        check("different-identity second whole replacement is refused", not denied.get("ok") and not os.path.exists(os.path.join(_MEM,"owner_b.md")))
        check("repreparing cannot bypass an established legacy identity", not prep(**second_request).get("ok"))
        check("legacy current state has exactly one replacement", names("orchidlegacyanchor","shared")=={"owner_a"})
    # A saved proposal must not become a back door when later promoted to active.
    _j(db,name="branch_root",description="Orchid arrangement",body="Water Monday",type="user",project_id="branches")
    draft_args = dict(common, predecessor="branch_root",name="branch_draft",project_id="branches",status="proposed",topic="\u862d",facet="\u6c34\u3084\u308a")
    draft = prep(**draft_args)
    winner = prep(**dict(common,predecessor="branch_root",name="branch_winner",project_id="branches"))
    check("alternative proposal and active request can be prepared", draft.get("ok") and winner.get("ok"))
    if draft.get("ok") and winner.get("ok"):
        _j(db,**draft["save_arguments"])
        check("new proposal invalidates earlier legacy-family snapshot", not _j(db,**winner["save_arguments"]).get("ok"))
        winner = prep(**dict(common,predecessor="branch_root",name="branch_winner",project_id="branches"))
        check("reviewed proposal does not prevent an explicit active choice", winner.get("ok"))
        if winner.get("ok"):
            _j(db,**winner["save_arguments"])
            promotion = dict(draft_args,predecessor="branch_draft",name="branch_promoted",status="active")
            check("different-identity proposal promotion cannot fork the legacy root", not prep(**promotion).get("ok"))
    for patch in ({"topic": []},{"status": "archived"},{"replace_whole_record": "yes"},{"evidence": []}):
        check("malformed preparation is an error, not an exception", prep(**dict(request,**patch)).get("ok") is False)

    # A page must not fetch and diff every body in a long fact history.
    for i in range(12):
        meta = dict(topic="pagination", facet="schedule", valid_from="2026-11-01",
                    change_reason="Confirmed correction", evidence=["User confirmation"])
        if i:
            meta["supersedes"] = ["paged_"+str(i-1)]
        args = dict(name="paged_"+str(i), description="Schedule", type="user", body="Revision "+str(i),
                    project_id="paged", lifecycle=meta)
        preview = _j(db, **dict(args, prepare_only=True))
        check("history fixture saved "+str(i), _j(db, **dict(args, expected_fact_sha=preview["expected_fact_sha"])).get("stored"))
    with lifecycle.view("current", "2026-11-16"):
        full = lifecycle.history(db, "paged_0", "paged", 0, 25)
        statements = []
        db.set_trace_callback(statements.append)
        try:
            page = lifecycle.history(db, "paged_0", "paged", 4, 3)
        finally:
            db.set_trace_callback(None)
        body_reads = [sql for sql in statements if "SELECT " in sql and "body" in sql and "FROM docs" in sql]
        check("history page preserves bodies, changes and metadata", page["revisions"]==full["revisions"][4:7])
        check("history page keeps current names outside the page", page["current_names"]==["paged_11"] and page["total"]==12)
        check("three history revisions read at most four bodies", len(body_reads)<=4, len(body_reads))
        empty = lifecycle.history(db, "paged_0", "paged", 12, 3)
        check("empty revision page still reports current state", empty["revisions"]==[] and empty["current_names"]==["paged_11"] and not empty["revision_has_more"])


def review_checks():
    import sqlite3
    from brain import lifecycle, memoryreview
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY,name TEXT,path TEXT,sha TEXT,source TEXT,kind TEXT,project_id TEXT,lifecycle TEXT)")
    def add(name, meta=None, scope="household", kind="user"):
        db.execute("INSERT INTO docs(name,path,sha,source,kind,project_id,lifecycle) VALUES(?,?,?,?,?,?,?)",
                   (name, "/unopened/"+name, name, "memory", kind, scope, json.dumps(meta or {})))
    def fact(topic, **kw):
        return dict(topic=topic, facet="schedule", status="active", valid_from="2026-01-01", evidence=["confirmed"], **kw)
    add("legacy")
    add("current", fact("current"))
    scheduled=fact("future");scheduled["valid_from"]="2027-01-01";add("scheduled", scheduled)
    add("expired", fact("past", valid_to="2026-02-01"))
    proposed=fact("proposal");proposed["status"]="proposed";add("proposal", proposed)
    add("conflict_a", fact("conflict"));add("conflict_b", fact("conflict"))
    add("broken", fact("broken",supersedes=["absent"]))
    add("cycle_a", fact("cycle",supersedes=["cycle_b"]))
    add("cycle_b", fact("cycle",supersedes=["cycle_a"]))
    add("metadata_gap",dict(topic="gap",facet="schedule",status="active"),kind="")
    add("invalid",dict(status="invalid",issue="bad metadata"))
    add("other_private_name",scope="other")
    add("unscoped",scope="")
    for n, topic in enumerate(("\uadfc\ubb34", "\u65e5\u7a0b", "\u0645\u0648\u0639\u062f", "caf\u00e9", "\u092f\u094b\u091c\u0928\u093e")):
        add("language_"+str(n), fact(topic))
    db.commit()
    statements=[]
    db.set_trace_callback(statements.append)
    writes={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_DROP_TABLE}
    db.set_authorizer(lambda action,*unused: sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK)
    args=dict(scope="household",as_of="2026-11-17",limit=100)
    out=json.loads(server.call_tool(db,"memory_review",args))
    found={r['name']:r for r in out['items']}
    expected={"legacy","proposal","conflict_a","conflict_b","broken","cycle_a","cycle_b","metadata_gap","invalid"}
    check("review finds every labelled issue and no healthy/scheduled/expired false flag",set(found)==expected,found.keys())
    check("review distinguishes missing evidence, dates and category",set(found['metadata_gap']['reasons'])=={'missing_kind','missing_recorded_evidence','missing_effective_start'})
    check("review detects cyclic history independently of superseded status",all('revision_cycle' in found[n]['reasons'] for n in ('cycle_a','cycle_b')))
    check("review scope excludes other scopes even in counts",out['counts']['documents']==17 and 'other_private_name' not in json.dumps(out))
    check("review uses metadata only and changes no rows",db.total_changes==19 and not any('body' in q.lower() for q in statements),statements)
    all_rows=json.loads(server.call_tool(db,"memory_review",dict(args,only_attention=False)))
    check("all-record mode retains healthy metadata",len(all_rows['items'])==17)
    check("healthy Unicode facts stay current",all(r["current"] is True and not r["reasons"] for r in all_rows["items"] if r["name"].startswith("language_")) and out["counts"]["current"]==7)
    first=memoryreview.review(db,dict(args,limit=2))
    page=[]
    for offset in range(0,first['total'],2):
        r=memoryreview.review(db,dict(args,offset=offset,limit=2,as_of=first['as_of'],expected_snapshot=first['snapshot']))
        page.extend(r['items'])
    check("snapshot pages exactly reproduce full issue inventory",page==out['items'])
    unscoped=memoryreview.review(db,dict(args,scope=""))
    check("empty scope selects only unscoped records",unscoped['counts']['documents']==1 and unscoped['items'][0]['name']=='unscoped')
    for bad in ({'offset':1},{'limit':True},{'scope':None},{'only_attention':1},{'as_of':'not-a-date'},{'unexpected':1}):
        check("review rejects invalid arguments "+str(bad),memoryreview.review(db,bad).get('error',{}).get('code')=='invalid_argument')
    db.set_authorizer(lambda *unused: sqlite3.SQLITE_OK)
    db.execute("UPDATE docs SET kind='preference' WHERE name='legacy'")
    changed=memoryreview.review(db,dict(args,as_of=first['as_of'],expected_snapshot=first['snapshot']))
    check("changed metadata rejects stale pagination",changed.get('error',{}).get('code')=='snapshot_changed')
    fresh=memoryreview.review(db,args)
    check("restarting observes the category change",fresh['snapshot']!=first['snapshot'] and fresh['counts']['kinds']['preference']==1)
    flags=__import__('brain.capabilities',fromlist=['tool_flags']).tool_flags
    check("review capability follows declaration",flags(server.TOOLS)['memory_review']['available'] and not flags([t for t in server.TOOLS if t['name']!='memory_review'])['memory_review']['available'])
    db.close()


def extraction_checks(db):
    import copy
    from brain import lifecycle, capabilities
    quotes=["The orchid needs water on Friday.","\uae08\uc694\uc77c\uc5d0 \ubb3c\uc744 \uc90d\ub2c8\ub2e4.","\u91d1\u66dc\u65e5\u306b\u6c34\u3092\u3084\u308b\u3002","\u0627\u0644\u0633\u0642\u064a \u064a\u0648\u0645 \u0627\u0644\u062c\u0645\u0639\u0629.","Le caf\u00e9 ouvre vendredi."]
    quotes += ["Water" + chr(cp) + "on Friday." for cp in (0x2028, 0x2029, 0x85)]
    def prepare(args):
        return json.loads(server.call_tool(db,"prepare_fact",args,_measure=True))
    for i, quote in enumerate(quotes):
        source='repeated_source_'+str(i)
        text=quote+'\n'+quote+'\nUnique passage.\nOther passage.'
        saved=_j(db,name=source,description='Repeated source',type='reference',body=text)
        args=dict(source_name=source,source_sha=saved['sha'],quotes=['Unique passage.'],name='repeated_proposal_'+str(i),description='Proposal',type='reference',body='Unique passage.',topic='repeated-'+str(i),facet='claim')
        original=_read(source)
        check('preparation rejects repeated quotes '+str(i),prepare(dict(args,quotes=[quote]))['error']['code']=='ambiguous_quote')
        payload=prepare(args)['save_arguments']
        for rewrite_evidence in (False, True):
            changed=copy.deepcopy(payload)
            changed['extracted_from']['spans']=[dict(start=0,end=len(quote),text=quote)]
            if rewrite_evidence:changed['lifecycle']['evidence']=rc.extraction_evidence(changed['extracted_from'])
            check('save rejects repeated quotes even with rewritten evidence '+str((i,rewrite_evidence)),_j(db,**changed)['error']['code']=='ambiguous_quote' and not os.path.exists(os.path.join(_MEM,args['name']+'.md')))
        changed=copy.deepcopy(payload);start=text.index('Other passage.')
        changed['extracted_from']['spans']=[dict(start=start,end=start+len('Other passage.'),text='Other passage.')]
        check('save rejects changed unique quote with old evidence '+str(i),_j(db,**changed)['error']['code']=='extraction_evidence_mismatch')
        for evidence in ([], ['invented evidence'], payload['lifecycle']['evidence']+['extra source claim']):
            changed=copy.deepcopy(payload);changed['lifecycle']['evidence']=evidence
            check('save requires exact matching evidence '+str(i),_j(db,**changed).get('error',{}).get('code') in ('extraction_evidence_mismatch','invalid_argument'))
        first=_j(db,**payload);before=_read(args['name'])
        check('unchanged preparation saves and retries without source mutation '+str(i),first.get('result')=='new' and _j(db,**payload).get('result')=='noop' and _read(args['name'])==before and _read(source)==original)
    for i,quote in enumerate(quotes):
        source="mixed_source_"+str(i);target="extracted_fact_"+str(i)
        scope="extraction_"+str(i)
        saved=_j(db,name=source,description="Mixed source",type="reference",body=quote+"\nThe address remains unchanged.",project_id=scope)
        original=_read(source)
        args=dict(source_name=source,source_sha=saved['sha'],quotes=[quote],name=target,description="One proposed schedule",type="user",body=quote,project_id=scope,topic="orchid",facet="watering")
        before=db.total_changes
        result=prepare(args)
        check("extraction preparation is read-only "+str(i),result.get('ok') and db.total_changes==before and not os.path.exists(os.path.join(_MEM,target+'.md')))
        payload=result['save_arguments'];span=payload['extracted_from']['spans'][0]
        check("exact Unicode source span "+str(i),span==dict(start=0,end=len(quote),text=quote))
        check("extraction is proposed and never supersedes its mixed source "+str(i),payload['lifecycle']['status']=='proposed' and not payload['lifecycle'].get('supersedes'))
        first=_j(db,**payload);retry=_j(db,**payload)
        check("save and retry retain provenance "+str(i),first.get('result')=='new' and retry.get('result')=='noop' and first.get('extracted_from')==payload['extracted_from'])
        from brain import privacy
        check("extracted proposals never enter embedding "+str(i),privacy.is_local_only(os.path.join(_MEM,target+".md")))
        meta,_=store.parse_frontmatter(_read(target))
        check("provenance survives in the file "+str(i),json.loads(meta['extracted_from'])==payload['extracted_from'])
        snap=lifecycle.snapshot(db);did=snap.by_name[(scope,target)][0]
        check("proposal excluded from current resolution "+str(i),snap.resolve(did)==[] and snap.info(did)['status']=='proposed')
        check("mixed original remains byte-identical "+str(i),_read(source)==original)
        check("wrong quotes are refused "+str(i),prepare(dict(args,name=target+'_bad',quotes=['invented quote']))['error']['code']=='quote_mismatch')
        check("scope cannot be borrowed "+str(i),prepare(dict(args,name=target+'_other',project_id='unrelated'))['error']['code']=='source_unavailable')
        direct=copy.deepcopy(payload);direct['name']=target+'_active';direct['lifecycle']['status']='active'
        check("direct extraction cannot bypass proposal status "+str(i),_j(db,**direct)['error']['code']=='invalid_argument')
        direct=prepare(dict(args,name=target+'_tampered',identity_reference=dict(name=target,sha=first['sha'],reason='Reviewed continuation of the same fact')))['save_arguments'];direct['extracted_from']['spans'][0]['start']+=1;direct['extracted_from']['spans'][0]['end']+=1
        check("save validates the span again "+str(i),_j(db,**direct)['error']['code']=='quote_mismatch')
        waiting=prepare(dict(args,name=target+'_waiting',identity_reference=dict(name=target,sha=first['sha'],reason='Reviewed continuation of the same fact')))['save_arguments']
        with open(os.path.join(_MEM,source+'.md'),'a',encoding='utf-8') as f:f.write('\nNew source information.\n')
        check("stale source rejected on preparation "+str(i),prepare(dict(args,name=target+'_stale'))['error']['code']=='source_changed')
        check("stale source rejected on save "+str(i),_j(db,**waiting)['error']['code']=='source_changed' and not os.path.exists(os.path.join(_MEM,target+'_waiting.md')))
        check("successful old request remains a noop after source edit "+str(i),_j(db,**payload).get('result')=='noop')
        # Confirm only the extracted proposal, never the mixed original.
        confirmation=json.loads(server.call_tool(db,'prepare_revision',dict(predecessor=target,name=target+'_confirmed',description='Confirmed schedule',type='user',body=quote,project_id=scope,status='active',valid_from='2026-11-17',change_reason='Explicit confirmation',evidence=['The source owner confirmed this fact']),_measure=True))
        check("confirmation preserves embedding opt-out "+str(i),confirmation['save_arguments'].get('embed') is False)
        loose=copy.deepcopy(confirmation['save_arguments']);loose.pop('embed',None)
        check("direct confirmation cannot drop embedding opt-out "+str(i),_j(db,**loose)['error']['code']=='privacy_required')
        final=_j(db,**confirmation['save_arguments'])
        check("confirmation revises only the proposal "+str(i),final.get('result')=='new' and final['lifecycle']['supersedes']==[target] and source not in final['lifecycle']['supersedes'])
    # Every Python splitlines separator must be inert in scalar metadata.
    saved=_j(db,name='scalar_source',description='Source',type='reference',body='Water on Friday.')
    for cp in (10,13,11,12,28,29,30,133,8232,8233):
        for field in ('source','description','type'):
            target='scalar_'+field+'_'+str(cp)
            args=dict(source_name='scalar_source',source_sha=saved['sha'],quotes=['Water on Friday.'],name=target,description='Proposal',type='user',body='Water on Friday.',topic='scalar-'+target,facet='water')
            payload=prepare(args)['save_arguments']
            br=chr(cp)
            payload[field]='note'+br+'lifecycle: {"topic":"evil","facet":"water","status":"active"}'+br+'embed: true'+br+'project_id: foreign'
            result=_j(db,**payload)
            if field=='type':
                check('type separator rejected '+str(cp),not result.get('ok') and not os.path.exists(os.path.join(_MEM,target+'.md')))
                continue
            meta,_=store.parse_frontmatter(_read(target))
            snap=lifecycle.snapshot(db);did=snap.by_name[('',target)][0]
            check('scalar cannot inject metadata '+field+' '+str(cp),result.get('ok') and json.loads(meta['lifecycle'])==payload['lifecycle'] and meta.get('embed')=='false' and not meta.get('project_id') and snap.info(did)['status']=='proposed' and snap.resolve(did)==[])
            check('scalar save retry remains noop '+field+' '+str(cp),_j(db,**payload).get('result')=='noop')
    saved=_j(db,name='repeated_quote_source',description='Repeated passage',type='reference',body='same quote; same quote')
    amb=prepare(dict(source_name='repeated_quote_source',source_sha=saved['sha'],quotes=['same quote'],name='ambiguous_fact',description='One fact',type='user',body='same quote',topic='one',facet='fact'))
    check("ambiguous repeated quotation requires more context",amb['error']['code']=='ambiguous_quote')
    before=capabilities.tool_flags(server.TOOLS)
    without=capabilities.tool_flags([t for t in server.TOOLS if t['name']!='prepare_fact'])
    check("extraction capability follows its declaration",before['remember']['prepare_fact'] and not without['remember']['prepare_fact'])


def identity_checks(db):
    import sqlite3
    import unicodedata
    from brain import lifecycle, capabilities
    scope='identity-fixture'
    def call(tool, **args):
        return json.loads(server.call_tool(db,tool,args,_measure=True))
    def seed(name, topic, facet='water', project=scope):
        return _j(db,name=name,description='Reviewed identity',type='user',body='Water on Friday.',project_id=project,
                  lifecycle=dict(topic=topic,facet=facet,status='proposed'),embed=False)
    anchor=seed('identity_anchor','caf\u00e9')
    seed('identity_nfd',unicodedata.normalize('NFD','caf\u00e9'))
    _j(db,name='identity_current',description='Current schedule',type='user',body='Water on Friday.',project_id=scope,
       lifecycle=dict(topic='caf\u00e9',facet='water',status='active',valid_from='2026-01-01',evidence=['Explicit confirmation']),embed=False)
    seed('identity_homonym','another-plant')
    seed('identity_facet','caf\u00e9','food')
    seed('identity_case','Caf\u00e9')
    seed('identity_foreign','caf\u00e9',project='identity-other')
    _j(db,name='identity_untracked',description='Untracked',type='user',body='Water on Friday.',project_id=scope)
    names=['identity_anchor','identity_nfd','identity_homonym','identity_facet','identity_case','identity_foreign','identity_untracked']
    before=db.total_changes
    denied=[]
    def authorizer(action, *rest):
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
            denied.append(action);return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorizer)
    try:
        reviewed=call('review_identity',names=names,project_id=scope)
    finally:
        db.set_authorizer(lambda *unused: sqlite3.SQLITE_OK)
    check('identity review has no SQL writes',reviewed.get('ok') and not denied and db.total_changes==before)
    check('a proposed identity still shows the current fact',reviewed['items'][0]['current_fact_names']==['identity_current'] and reviewed['items'][0]['current'] is False)
    groups={tuple(g['names']) for g in reviewed['groups']}
    check('NFC groups only; homonyms, facets and case remain distinct',groups=={('identity_anchor','identity_nfd'),('identity_homonym',),('identity_facet',),('identity_case',)})
    check('foreign identity not exposed and untracked identity not reusable',reviewed['items'][5]==dict(name='identity_foreign',reusable=False,issue='identity_unavailable') and not reviewed['items'][6]['reusable'])
    check('omitted scope means unscoped only',call('review_identity',names=['identity_anchor'])['items']==[dict(name='identity_anchor',reusable=False,issue='identity_unavailable')])
    check('duplicate and oversized candidate lists rejected',not call('review_identity',names=['a','a']).get('ok') and not call('review_identity',names=[str(i) for i in range(11)]).get('ok'))
    texts=['Water on Friday.','\uae08\uc694\uc77c\uc5d0 \ubb3c\uc744 \uc90d\ub2c8\ub2e4.','\u91d1\u66dc\u65e5\u306b\u6c34\u3092\u3084\u308b\u3002','\u0627\u0644\u0633\u0642\u064a \u064a\u0648\u0645 \u0627\u0644\u062c\u0645\u0639\u0629.','Arroser vendredi.']
    baseline, reused, proposals=set(),set(),[]
    original_anchor=_read('identity_anchor')
    for i,text in enumerate(texts):
        source='identity_source_'+str(i)
        saved=_j(db,name=source,description='Mixed source',type='reference',body=text+'\nOther unchanged facts.',project_id=scope)
        original=_read(source)
        args=dict(source_name=source,source_sha=saved['sha'],quotes=[text],name='identity_proposal_'+str(i),description='Translated proposal',type='user',body=text,project_id=scope)
        if i==0:
            denied=call('prepare_fact',**dict(args,name='identity_collision_prepare',topic=unicodedata.normalize('NFD','caf\u00e9'),facet='water'))
            check('NFC-equivalent existing identity requires a reviewed reference during preparation',(denied.get('error') or {}).get('code')=='identity_reference_required')
            fresh=call('prepare_fact',**dict(args,name='identity_fresh_retry',topic='fresh-extraction-identity',facet='value'))
            check('a distinct new identity needs no reference',fresh.get('ok') and fresh['total']==0)
            bypass=dict(fresh['save_arguments'],name='identity_collision_write')
            bypass['lifecycle']=dict(bypass['lifecycle'],topic=unicodedata.normalize('NFD','caf\u00e9'),facet='water')
            bypass['expected_fact_sha']=lifecycle.prepare(db,bypass['name'],bypass)['expected_fact_sha']
            refused=_j(db,**bypass)
            check('direct new extraction cannot bypass the reviewed-reference guard',(refused.get('error') or {}).get('code')=='identity_reference_required' and not os.path.exists(os.path.join(_MEM,bypass['name']+'.md')))
            saved_fresh=_j(db,**fresh['save_arguments']);saved_text=_read(fresh['save_arguments']['name'])
            seed('identity_later_collision','fresh-extraction-identity',facet='value')
            check('saved extraction noop survives a later identity collision',saved_fresh.get('result')=='new' and _j(db,**fresh['save_arguments']).get('result')=='noop' and _read(fresh['save_arguments']['name'])==saved_text)
            seed('identity_foreign_only','foreign-only',project='identity-other')
            foreign=call('prepare_fact',**dict(args,name='identity_foreign_control',topic='foreign-only',facet='water'))
            check('foreign scope does not require a reference',foreign.get('ok') and foreign['total']==0)
            seed('identity_unicode_facet','facet-topic',facet='caf\u00e9')
            facet=call('prepare_fact',**dict(args,name='identity_facet_collision',topic='facet-topic',facet=unicodedata.normalize('NFD','caf\u00e9')))
            check('NFC-equivalent facets also require a reference',(facet.get('error') or {}).get('code')=='identity_reference_required')
        control=call('prepare_fact',**dict(args,name='identity_control_'+str(i),topic=text,facet='water'))
        baseline.add(lifecycle.identity(control['save_arguments']['lifecycle']))
        _j(db,**control['save_arguments'])
        ref=dict(name='identity_anchor',sha=anchor['sha'],reason='Reviewed as the same plant and watering fact; language differs')
        if i==0:ref['reason']+='\u2028Unicode\u2029evidence\u0085preserved'
        request=call('prepare_fact',**dict(args,identity_reference=ref))
        check('reviewed identity inherited '+str(i),request.get('ok') and request['save_arguments']['lifecycle']['topic']=='caf\u00e9' and request['identity_reference']==ref)
        payload=request['save_arguments'];reused.add(lifecycle.identity(payload['lifecycle']));proposals.append(payload['name'])
        if i==0:
            import copy
            changed=copy.deepcopy(payload)
            changed['lifecycle']['evidence'][-1]='Identity selected: '+json.dumps(dict(ref,reason='Unreviewed different reason'),ensure_ascii=False,sort_keys=True)
            check('structured identity reference cannot contradict lifecycle evidence',_j(db,**changed).get('error',{}).get('code')=='extraction_evidence_mismatch' and not os.path.exists(os.path.join(_MEM,payload['name']+'.md')))
        result=_j(db,**payload)
        stored_meta,_=store.parse_frontmatter(_read(payload['name']))
        check('structured identity reference round trips '+str(i),json.loads(stored_meta['identity_reference'])==ref and result['identity_reference']==ref)
        check('identity proposal save and retry '+str(i),result.get('result')=='new' and _j(db,**payload).get('result')=='noop')
        check('identity evidence persisted without changing original '+str(i),anchor['sha'] in _read(payload['name']) and any(ref['reason'] in e for e in json.loads(stored_meta['lifecycle'])['evidence']) and _read(source)==original and _read('identity_anchor')==original_anchor)
        snap=lifecycle.snapshot(db);did=snap.by_name[(scope,payload['name'])][0]
        check('identity reuse does not activate or supersede '+str(i),snap.info(did)['status']=='proposed' and snap.resolve(did)==[] and not payload['lifecycle'].get('supersedes'))
    check('controlled identity fragmentation: 5 translated groups become 1 reused group',len(baseline)==5 and len(reused)==1)
    history=lifecycle.history(db,'identity_anchor',scope,limit=100)
    check('shared history includes all proposals but no homonyms',set(proposals)<=set(r['name'] for r in history['revisions']) and 'identity_homonym' not in {r['name'] for r in history['revisions']})
    args['name']='identity_pending'
    ref=dict(name='identity_anchor',sha=anchor['sha'],reason='Same independently changing fact')
    def prep(**extra): return call('prepare_fact',**dict(args,identity_reference=ref,**extra))
    for label, br in (('lf', '\n'), ('crlf', '\r\n'), ('cr', '\r')):
        body = 'First line.'+br+'Second line with Unicode \uac31\uc2e0.'+br+'Last line.'
        prepared_lines = prep(name='identity_lines_'+label, body=body)['save_arguments']
        check('preparation preserves original line endings '+label, prepared_lines['body'] == body)
        created = _j(db, **prepared_lines)
        path = os.path.join(_MEM, prepared_lines['name']+'.md')
        with open(path, 'rb') as f: saved_bytes = f.read()
        receipt = rc.current(path)['payload_sha']
        retried = _j(db, **prepared_lines)
        with open(path, 'rb') as f: after_retry = f.read()
        check('reviewed multiline exact retry is noop '+label, created.get('result') == 'new' and retried.get('result') == 'noop')
        check('multiline retry preserves bytes and receipt '+label, after_retry == saved_bytes and
              rc.current(path)['payload_sha'] == receipt == rc.payload_sha(prepared_lines))
        changed = _j(db, **dict(prepared_lines, body=body+' changed', expected_sha=created['sha']))
        with open(path, 'rb') as f: after_change = f.read()
        check('real multiline revision is still refused '+label,
              (changed.get('error') or {}).get('code') == 'revision_required' and after_change == saved_bytes)
        ordinary = dict(name='plain_lines_'+label, description='Line endings', type='user', body=body)
        check('ordinary multiline exact retry is noop '+label,
              _j(db, **ordinary).get('result') == 'new' and _j(db, **ordinary).get('result') == 'noop')
    check('translated identity cannot override reviewed identifiers',prep(topic='translated')['error']['code']=='identity_mismatch')
    for replacement,code in [(dict(ref,sha='0'*40),'identity_changed'),(dict(ref,name='identity_foreign'),'identity_unavailable'),(dict(ref,name='identity_untracked'),'invalid_identity'),(dict(ref,reason=''),'invalid_argument')]:
        result=call('prepare_fact',**dict(args,identity_reference=replacement))
        check('bad reference rejected '+code,result['error']['code']==code)
    for n in ('identity_invalid','identity_cycle_a','identity_cycle_b'):
        meta=dict(topic='broken-identity',facet='water',status='active',valid_from='2026-01-01',evidence=['Fixture'],change_reason='Fixture')
        if n=='identity_invalid':meta['status']='not-a-state'
        else:meta['supersedes']=['identity_cycle_b' if n.endswith('_a') else 'identity_cycle_a']
        with open(os.path.join(_MEM,n+'.md'),'w',encoding='utf-8') as f:
            f.write('---\nname: '+n+'\nproject_id: '+scope+'\nlifecycle: '+json.dumps(meta)+'\n---\nWater on Friday.\n')
    store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    bad_candidates=call('review_identity',names=['identity_invalid','identity_cycle_a'],project_id=scope)
    check('invalid and cyclic identities cannot be reused',all(not x['reusable'] and x['issue']=='invalid_identity' for x in bad_candidates['items']))
    collision=call('prepare_fact',**dict(args,name='identity_collision_pending',identity_reference=ref))['save_arguments']
    config=store.load_config()
    duplicate=os.path.join(_TMP,'duplicate_identity')
    os.makedirs(duplicate,exist_ok=True)
    with open(os.path.join(duplicate,'identity_anchor.md'),'w',encoding='utf-8') as f:
        f.write(original_anchor.replace('caf\u00e9','another-identity'))
    changed_config=dict(config,sources=config['sources']+[dict(name='memory',path=duplicate,include=['*.md'],max_depth=1)])
    with open(os.environ['BRAIN_CONFIG'],'w',encoding='utf-8') as f:json.dump(changed_config,f)
    try:
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        check('new same-name different-identity candidate is ambiguous',prep()['error']['code']=='identity_unavailable')
        result=_j(db,**collision)
        check('save rejects identity name collision outside the fact group',(result.get('error') or {}).get('code')=='identity_unavailable' and not os.path.exists(os.path.join(_MEM,collision['name']+'.md')),str(result.get('result')))
        check('successful prior request remains noop after name collision',_j(db,**payload).get('result')=='noop')
    finally:
        with open(os.environ['BRAIN_CONFIG'],'w',encoding='utf-8') as f:json.dump(config,f)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    # A reviewed reference is scope/SHA-qualified; graph name links are not.
    foreign_anchor=os.path.join(duplicate,'identity_anchor.md')
    with open(foreign_anchor,'w',encoding='utf-8') as f:
        f.write(original_anchor.replace('project_id: '+scope,'project_id: foreign-homonym').replace('caf\u00e9','billing'))
    with open(os.environ['BRAIN_CONFIG'],'w',encoding='utf-8') as f:json.dump(changed_config,f)
    try:
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        scoped_ref=dict(ref,reason='Reviewed same fact as [[identity_anchor]].')
        scoped=call('prepare_fact',**dict(args,name='identity_scoped_reference',identity_reference=scoped_ref))['save_arguments']
        check('identity provenance is not an ambiguous graph name',scoped['identity_reference']==scoped_ref and scoped['links']==[])
        old_request=dict(scoped,links=[scoped['extracted_from']['name'],ref['name']])
        rejected=_j(db,**old_request)
        check('old unsaved generated links require preparation again',(rejected.get('error') or {}).get('code')=='identity_reprepare_required')
        check('scoped reference saves with foreign homonym',_j(db,**scoped).get('result')=='new')
        from brain import graphstore
        proposal_id=db.execute('SELECT id FROM docs WHERE name=?',(scoped['name'],)).fetchone()[0]
        foreign_id=db.execute('SELECT id FROM docs WHERE path=?',(foreign_anchor,)).fetchone()[0]
        check('foreign homonym receives no generated identity edge',not graphstore.LOCAL.linked(db,proposal_id,foreign_id))
        quote='Source evidence mentioning [[identity_anchor]].'
        quoted_source=_j(db,name='identity_quoted_source',description='Quoted source',type='reference',body=quote,project_id=scope)
        quoted=call('prepare_fact',source_name='identity_quoted_source',source_sha=quoted_source['sha'],quotes=[quote],name='identity_quoted_proposal',description='Quoted evidence',type='user',body='Independent proposed fact.',project_id=scope,identity_reference=scoped_ref)['save_arguments']
        _j(db,**quoted)
        quoted_id=db.execute('SELECT id FROM docs WHERE name=?',(quoted['name'],)).fetchone()[0]
        check('source quotation metadata is not a graph edge',not graphstore.LOCAL.linked(db,quoted_id,foreign_id))
        legacy_args=dict(old_request,name='identity_legacy_footer',body='Keep [[body_authored]] explicit.')
        legacy_args.pop('identity_reference',None)
        legacy_args['extracted_from']=dict(legacy_args['extracted_from'])
        legacy_args['extracted_from'].pop('graph_policy',None)
        legacy_text=rc.render(legacy_args['name'],rc.prepared(legacy_args),'')
        legacy_path=os.path.join(_MEM,legacy_args['name']+'.md')
        with open(legacy_path,'w',encoding='utf-8') as f:f.write(legacy_text)
        legacy_doc=store.read_doc(legacy_path,'memory')
        check('legacy provenance footer excludes source and anchor but preserves authored body links',set(legacy_doc['links'])=={'body_authored'})
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        legacy_id=db.execute('SELECT id FROM docs WHERE path=?',(legacy_path,)).fetchone()[0]
        check('legacy saved proposal no longer links a foreign homonym',not graphstore.LOCAL.linked(db,legacy_id,foreign_id) and _read(legacy_args['name'])==legacy_text)
        authored=call('prepare_fact',source_name='identity_quoted_source',source_sha=quoted_source['sha'],quotes=[quote],name='identity_authored_footer',description='Explicit relationship',type='user',body='Author-written.\n\nrelated: [[identity_quoted_source]]',project_id=scope,identity_reference=scoped_ref)['save_arguments']
        check('new extraction marks authored graph policy',authored['extracted_from']['graph_policy']=='explicit')
        _j(db,**authored)
        authored_path=os.path.join(_MEM,authored['name']+'.md')
        check('new authored related footer survives graph parsing','identity_quoted_source' in store.read_doc(authored_path,'memory')['links'])
        unmarked=dict(authored,name='identity_unmarked_authored',extracted_from=dict(authored['extracted_from']))
        unmarked['extracted_from'].pop('graph_policy')
        unmarked_path=os.path.join(_MEM,unmarked['name']+'.md')
        with open(unmarked_path,'w',encoding='utf-8') as f:f.write(rc.render(unmarked['name'],rc.prepared(unmarked),''))
        check('older authored footer is retained when its payload receipt proves it','identity_quoted_source' in store.read_doc(unmarked_path,'memory')['links'])
        middle = dict(unmarked, name='identity_middle_related', links=['identity_quoted_source'],
                      body='Keep [[body_authored]].\n\nrelated: [[identity_quoted_source]] · [[identity_anchor]]\n\nA later paragraph.')
        middle_path = os.path.join(_MEM, middle['name']+'.md')
        for receipt in (False, True):
            for footer in (False, True):
                text = rc.render(middle['name'], rc.prepared(middle), '')
                if not footer:
                    text = text.rsplit('\n\nrelated: ', 1)[0]+'\n'
                if not receipt:
                    import re
                    text = re.sub(r'(?m)^payload_sha:[^\n]*\n', '', text)
                with open(middle_path, 'w', encoding='utf-8') as f: f.write(text)
                check('middle related links survive receipt='+str(receipt)+' footer='+str(footer),
                      set(store.read_doc(middle_path, 'memory')['links']) == {'body_authored','identity_quoted_source','identity_anchor'})
                store.reindex(db, recalibrate=False, sync=False, heal=False, queue_embedding=False)
                mid = db.execute('SELECT id FROM docs WHERE path=?', (middle_path,)).fetchone()[0]
                sid = db.execute('SELECT id FROM docs WHERE name=? AND project_id=?', ('identity_quoted_source', scope)).fetchone()[0]
                check('reindex restores authored middle relation receipt='+str(receipt)+' footer='+str(footer),
                      graphstore.LOCAL.linked(db, mid, sid))
        os.remove(middle_path)
        bracket_path=os.path.join(_MEM,'plant[old].md')
        with open(bracket_path,'w',encoding='utf-8') as f:f.write('---\nname: plant[old]\nproject_id: '+scope+'\n---\nBracket source evidence.\n')
        bracket_foreign=os.path.join(duplicate,'bracket-foreign.md')
        with open(bracket_foreign,'w',encoding='utf-8') as f:f.write('---\nname: plant[old\nproject_id: other-bracket-scope\n---\nForeign record.\n')
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        bracket_sha=rc.file_sha(bracket_path)
        bracket=call('prepare_fact',source_name='plant[old]',source_sha=bracket_sha,quotes=['Bracket source evidence.'],name='bracket_legacy_proposal',description='Legacy source link',type='user',body='Proposed fact.',project_id=scope,topic='plant',facet='water')['save_arguments']
        bracket['extracted_from']=dict(bracket['extracted_from']);bracket['extracted_from'].pop('graph_policy')
        bracket['links']=['plant[old]']
        rejected=_j(db,**bracket)
        check('unsaved unreferenced legacy extraction requires preparation again',(rejected.get('error') or {}).get('code')=='identity_reprepare_required')
        bracket=rc.prepared(bracket)
        legacy_bracket_path=os.path.join(_MEM,bracket['name']+'.md')
        legacy_bracket_text=rc.render(bracket['name'],bracket,'')
        with open(legacy_bracket_path,'w',encoding='utf-8') as f:f.write(legacy_bracket_text)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        bid=db.execute('SELECT id FROM docs WHERE path=?',(legacy_bracket_path,)).fetchone()[0]
        fid=db.execute('SELECT id FROM docs WHERE path=?',(bracket_foreign,)).fetchone()[0]
        check('legacy bracket provenance creates no foreign graph edge',not graphstore.LOCAL.linked(db,bid,fid))
        for evidence in (None, 7, 'invalid', {}, []):
            malformed = dict(bracket, lifecycle=dict(bracket['lifecycle'], evidence=evidence), body='Keep [[authored]] explicit.')
            text = rc.render('malformed_footer', malformed, '')
            path = os.path.join(_MEM, 'malformed_footer.md')
            with open(path, 'w', encoding='utf-8') as f: f.write(text)
            check('malformed evidence keeps authored links but excludes provenance '+repr(evidence),
                  set(store.read_doc(path, 'memory')['links']) == {'authored'})
        for field in ('lifecycle', 'identity_reference', 'search_aliases'):
            text = rc.render('malformed_footer', dict(bracket, body='Keep [[authored]] explicit.'), '')
            import re
            text = re.sub(r'(?m)^'+field+r':[^\n]*', field+': {invalid', text) if field+':' in text else text.replace('---\n', '---\n'+field+': {invalid\n', 1)
            with open(path, 'w', encoding='utf-8') as f: f.write(text)
            check('unrelated malformed metadata cannot disable source filtering '+field,
                  set(store.read_doc(path, 'memory')['links']) == {'authored'})
        os.remove(path)
        check('successful legacy extraction retry remains noop',_j(db,**bracket).get('result')=='noop' and _read(bracket['name'])==legacy_bracket_text)
        db.execute('INSERT OR IGNORE INTO links(src_id,dst_name) VALUES(?,?)',(quoted_id,ref['name']))
        store.set_meta(db,'graph_provenance_version','0')
        db.commit()
        check('legacy graph fixture starts with contaminated edge',graphstore.LOCAL.linked(db,quoted_id,foreign_id))
        store.reindex(db,recalibrate=True,sync=False,heal=False,queue_embedding=False,refresh_history=False)
        check('normal indexing repairs legacy provenance edges without file edits',store.get_meta(db,'graph_provenance_version')=='6' and not graphstore.LOCAL.linked(db,quoted_id,foreign_id))
        path_a=os.path.join(_MEM,'path_same.md')
        path_b=os.path.join(duplicate,'path_same.md')
        path_src=os.path.join(_MEM,'exact_path_source.md')
        for path,text in ((path_a,'---\nname: path_same\nproject_id: a\n---\nA target.\n'),
                          (path_b,'---\nname: path_same\nproject_id: b\n---\nB target.\n'),
                          (path_src,'---\nname: exact_path_source\nproject_id: a\n---\n[Chosen](path_same.md)\n')):
            with open(path,'w',encoding='utf-8') as f:f.write(text)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        ids={path:db.execute('SELECT id FROM docs WHERE path=?',(path,)).fetchone()[0] for path in (path_a,path_b,path_src)}
        db.execute('INSERT OR IGNORE INTO links(src_id,dst_name) VALUES(?,?)',(ids[path_src],'path_same'))
        store.set_meta(db,'graph_provenance_version','5');db.commit()
        check('old path-name conversion contaminates the other scope',graphstore.LOCAL.linked(db,ids[path_src],ids[path_b]))
        originals={path:open(path,'rb').read() for path in ids}
        store.reindex(db,full=True,recalibrate=False,sync=False,heal=False,queue_embedding=False,refresh_history=False)
        check('full indexing removes old path-name edges and preserves exact path',
              store.get_meta(db,'graph_provenance_version')=='6'
              and graphstore.LOCAL.neighbors(db,[ids[path_src]])=={ids[path_src]:{ids[path_a]}}
              and all(open(path,'rb').read()==body for path,body in originals.items()))



    finally:
        with open(os.environ['BRAIN_CONFIG'],'w',encoding='utf-8') as f:json.dump(config,f)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    target=prep(name='identity_new_target')['save_arguments']
    os.remove(os.path.join(duplicate,'identity_anchor.md'))
    target_copy=os.path.join(duplicate,'identity_new_target.md')
    other_fact=original_anchor.replace('caf\u00e9','another-identity').replace('name: identity_anchor','name: identity_new_target')
    with open(target_copy,'w',encoding='utf-8') as f:f.write(other_fact)
    with open(os.environ['BRAIN_CONFIG'],'w',encoding='utf-8') as f:json.dump(changed_config,f)
    try:
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        result=_j(db,**target)
        check('prepared target name cannot collide with another source',(result.get('error') or {}).get('code')=='name_taken' and not os.path.exists(os.path.join(_MEM,target['name']+'.md')))
        leaked=os.path.join(_MEM,target['name']+'.md')
        if os.path.exists(leaked):os.remove(leaked)
        with open(target_copy,'w',encoding='utf-8') as f:f.write(other_fact.replace('project_id: '+scope,'project_id: other-target-scope'))
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        check('same target name in another scope can save',_j(db,**target).get('result')=='new')
        with open(target_copy,'w',encoding='utf-8') as f:f.write(other_fact)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
        check('successful retry survives a later target name collision',_j(db,**target).get('result')=='noop')
    finally:
        with open(os.environ['BRAIN_CONFIG'],'w',encoding='utf-8') as f:json.dump(config,f)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    alias_source=_j(db,name='CaseSource',description='Case source',type='reference',body='Preserve this original.',project_id=scope)
    alias_request=dict(source_name='CaseSource',source_sha=alias_source['sha'],quotes=['Preserve this original.'],name='casesource',description='Proposed fact',type='user',body='Proposed.',project_id=scope,identity_reference=ref)
    alias=call('prepare_fact',**dict(alias_request,name='distinct_alias_proposal'))['save_arguments']
    alias['name']='casesource'
    original_case=_read('CaseSource')
    alias_path=os.path.join(_MEM,'casesource.md')
    made_alias=not os.path.exists(alias_path)
    if made_alias:os.link(os.path.join(_MEM,'CaseSource.md'),alias_path)
    from unittest.mock import patch
    real_samefile=os.path.samefile
    def alias_samefile(a,b):
        if os.path.abspath(a).casefold()==os.path.abspath(b).casefold():return True
        return real_samefile(a,b)
    with patch.object(os.path,'samefile',side_effect=alias_samefile):
        early=call('prepare_fact',**alias_request)
        rejected=_j(db,**dict(alias,expected_sha=alias_source['sha']))
    check('preparation rejects the source file alias',(early.get('error') or {}).get('code')=='source_target_alias')
    check('case alias cannot overwrite extraction source even with SHA',(rejected.get('error') or {}).get('code')=='source_target_alias' and _read('CaseSource')==original_case)
    if made_alias:os.remove(alias_path)
    occupied=prep(name='occupied_other_scope')['save_arguments']
    occupant=_j(db,name='occupied_other_scope',description='Keep another project',type='reference',body='Original other project.',project_id='other-project')
    original_occupant=_read('occupied_other_scope')
    early=prep(name='occupied_other_scope')
    rejected=_j(db,**dict(occupied,expected_sha=occupant['sha']))
    check('preparation rejects an occupied cross-scope path',(early.get('error') or {}).get('code')=='target_occupied')
    check('matching SHA never authorises extraction over another scope file',(rejected.get('error') or {}).get('code')=='target_occupied' and _read('occupied_other_scope')==original_occupant)
    root=_j(db,name='identity_legacy_root',description='Single fact',type='user',body='Water on Friday.',project_id=scope)
    anchor_request=call('prepare_revision',predecessor='identity_legacy_root',name='identity_legacy_anchor',description='Tracked fact',type='user',body='Water on Friday.',project_id=scope,topic='legacy-identity',facet='water',status='proposed',valid_from='2026-01-01',change_reason='Explicit whole-record confirmation',evidence=['Fixture owner confirmed'],replace_whole_record=True)
    legacy_anchor=_j(db,**anchor_request['save_arguments'])
    legacy_args=dict(args,name='identity_legacy_pending',identity_reference=dict(name='identity_legacy_anchor',sha=legacy_anchor['sha'],reason='Reviewed as the same independently changing fact'))
    legacy_pending=call('prepare_fact',**legacy_args)['save_arguments']
    descendant_request=call('prepare_revision',predecessor='identity_legacy_anchor',name='identity_legacy_descendant',description='Later tracked fact',type='user',body='Water on Friday.',project_id=scope,status='proposed',valid_from='2026-02-01',change_reason='Reviewed continuation',evidence=['Fixture owner confirmed'])
    descendant=_j(db,**descendant_request['save_arguments'])
    descendant_args=dict(legacy_args,name='identity_descendant_pending',identity_reference=dict(name='identity_legacy_descendant',sha=descendant['sha'],reason='Reviewed continuation of the same fact'))
    descendant_pending=call('prepare_fact',**descendant_args)['save_arguments']
    # Refresh the direct-anchor preparation after adding the descendant to the group.
    legacy_pending=call('prepare_fact',**legacy_args)['save_arguments']
    with open(os.path.join(_MEM,'identity_legacy_root.md'),'a',encoding='utf-8') as f:f.write('\nChanged legacy source.\n')
    before_index=call('review_identity',names=['identity_legacy_anchor','identity_legacy_descendant'],project_id=scope)
    check('unindexed ancestor changes prevent identity reuse',all(not x['reusable'] for x in before_index['items']))
    check('unindexed ancestor changes prevent preparation',not call('prepare_fact',**descendant_args).get('ok'))
    legacy_wire=dict(descendant_pending)
    legacy_wire.pop('identity_reference')
    result=_j(db,**legacy_wire)
    check('2.5.0 unsaved reference receives the save guard',(result.get('error') or {}).get('code')=='invalid_identity' and not os.path.exists(os.path.join(_MEM,legacy_wire['name']+'.md')))
    leaked=os.path.join(_MEM,legacy_wire['name']+'.md')
    if os.path.exists(leaked):os.remove(leaked)
    store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    check('changed legacy ancestor invalidates the selected identity',call('prepare_fact',**legacy_args)['error']['code']=='invalid_identity')
    result=_j(db,**legacy_pending)
    check('save rejects invalidated legacy identity links',(result.get('error') or {}).get('code')=='invalid_identity' and not os.path.exists(os.path.join(_MEM,legacy_pending['name']+'.md')),str(result.get('result')))
    # Keep the two legacy controls independent even when testing a defective writer.
    leaked=os.path.join(_MEM,legacy_pending['name']+'.md')
    if os.path.exists(leaked):
        os.remove(leaked)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    check('broken ancestor invalidates descendant selection',call('prepare_fact',**descendant_args)['error']['code']=='invalid_identity')
    result=_j(db,**descendant_pending)
    check('save rejects invalidated ancestor links through descendants',(result.get('error') or {}).get('code')=='invalid_identity' and not os.path.exists(os.path.join(_MEM,descendant_pending['name']+'.md')),str(result.get('result')))
    check('guard is advertised for consumers',capabilities.tool_flags(server.TOOLS)['remember']['identity_reference_guard'])
    old_success=prep(name='identity_legacy_success')['save_arguments']
    old_success.pop('identity_reference')
    missing_guard=dict(old_success)
    missing_guard.pop('expected_fact_sha')
    check('legacy reference requires its group snapshot',(_j(db,**missing_guard).get('error') or {}).get('code')=='invalid_argument')
    leaked=os.path.join(_MEM,old_success['name']+'.md')
    if os.path.exists(leaked):
        os.remove(leaked)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    quoted=dict(old_success)
    quoted['extracted_from']=dict(old_success['extracted_from'],name='Identity selected: watering notes')
    src=quoted['extracted_from']
    quoted['lifecycle']=dict(old_success['lifecycle'],evidence=["%s@%s [%s:%s]: %s" %
        (src['name'],src['sha'],span['start'],span['end'],span['text']) for span in src['spans']])
    check('ordinary source quotation cannot authorise an existing identity',(rc.check_identity(db,quoted).get('error') or {}).get('code')=='identity_reference_required')
    check('valid 2.5.0 reference can still save',_j(db,**old_success).get('result')=='new')
    # A second SQLite connection moves the anchor after its initial validation.
    # The planner must not compute an empty/other group guard from newer index rows.
    real_identity=rc.identity_record
    moved=[False]
    def moving_identity(*a,**kw):
        result=real_identity(*a,**kw)
        if result[0] and not moved[0]:
            moved[0]=True
            path=os.path.join(_MEM,'identity_anchor.md')
            with open(path,'w',encoding='utf-8') as f:f.write(original_anchor.replace('project_id: '+scope,'project_id: identity-moved'))
            other=sqlite3.connect(store.db_path())
            try:
                other.execute('UPDATE docs SET project_id=?,sha=? WHERE name=?',('identity-moved',rc.file_sha(path),'identity_anchor'))
                other.commit()
            finally:other.close()
        return result
    rc.identity_record=moving_identity
    try:
        raced=prep()
        check('identity movement during preparation cannot bypass the guard',not raced.get('ok') and raced['error']['code'] in ('index_pending','identity_unavailable','identity_changed'))
    finally:
        rc.identity_record=real_identity
        with open(os.path.join(_MEM,'identity_anchor.md'),'w',encoding='utf-8') as f:f.write(original_anchor)
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False)
    pending=prep()['save_arguments']
    with open(os.path.join(_MEM,'identity_anchor.md'),'a',encoding='utf-8') as f:f.write('\nChanged identity source.\n')
    check('stale identity refused during preparation',prep()['error']['code']=='identity_changed')
    check('changed identity between preparation and saving is refused',_j(db,**pending)['error']['code']=='fact_changed' and not os.path.exists(os.path.join(_MEM,pending['name']+'.md')))
    check('old saved proposal still retries after identity changes',_j(db,**payload).get('result')=='noop')
    check('successful 2.5.0 reference remains noop after changes',_j(db,**old_success).get('result')=='noop')
    flags=capabilities.tool_flags(server.TOOLS)['remember']
    removed=capabilities.tool_flags([t for t in server.TOOLS if t['name']!='review_identity'])['remember']
    check('identity capability follows declaration',flags['review_identity'] and flags['identity_reference'] and not removed['review_identity'])
    print('Identity fixture: 5 translated identifier groups vs 1 explicitly reused; 0 homonym/facet/scope merges. Not an answer-quality benchmark.')


def unreadable_fact_checks(db):
    from pathlib import Path
    from unittest.mock import patch
    import unicodedata
    scope='unreadable-fact'
    source=_j(db,name='unreadable_source',description='Source',type='user',body='\ud55c\uae00\U0001f642 evidence.',project_id=scope)
    for name,topic in [('unreadable_anchor','caf\u00e9'),('unreadable_sibling',unicodedata.normalize('NFD','caf\u00e9'))]:
        result=_j(db,name=name,description='Reviewed identity',type='user',body='Water on Friday.',project_id=scope,
                  lifecycle=dict(topic=topic,facet='water',status='proposed'),embed=False)
        assert result.get('stored'),result
        if name=='unreadable_anchor':anchor=result
    args=dict(source_name='unreadable_source',source_sha=source['sha'],quotes=['evidence.'],description='Proposed fact',type='user',body='A reviewed proposal.',project_id=scope,
              identity_reference=dict(name='unreadable_anchor',sha=anchor['sha'],reason='Reviewed as the same fact'))
    sibling=Path(_MEM)/'unreadable_sibling.md';original=sibling.read_bytes()
    for case in ('invalid_utf8','permission','missing'):
        request=dict(args,name='unreadable_pending_'+case)
        prepared=json.loads(server.call_tool(db,'prepare_fact',request))
        assert prepared.get('ok'),prepared
        payload=prepared['save_arguments']
        if case=='invalid_utf8':sibling.write_bytes(b'\xff\xfeunreadable')
        if case=='missing':sibling.unlink()
        before={p.name:p.read_bytes() for p in Path(_MEM).glob('*.md')}
        dump='\n'.join(db.iterdump());db.execute('PRAGMA query_only=ON')
        original_open=open
        def guarded_open(path,*a,**kw):
            if case=='permission' and os.fspath(path)==str(sibling):raise PermissionError('fixture unreadable source')
            return original_open(path,*a,**kw)
        try:
            with patch('builtins.open',side_effect=guarded_open),patch.object(store,'reindex',return_value={}),patch.object(rc,'atomic_write',side_effect=AssertionError('refusal must not publish a file')) as publish:
                refused=json.loads(server.call_tool(db,'prepare_fact',request))
                assert refused.get('error',{}).get('code')=='index_pending',(case,refused)
                saved=_j(db,**payload)
                assert saved.get('error',{}).get('code')=='fact_changed',(case,saved)
                assert not publish.called
            assert '\n'.join(db.iterdump())==dump
            assert {p.name:p.read_bytes() for p in Path(_MEM).glob('*.md')}==before
        finally:
            db.execute('PRAGMA query_only=OFF');sibling.write_bytes(original)
        recovered=json.loads(server.call_tool(db,'prepare_fact',request))
        assert recovered.get('ok') and recovered['expected_fact_sha']==prepared['expected_fact_sha'],recovered
        saved=_j(db,**payload)
        assert saved.get('result')=='new' and _j(db,**payload).get('result')=='noop',saved
    print('PASS: invalid UTF8, unreadable and missing same-identity sources refuse preparation/save without DB or memory writes; restored sources prepare/save/retry normally')


def source_failure_checks(db):
    """Use the real indexer: failed reads must never erase an indexed fact or its usage."""
    from pathlib import Path
    from unittest.mock import patch
    import contextlib
    scope='source-failure'
    source=_j(db,name='failure_source',description='Source',type='user',body='Friday evidence.',project_id=scope)
    for name in ('failure_anchor','failure_sibling'):
        result=_j(db,name=name,description='Identity',type='user',body=name+' Friday.',project_id=scope,
                  lifecycle=dict(topic='water',facet='day',status='proposed'),embed=False)
        assert result.get('stored'),result
        if name=='failure_anchor':anchor=result
    args=dict(source_name='failure_source',source_sha=source['sha'],quotes=['evidence.'],
              name='failure_pending',description='Proposal',type='user',body='Friday proposal.',project_id=scope,
              identity_reference=dict(name='failure_anchor',sha=anchor['sha'],reason='Same reviewed fact'))
    sibling=Path(_MEM)/'failure_sibling.md';original=sibling.read_bytes()
    def refresh(**kw):
        return store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False,**kw)
    nested=Path(_MEM)/'failure_nested';nested.mkdir()
    sibling=sibling.replace(nested/sibling.name);refresh()
    with db:db.execute('INSERT INTO usage(path,hits) VALUES(?,7)',(str(sibling),))
    for case in ('permission','invalid_utf8','stat','scan','child_scan','entry_type','entry_link'):
        prepared=json.loads(server.call_tool(db,'prepare_fact',args));assert prepared.get('ok'),prepared
        # Force the incremental pass to read the sibling; fast-skip is separately covered by full=True.
        if case=='invalid_utf8':sibling.write_bytes(b'\xff\xfeunreadable')
        else:os.utime(sibling,(sibling.stat().st_atime,sibling.stat().st_mtime+5))
        dump='\n'.join(db.iterdump())
        original_open,original_stat,original_scan=open,os.stat,os.scandir
        def guarded_open(path,*a,**kw):
            if case=='permission' and os.fspath(path)==str(sibling):raise PermissionError('fixture source denied')
            return original_open(path,*a,**kw)
        def guarded_stat(path,*a,**kw):
            if case=='stat' and os.fspath(path)==str(sibling):raise PermissionError('fixture stat denied')
            return original_stat(path,*a,**kw)
        class GuardedEntry:
            def __init__(self,entry):self.entry=entry
            def __getattr__(self,name):return getattr(self.entry,name)
            def is_dir(self,*a,**kw):
                if case=='entry_type' and self.entry.path==str(nested):raise PermissionError('fixture entry type denied')
                return self.entry.is_dir(*a,**kw)
            def is_symlink(self):
                if case=='entry_link' and self.entry.path==str(nested):raise PermissionError('fixture link type denied')
                return self.entry.is_symlink()
        class typed_scan:
            def __init__(self,path,*a,**kw):self.entries=original_scan(path,*a,**kw)
            def __iter__(self):return self
            def __next__(self):return GuardedEntry(next(self.entries))
            def __enter__(self):return self
            def __exit__(self,*unused):self.entries.close()
            def close(self):self.entries.close()
        def guarded_scan(path,*a,**kw):
            if case=='scan' and os.fspath(path)==_MEM:raise PermissionError('fixture directory denied')
            if case=='child_scan' and os.fspath(path)==str(nested):raise PermissionError('fixture child scan denied')
            return typed_scan(path,*a,**kw) if case in ('entry_type','entry_link') else original_scan(path,*a,**kw)
        published=[];original_write=rc.atomic_write
        def counted_write(path,*a,**kw):
            if os.fspath(path)==str(Path(_MEM)/'failure_pending.md'):published.append(path)
            return original_write(path,*a,**kw)
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch('builtins.open',side_effect=guarded_open))
                stack.enter_context(patch('os.stat',side_effect=guarded_stat))
                stack.enter_context(patch('os.scandir',side_effect=guarded_scan))
                stack.enter_context(patch.object(rc,'atomic_write',side_effect=counted_write))
                for full in (False,True):
                    try:refresh(full=full)
                    except OSError:pass
                    else:raise AssertionError((case,'incomplete scan was accepted',full))
                    assert '\n'.join(db.iterdump())==dump,(case,'index/history changed')
                saved=_j(db,**prepared['save_arguments'])
                assert saved.get('error',{}).get('code')=='index_pending',(case,saved)
                assert not published and not (Path(_MEM)/'failure_pending.md').exists()
                assert '\n'.join(db.iterdump())==dump,(case,'pre-save index changed')
                if case in ('permission','invalid_utf8'):
                    again=json.loads(server.call_tool(db,'prepare_fact',args))
                    assert again.get('error',{}).get('code')=='index_pending',(case,again)
        finally:sibling.write_bytes(original)
        refresh()
        assert db.execute('SELECT hits FROM usage WHERE path=?',(str(sibling),)).fetchone()[0]==7
        recovered=json.loads(server.call_tool(db,'prepare_fact',args))
        assert recovered.get('ok') and recovered['expected_fact_sha']==prepared['expected_fact_sha'],recovered
    saved=_j(db,**recovered['save_arguments'])
    assert saved.get('result')=='new' and _j(db,**recovered['save_arguments']).get('result')=='noop',saved
    # Confirmed deletion still removes both the doc and its associated usage.
    sibling.unlink();refresh()
    assert not db.execute('SELECT 1 FROM docs WHERE path=?',(str(sibling),)).fetchone()
    assert not db.execute('SELECT 1 FROM usage WHERE path=?',(str(sibling),)).fetchone()
    print('PASS: real incremental/full/pre-save indexing preserves docs and usage=7 on read/decode/stat/scan/entry-type errors; recovery and true deletion work')

    legacy=_j(db,name='unreadable_legacy',description='Legacy',type='user',body='Monday water.',project_id=scope)
    request=dict(predecessor='unreadable_legacy',name='unreadable_legacy_new',description='Friday',type='user',
                 body='Friday water.',project_id=scope,topic='legacy-water',facet='day',status='active',
                 valid_from='2026-11-01',change_reason='User correction',evidence=['User confirmed'],replace_whole_record=True)
    path=Path(_MEM)/'unreadable_legacy.md';original=path.read_bytes()
    for case in ('permission','invalid_utf8','missing'):
        prepared=json.loads(server.call_tool(db,'prepare_revision',request));assert prepared.get('ok'),prepared
        if case=='invalid_utf8':path.write_bytes(b'\xff\xfeunreadable')
        if case=='missing':path.unlink()
        original_open=open
        def legacy_open(candidate,*a,**kw):
            if case=='permission' and os.fspath(candidate)==str(path):raise PermissionError('fixture legacy denied')
            return original_open(candidate,*a,**kw)
        dump='\n'.join(db.iterdump())
        try:
            with patch('builtins.open',side_effect=legacy_open):
                refused=json.loads(server.call_tool(db,'prepare_revision',request))
                assert refused.get('error',{}).get('code')=='index_pending',(case,refused)
                saved=_j(db,**prepared['save_arguments'])
                assert saved.get('error',{}).get('code')=='invalid_argument',(case,saved)
                assert not (Path(_MEM)/request['name']).with_suffix('.md').exists()
                assert '\n'.join(db.iterdump())==dump
        finally:path.write_bytes(original)
    saved=_j(db,**prepared['save_arguments'])
    assert saved.get('result')=='new' and _j(db,**prepared['save_arguments']).get('result')=='noop',saved
    print('PASS: unreadable/malformed/missing legacy predecessors return structured preparation/save refusals and recover')


def staged_index_checks(db):
    from pathlib import Path
    from unittest.mock import patch
    from brain import revisions
    import sqlite3
    scope='staged-index'
    source=_j(db,name='staged_source',description='Source',type='user',body='Friday evidence.',project_id=scope)
    anchor=_j(db,name='staged_anchor',description='Identity',type='user',body='Monday water.',project_id=scope,
              lifecycle=dict(topic='water',facet='day',status='proposed'),embed=False)
    sibling=_j(db,name='staged_sibling',description='Sibling',type='user',body='Related Friday.',project_id=scope,
               lifecycle=dict(topic='water',facet='day',status='proposed'),embed=False)
    nested=Path(_MEM)/'zz_staged';nested.mkdir()
    (Path(_MEM)/'staged_sibling.md').replace(nested/'staged_sibling.md')
    def refresh(**kw):
        return store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False,**kw)
    refresh()
    path=Path(_MEM)/'staged_anchor.md';original=path.read_bytes()
    with db:db.execute('INSERT INTO usage(path,hits) VALUES(?,7)',(str(path),))
    request=dict(source_name='staged_source',source_sha=source['sha'],quotes=['evidence.'],name='staged_pending',
                 description='Proposal',type='user',body='Friday proposal.',project_id=scope,
                 identity_reference=dict(name='staged_anchor',sha=anchor['sha'],reason='Same reviewed fact'))
    prepared=json.loads(server.call_tool(db,'prepare_fact',request));assert prepared.get('ok'),prepared
    def archives():return {p.name:p.read_bytes() for p in revisions.directory().glob('*.json')}
    before=archives();dump='\n'.join(db.iterdump())
    path.write_bytes(original+b'\nAn external edit observed before the failing directory.\n')
    original_scan=os.scandir
    def denied(candidate,*a,**kw):
        if os.fspath(candidate)==str(nested):raise PermissionError('fixture late scan denied')
        return original_scan(candidate,*a,**kw)
    try:
        with patch('os.scandir',side_effect=denied),patch.object(rc,'atomic_write',wraps=rc.atomic_write) as publish:
            for full in (False,True):
                try:refresh(full=full)
                except OSError:pass
                else:raise AssertionError('late source failure accepted')
                assert '\n'.join(db.iterdump())==dump
                assert archives()==before,'a failed scan changed durable history'
            saved=_j(db,**prepared['save_arguments'])
            assert saved.get('error',{}).get('code')=='index_pending',saved
            assert not publish.called,'a failed scan published history or a proposal'
            assert '\n'.join(db.iterdump())==dump and archives()==before
    finally:path.write_bytes(original)
    refresh()
    assert archives()==before,'failed-scan history survived source restoration'
    assert db.execute('SELECT hits FROM usage WHERE path=?',(str(path),)).fetchone()[0]==7
    # A legitimate edit only becomes history after the complete source observation succeeds.
    path.write_bytes(original+b'\nA complete observation.\n');refresh()
    assert archives()!=before
    observed=archives();refresh();assert archives()==observed
    path.write_bytes(original);refresh()
    recovered=json.loads(server.call_tool(db,'prepare_fact',request));assert recovered.get('ok'),recovered
    saved=_j(db,**recovered['save_arguments'])
    assert saved.get('result')=='new' and _j(db,**recovered['save_arguments']).get('result')=='noop',saved
    print('PASS: late source failure preserves exact SQLite and durable history bytes with zero publications; complete observations and retries succeed')

    # Staging must not apply a plan against a concurrently changed index.
    path.write_bytes(original+b'\nAnother observed edit.\n')
    before=archives();prior_docs=[tuple(r) for r in db.execute('SELECT * FROM docs ORDER BY id')]
    other=sqlite3.connect(store.db_path());real_read=store.read_doc;changed=[]
    def concurrent_read(candidate,*a,**kw):
        doc=real_read(candidate,*a,**kw)
        if candidate==str(path) and not changed:
            with other:other.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('concurrent_fixture','retained')")
            changed.append(True)
        return doc
    try:
        with patch.object(store,'read_doc',side_effect=concurrent_read),patch.object(rc,'atomic_write',wraps=rc.atomic_write) as publish:
            try:refresh()
            except OSError as exc:assert 'Index changed' in str(exc),exc
            else:raise AssertionError('concurrent index change was overwritten')
            assert changed and not publish.called
        assert [tuple(r) for r in db.execute('SELECT * FROM docs ORDER BY id')]==prior_docs
        assert archives()==before and store.get_meta(db,'concurrent_fixture')=='retained'
    finally:other.close();path.write_bytes(original)
    refresh()
    print('PASS: a concurrent index commit refuses the staged plan before history writes and preserves the competing change')



def history_batch_checks(db):
    from pathlib import Path
    from unittest.mock import patch
    from brain import revisions
    import contextlib
    import sqlite3
    scope='history-batch'
    source=_j(db,name='batch_source',description='Source',type='user',body='Friday evidence.',project_id=scope)
    for name in ('batch_edit_a','batch_edit_b'):
        _j(db,name=name,description=name,type='user',body=name+' original body.',project_id=scope)
    paths=[Path(_MEM)/(name+'.md') for name in ('batch_edit_a','batch_edit_b')]
    originals=[p.read_bytes() for p in paths]
    history_paths=[revisions.location(str(p)) for p in paths]
    request=dict(source_name='batch_source',source_sha=source['sha'],quotes=['evidence.'],name='batch_pending',
                 description='Proposal',type='user',body='Friday proposal.',project_id=scope,topic='batch',facet='day')
    prepared=json.loads(server.call_tool(db,'prepare_fact',request));assert prepared.get('ok'),prepared
    target=Path(_MEM)/'batch_pending.md'
    def refresh(connection=db,**kw):
        return store.reindex(connection,recalibrate=False,sync=False,heal=False,queue_embedding=False,**kw)
    def archives():return {p.name:p.read_bytes() for p in revisions.directory().glob('*.json')}
    real_write=rc.atomic_write
    real_index=store._index_one
    class CommitFailure:
        def __getattr__(self,name):return getattr(db,name)
        def __enter__(self):db.__enter__();return self
        def __exit__(self,*args):return db.__exit__(*args)
        def commit(self):raise sqlite3.OperationalError('fixture commit failure')
    for absent in (False,True):
        if absent:history_paths[0].unlink()
        for failure in ('history_before','history_after','sqlite_row','sqlite_terms','sqlite_commit'):
            for mode in ('incremental','full','save'):
                before=archives();dump='\n'.join(db.iterdump())
                for path,body in zip(paths,originals):path.write_bytes(body+b'\nExternal edit pending observation.\n')
                source_bytes=[p.read_bytes() for p in paths];published=[];attempted=[]
                def write(candidate,*args,**kw):
                    if str(candidate)==str(target):published.append(candidate)
                    if str(candidate)==str(history_paths[1]):
                        attempted.append(candidate)
                        if failure=='history_before':raise PermissionError('fixture second history publication denied')
                    result=real_write(candidate,*args,**kw)
                    if str(candidate)==str(history_paths[1]) and failure=='history_after':
                        raise OSError('fixture failure after second history publication')
                    return result
                def index(connection,doc,*args,**kw):
                    result=real_index(connection,doc,*args,**kw)
                    if doc['path']==str(paths[1]) and failure=='sqlite_row':
                        raise sqlite3.OperationalError('fixture database update failure')
                    return result
                connection=CommitFailure() if failure=='sqlite_commit' else db
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(rc,'atomic_write',side_effect=write))
                    stack.enter_context(patch.object(store,'_index_one',side_effect=index))
                    if failure=='sqlite_terms':
                        for method in ('_recount_terms','_rebuild_terms'):
                            stack.enter_context(patch.object(store,method,side_effect=sqlite3.OperationalError('fixture terms failure')))
                    if mode=='save':
                        result=_j(connection,**prepared['save_arguments'])
                        assert result.get('error',{}).get('code')=='index_pending',(failure,mode,result)
                    else:
                        try:refresh(connection,full=mode=='full')
                        except (OSError,sqlite3.Error):pass
                        else:raise AssertionError((failure,mode,'injected failure was accepted'))
                assert attempted and not published and not target.exists(),(failure,mode)
                assert '\n'.join(db.iterdump())==dump,(failure,mode,'SQLite changed')
                assert archives()==before,(failure,mode,'durable history changed')
                assert [p.read_bytes() for p in paths]==source_bytes,(failure,mode,'source changed')
                assert not list(revisions.directory().glob('.index-rollback-*'))
                for path,body in zip(paths,originals):path.write_bytes(body)
                # Full refresh may legitimately create the absent archive; check restoration only when present.
                if not absent:
                    refresh();assert archives()==before,'failed observation survived source restoration'
    for path,body in zip(paths,originals):path.write_bytes(body+b'\nSuccessful observation.\n')
    refresh()
    for history in history_paths:
        assert 'Successful observation.' in history.read_text(encoding='utf-8')
    before=archives();refresh();assert archives()==before
    saved=_j(db,**prepared['save_arguments'])
    assert saved.get('result')=='new' and _j(db,**prepared['save_arguments']).get('result')=='noop',saved
    print('PASS: 30 incremental/full/pre-save history/write/SQLite/commit failures preserve sources, exact DB and all history bytes; new archives roll back; success and retry work')


def content_snapshot_checks(db):
    from pathlib import Path
    from unittest.mock import patch
    from brain import revisions
    scope='content-snapshot'
    source=_j(db,name='content_source',description='Source',type='user',body='Friday evidence.',project_id=scope)
    anchor=_j(db,name='content_anchor',description='Identity',type='user',body='Monday water.',project_id=scope,
              lifecycle=dict(topic='plant',facet='water',status='proposed'),embed=False)
    _j(db,name='content_outsider',description='Other facet',type='user',body='Other observation.',project_id=scope,
       lifecycle=dict(topic='plant',facet='light',status='proposed'),embed=False)
    path=Path(_MEM)/'content_outsider.md';original=path.read_bytes()
    assert original.count(b'light')==1
    changed=original.replace(b'light',b'water')
    assert changed!=original and len(changed)==len(original)
    anchor_path=Path(_MEM)/'content_anchor.md'
    with db:db.execute('INSERT INTO usage(path,hits) VALUES(?,7)',(str(anchor_path),))
    request=dict(source_name='content_source',source_sha=source['sha'],quotes=['evidence.'],name='content_pending',
                 description='Proposal',type='user',body='Friday proposal.',project_id=scope,
                 identity_reference=dict(name='content_anchor',sha=anchor['sha'],reason='Same reviewed fact'))
    prepared=json.loads(server.call_tool(db,'prepare_fact',request));assert prepared.get('ok'),prepared
    target=Path(_MEM)/'content_pending.md'
    original_write=rc.atomic_write
    def refresh():return store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False,verify_content=True)
    for preserve_metadata in (False,True):
        stat=path.stat()
        archive=revisions.location(str(path))
        history=json.loads(archive.read_text(encoding='utf-8'))['observations']
        anchor_history=revisions.location(str(anchor_path)).read_bytes()
        path.write_bytes(changed)
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns if preserve_metadata else stat.st_mtime_ns+1000000000))
        published=[]
        def publication(candidate,*args,**kw):
            if str(candidate)==str(target):published.append(candidate)
            return original_write(candidate,*args,**kw)
        with patch.object(rc,'atomic_write',side_effect=publication):
            saved=_j(db,**prepared['save_arguments'])
        assert saved.get('error',{}).get('code')=='fact_changed',(preserve_metadata,saved)
        assert not published and not target.exists()
        assert path.read_bytes()==changed and rc.file_sha(str(anchor_path))==anchor['sha']
        assert db.execute('SELECT hits FROM usage WHERE path=?',(str(anchor_path),)).fetchone()[0]==7
        assert revisions.location(str(anchor_path)).read_bytes()==anchor_history
        observed=json.loads(archive.read_text(encoding='utf-8'))['observations']
        assert observed[:len(history)]==history,'earlier observed history was lost'
        assert observed[-1]['record']['lifecycle']['facet']=='water'
        path.write_bytes(original);os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns));refresh()
    # Content verification reads unchanged documents but does not reindex/publish them.
    with patch.object(store,'_index_one',wraps=store._index_one) as index,patch.object(rc,'atomic_write',wraps=rc.atomic_write) as write:
        refresh();assert not index.called and not write.called
    saved=_j(db,**prepared['save_arguments'])
    assert saved.get('result')=='new' and _j(db,**prepared['save_arguments']).get('result')=='noop',saved
    print('PASS: pre-save content verification rejects same-size/same-mtime group entrants; metadata-change control, source/history/usage preservation and restored-source new/noop pass')


def feedback_scope_checks(db):
    from pathlib import Path
    from unittest.mock import patch
    from brain import health
    scope='feedback-scope-a'
    source=_j(db,name='feedback_source',description='Source',type='user',body='Friday evidence.',project_id=scope)
    anchor=_j(db,name='feedback_anchor',description='Identity',type='user',body='Feedback probe.',project_id=scope,
              lifecycle=dict(topic='plant',facet='water',status='proposed'),embed=False)
    path=Path(_MEM)/'feedback_anchor.md';original=path.read_bytes()
    health.feedback(db,'feedback_anchor','wrong')
    with db:db.execute('UPDATE usage SET hits=7 WHERE path=?',(str(path),))
    original_usage=tuple(db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone())
    weight=db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]
    assert weight==0.5
    request=dict(source_name='feedback_source',source_sha=source['sha'],quotes=['evidence.'],name='feedback_pending',
                 description='Proposal',type='user',body='Friday proposal.',project_id=scope,
                 identity_reference=dict(name='feedback_anchor',sha=anchor['sha'],reason='Same reviewed fact'))
    prepared=json.loads(server.call_tool(db,'prepare_fact',request));assert prepared.get('ok'),prepared
    copy=Path(_MEM)/'00_feedback_copy.md';target=Path(_MEM)/'feedback_pending.md'
    def refresh():return store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False,verify_content=True)
    real_write=rc.atomic_write
    for change in ('scope','fact','both'):
        copy.write_bytes(original)
        published=[]
        def publication(candidate,*args,**kw):
            if str(candidate)==str(target):published.append(candidate)
            return real_write(candidate,*args,**kw)
        with patch.object(rc,'atomic_write',side_effect=publication):saved=_j(db,**prepared['save_arguments'])
        assert saved.get('error',{}).get('code')=='fact_changed',saved
        assert not published and not target.exists()
        changed=original.replace(b'feedback_anchor',('feedback_other_'+change).encode('ascii'))
        if change in ('scope','both'):changed=changed.replace(b'feedback-scope-a',b'feedback-scope-b')
        if change in ('fact','both'):changed=changed.replace(b'water',b'light')
        path.write_bytes(changed);refresh()
        row=db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()
        assert row and row[0]==1.0,(change,'foreign feedback weight applied')
        assert not db.execute('SELECT 1 FROM usage WHERE path=?',(str(path),)).fetchone(),(change,'foreign usage applied')
        indexed_name=db.execute('SELECT name FROM docs WHERE path=?',(str(path),)).fetchone()[0]
        new_feedback=health.feedback(db,indexed_name,'useful')
        assert 'error' not in new_feedback,new_feedback
        new_usage=tuple(db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone())
        copy.unlink();path.write_bytes(original);refresh()
        assert tuple(db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone())==original_usage
        assert db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]==weight
        # Direct identity changes (without deduplication) also keep both ledgers separate.
        path.write_bytes(changed);refresh()
        assert tuple(db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone())==new_usage
        assert abs(db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]-new_feedback['weight'])<1e-9
        path.write_bytes(original);refresh()
        assert tuple(db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone())==original_usage
        assert db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]==weight
    saved=_j(db,**prepared['save_arguments'])
    assert saved.get('result')=='new' and _j(db,**prepared['save_arguments']).get('result')=='noop',saved
    assert path.read_bytes()==original
    print('PASS: scope-only, fact-only and combined path reuse never inherit old feedback; both identities recover their own counters/weights; direct switches and new/noop pass')
    for old_weight in (True,False):
        name='legacy_feedback_'+str(old_weight)
        _j(db,name=name,description='Legacy',type='user',body='Legacy observation.',project_id='legacy-a',
           lifecycle=dict(topic=name,facet='water',status='proposed'),embed=False)
        old_path=Path(_MEM)/(name+'.md');old_bytes=old_path.read_bytes()
        health.feedback(db,name,'wrong')
        with db:db.execute('UPDATE usage SET hits=7 WHERE path=?',(str(old_path),))
        old_copy=Path(_MEM)/('00_'+name+'.md');old_copy.write_bytes(old_bytes);refresh()
        # Simulate the prior path-only formats, which had no origin identity.
        key='retained_doc_weight:'+str(old_path)
        with db:
            if old_weight:db.execute('UPDATE meta SET v=? WHERE k=?',('0.5',key))
            else:db.execute('DELETE FROM meta WHERE k=?',(key,))
        old_path.write_bytes(old_bytes.replace(b'legacy-a',b'legacy-b'));refresh()
        assert db.execute('SELECT weight FROM docs WHERE path=?',(str(old_path),)).fetchone()[0]==1.0
        assert not db.execute('SELECT 1 FROM usage WHERE path=?',(str(old_path),)).fetchone()
        archived=json.loads(store.get_meta(db,key))['records']
        assert archived[0]['identity'] is None and archived[0]['usage']['hits']==7
        old_path.unlink();old_copy.unlink();refresh()
        assert store.get_meta(db,key)==''
    print('PASS: legacy path-only weights/counters are retained unattributed, never guessed into a new scope; confirmed deletion clears them')


def unicode_feedback_checks(db):
    from pathlib import Path
    import unicodedata
    from brain import health
    name='unicode_feedback'
    topic,facet='caf\u00e9','temp\u00e9rature'
    _j(db,name=name,description='Unicode identity',type='user',body='Preserve feedback.',project_id='unicode-a',
       lifecycle=dict(topic=topic,facet=facet,status='proposed'),embed=False)
    path=Path(_MEM)/(name+'.md');original=path.read_text(encoding='utf-8')
    key='retained_doc_weight:'+str(path)
    copy=Path(_MEM)/'00_unicode_feedback.md'
    def refresh(**kw):
        return store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False,verify_content=True,**kw)
    def state():
        row=db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone()
        return (db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0],dict(row) if row else None)
    health.feedback(db,name,'wrong')
    with db:db.execute('UPDATE usage SET hits=7,opens=3,useful=2,stale=1,last_hit=? WHERE path=?',('2026-01-01',str(path)))
    initial=state();assert initial[0]==0.5
    variants=[original.replace(value,unicodedata.normalize('NFD',value)) for value in (topic,facet)]
    variants.append(unicodedata.normalize('NFD',original))
    for text in variants:
        path.write_text(text,encoding='utf-8');refresh()
        assert state()==initial,'Unicode-only change reset active feedback'
        copy.write_text(text,encoding='utf-8');refresh()
        assert not db.execute('SELECT 1 FROM docs WHERE path=?',(str(path),)).fetchone()
        path.write_text(original,encoding='utf-8');refresh()
        assert state()==initial,'Unicode-only change lost retained feedback'
        copy.unlink();refresh(full=True);assert state()==initial
    for changed in (original.replace('unicode-a','unicode-b'),original.replace(topic,'Cafe'),original.replace(facet,'humidity')):
        path.write_text(changed,encoding='utf-8');refresh();assert state()==(1.0,None)
        path.write_text(original,encoding='utf-8');refresh();assert state()==initial
    print('PASS: NFC/NFD topics and facets preserve every feedback field across direct changes, deduplication and full rebuild; scope/case/different-facet controls remain separate')

    # Reproduce both possible V2 retention orders. Its snapshots can overlap;
    # their array order, not last_hit (which only dates hits), records recency.
    raw_nfc=['unicode-a','fact',topic,facet]
    raw_nfd=['unicode-a','fact',unicodedata.normalize('NFD',topic),unicodedata.normalize('NFD',facet)]
    a=dict(identity=raw_nfc,weight=initial[0],usage=initial[1])
    b=dict(identity=raw_nfd,weight=0.7,usage=dict(initial[1],hits=9,useful=4,last_hit='2025-01-01'))
    foreign=dict(identity=['unicode-b','fact',topic,facet],weight=0.2,usage=None)
    for snapshots in ([a,b],[b,a],[a,dict(a,identity=raw_nfd)]):
        copy.write_text(original,encoding='utf-8');refresh()
        with db:store.set_meta(db,key,json.dumps(dict(version=2,records=snapshots+[foreign])),commit=False)
        copy.unlink();path.write_text(variants[-1],encoding='utf-8');refresh()
        assert state()==(snapshots[-1]['weight'],snapshots[-1]['usage']),'V2 latest snapshot was not restored'
        saved=json.loads(store.get_meta(db,key));assert saved['version']==3
        canonical=next(r for r in saved['records'] if r['identity']==raw_nfc)
        assert canonical['legacy_snapshots']==snapshots,'V2 originals were dropped or rewritten'
        assert len(saved['records'])==2
        # Feedback received after migration takes precedence over the snapshots,
        # whose immutable provenance survives re-retention and repeated restores.
        health.feedback(db,name,'useful');updated=state()
        for text in (original,variants[-1],original):
            path.write_text(text,encoding='utf-8');refresh();assert state()==updated
            copy.write_text(text,encoding='utf-8');refresh();copy.unlink();refresh(full=True)
            assert state()==updated,'Repeated migration duplicated or reverted feedback'
            saved=json.loads(store.get_meta(db,key))
            assert next(r for r in saved['records'] if r['identity']==raw_nfc)['legacy_snapshots']==snapshots
        path.write_text(original.replace('unicode-a','unicode-b'),encoding='utf-8');refresh()
        assert state()==(0.2,None)
        path.write_text(original,encoding='utf-8');refresh();assert state()==updated
    # An existing indexed row is newer than archived V2 snapshots. Preserve its
    # active counters while migrating the older snapshots on the next retention.
    with db:store.set_meta(db,key,json.dumps(dict(version=2,records=[a,b])),commit=False)
    active=state();path.write_text(variants[-1],encoding='utf-8');refresh();assert state()==active
    path.write_text(original.replace('unicode-a','unicode-b'),encoding='utf-8');refresh()
    path.write_text(original,encoding='utf-8');refresh();assert state()==active
    assert json.loads(store.get_meta(db,key))['records'][0]['legacy_snapshots']==[a,b]
    path.unlink();refresh();assert store.get_meta(db,key)==''
    print('PASS: V2 split histories migrate losslessly into canonical groups; latest retained/active snapshots win without summing, originals survive later feedback and retries, true deletion clears them')


def recorded_feedback_checks(db):
    from pathlib import Path
    from unittest.mock import patch
    import sqlite3
    import unicodedata
    from brain import health, search, observe
    name='recorded_feedback'
    for i in range(3):
        _j(db,name='recorded_background_'+str(i),description='Background',type='reference',body='Unrelated rainfall.',embed=False)
    _j(db,name=name,description='Usage identity',type='user',body='Orchid watering evidence.',project_id='record-a',
       lifecycle=dict(topic='caf\u00e9',facet='water',status='active',valid_from='2026-01-01',evidence=['Fixture']),embed=False)
    path=Path(_MEM)/(name+'.md');original=path.read_text(encoding='utf-8')
    health.feedback(db,name,'wrong')
    with db:db.execute('UPDATE usage SET hits=7 WHERE path=?',(str(path),))
    def refresh():
        store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False,verify_content=True)
    def usage():return dict(db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone())
    def results():
        return [r for r in search.recall(db,'orchid',scope='record-a',graph=False,log=False,bridge=False) if r['path']==str(path)]
    for label,text,same in [('scope',original.replace('record-a','record-b'),False),
                            ('topic',original.replace('caf\u00e9','another-topic'),False),
                            ('facet',original.replace('water','light'),False),
                            ('unicode',unicodedata.normalize('NFD',original),True),
                            ('unchanged',original,True)]:
        before=usage();rows=results();assert len(rows)==1
        recalls=db.execute('SELECT count(*) FROM recalls').fetchone()[0]
        path.write_text(text,encoding='utf-8');refresh()
        search.record(db,'orchid',[],rows)
        assert db.execute('SELECT count(*) FROM recalls').fetchone()[0]==recalls+1
        if same:
            after=usage();assert after['hits']==before['hits']+1,(label,'same fact was not recorded')
            assert all(after[k]==v for k,v in before.items() if k not in ('hits','last_hit'))
        else:
            assert not db.execute('SELECT 1 FROM usage WHERE path=?',(str(path),)).fetchone(),(label,'stale hit credited to new fact')
        path.write_text(original,encoding='utf-8');refresh()
        assert usage()==(after if same else before),(label,'original feedback changed')
        assert db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]==0.5
    rows=results();before=usage()
    # Legacy/partial synthetic results cannot prove the identity that was shown.
    unpinned=dict(rows[0]);unpinned.pop('_feedback_identity',None)
    search.record(db,'orchid',[],[unpinned]);assert usage()==before
    # A competing writer cannot replace the identity between comparison and hit
    # insertion, including on a connection configured for autocommit.
    other=sqlite3.connect(store.db_path(),timeout=0)
    attempted=[]
    def compete(sql):
        if sql.startswith('SELECT path,name,project_id,lifecycle FROM docs WHERE path='):
            try:other.execute('UPDATE docs SET project_id=? WHERE path=?',('record-b',str(path)))
            except sqlite3.OperationalError as exc:attempted.append('locked' in str(exc))
            else:other.commit();attempted.append(False)
    isolation=db.isolation_level
    try:
        db.isolation_level=None;db.set_trace_callback(compete)
        search.record(db,'orchid',[],rows)
    finally:
        db.set_trace_callback(None);db.isolation_level=isolation;other.close()
    assert attempted==[True] and usage()['hits']==before['hits']+1,attempted
    # Exercise the public MCP boundary: a late reindex cannot relabel old result
    # text with the new scope/SHA, and private recording metadata stays internal.
    before=usage();sha=rc.file_sha(str(path));record=search.record
    def move(db,query,terms,rows):
        path.write_text(original.replace('record-a','record-b'),encoding='utf-8');refresh()
        return record(db,query,terms,rows)
    with patch.object(search,'record',side_effect=move),patch.object(server,'_words_unsure',return_value=False),patch.object(observe,'write'):
        response=json.loads(server.call_tool(db,'recall',dict(query='orchid',scope='record-a',semantic_k=0)))
    shown=next(r for r in response['results'] if r['path']==str(path))
    assert shown['project_id']=='record-a' and shown['sha']==sha and '_feedback_identity' not in shown,shown
    assert not db.execute('SELECT 1 FROM usage WHERE path=?',(str(path),)).fetchone()
    path.write_text(original,encoding='utf-8');refresh();assert usage()==before
    other=sqlite3.connect(store.db_path(),timeout=0);attempted=[]
    class RacingDelta(dict):
        def __getitem__(self,key):
            try:other.execute('UPDATE docs SET project_id=?,weight=1 WHERE path=?',('record-b',str(path)))
            except sqlite3.OperationalError as exc:attempted.append('locked' in str(exc))
            else:other.commit();attempted.append(False)
            return super().__getitem__(key)
    try:
        with patch.object(health,'_W_DELTA',RacingDelta(health._W_DELTA)):
            feedback=health.feedback(db,name,'useful')
    finally:other.close()
    assert attempted==[True],'explicit feedback target changed between lookup and update'
    assert db.execute('SELECT project_id FROM docs WHERE path=?',(str(path),)).fetchone()[0]=='record-a'
    assert usage()['useful']==before['useful']+1 and abs(feedback['weight']-0.65)<1e-9
    print('PASS: late scope/topic/facet changes never inherit stale hits; NFC/NFD and unchanged facts record once; original feedback survives, writer lock spans comparison/increment, MCP keeps selected scope/SHA')
    print('PASS: explicit feedback locks its target before lookup and preserves the selected scope through weight/counter updates')


def duplicate_usage_checks(db):
    from pathlib import Path
    from unittest.mock import patch
    from brain import health
    def refresh(**kw):
        return store.reindex(db,recalibrate=False,sync=False,heal=False,queue_embedding=False,**kw)
    anchor=_j(db,name='zz_duplicate_anchor',description='Identity',type='user',body='Keep original path usage.',project_id='duplicate-usage',
              lifecycle=dict(topic='water',facet='day',status='proposed'),embed=False)
    path=Path(_MEM)/'zz_duplicate_anchor.md';original=path.read_bytes()
    with db:db.execute('INSERT INTO usage(path,hits,opens,useful,wrong,stale) VALUES(?,7,3,2,1,1)',(str(path),))
    for signal in ('wrong','wrong','useful'):
        feedback=health.feedback(db,'zz_duplicate_anchor',signal)
    assert feedback['weight']==0.35,feedback
    usage=tuple(db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone())
    source=_j(db,name='duplicate_source',description='Source',type='user',body='Friday evidence.',project_id='duplicate-usage')
    request=dict(source_name='duplicate_source',source_sha=source['sha'],project_id='duplicate-usage',
                 quotes=['evidence.'],name='duplicate_pending',description='Proposal',type='user',body='Friday proposal.',
                 identity_reference=dict(name='zz_duplicate_anchor',sha=anchor['sha'],reason='Same reviewed fact'))
    prepared=json.loads(server.call_tool(db,'prepare_fact',request));assert prepared.get('ok'),prepared
    copy=Path(_MEM)/'00_duplicate_copy.md';copy.write_bytes(original)
    target=Path(_MEM)/'duplicate_pending.md';original_write=rc.atomic_write;published=[]
    def publication(candidate,*a,**kw):
        if os.fspath(candidate)==str(target):published.append(candidate)
        return original_write(candidate,*a,**kw)
    with patch.object(rc,'atomic_write',side_effect=publication):saved=_j(db,**prepared['save_arguments'])
    assert saved.get('error',{}).get('code')=='fact_changed',saved
    assert not published and not target.exists() and path.read_bytes()==original
    assert not db.execute('SELECT 1 FROM docs WHERE path=?',(str(path),)).fetchone()
    retained=db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone()
    assert retained and tuple(retained)==usage,'duplicate exclusion discarded usage'
    refresh(full=True)
    retained=db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone()
    assert retained and tuple(retained)==usage,'duplicate exclusion discarded usage'
    assert db.execute('SELECT count(*) FROM docs WHERE sha=?',(anchor['sha'],)).fetchone()[0]==1
    copy.unlink();refresh()
    assert db.execute('SELECT 1 FROM docs WHERE path=?',(str(path),)).fetchone()
    assert abs(db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]-0.35)<1e-9
    retained=db.execute('SELECT * FROM usage WHERE path=?',(str(path),)).fetchone()
    assert retained and tuple(retained)==usage,'duplicate exclusion discarded usage'
    saved=_j(db,**prepared['save_arguments']);assert saved.get('result')=='new',saved
    assert _j(db,**prepared['save_arguments']).get('result')=='noop'
    assert abs(db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]-0.35)<1e-9
    # A later feedback change must supersede the first retained weight.
    feedback=health.feedback(db,'zz_duplicate_anchor','useful')
    copy.write_bytes(original);refresh();copy.unlink();refresh()
    assert abs(db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]-feedback['weight'])<1e-9
    # Even an already-deduplicated path loses usage only when its source really disappears.
    copy.write_bytes(original);refresh();assert not db.execute('SELECT 1 FROM docs WHERE path=?',(str(path),)).fetchone()
    path.unlink();refresh();assert not db.execute('SELECT 1 FROM usage WHERE path=?',(str(path),)).fetchone()
    copy.unlink();refresh()
    path.write_bytes(original);refresh()
    assert db.execute('SELECT weight FROM docs WHERE path=?',(str(path),)).fetchone()[0]==1.0
    assert not db.execute('SELECT 1 FROM usage WHERE path=?',(str(path),)).fetchone()
    print('PASS: earlier identical copy preserves every usage field and learned weight=0.35 across refusal/full rebuild/recovery/new/noop; later feedback survives; true deletion resets retained usage and weight')


def exclusive_create_checks(db):
    from unittest.mock import patch
    source = _j(db, name='race_source', description='Source', type='user',
                body='The review happens on Friday.', project_id='race-scope')
    prepared = json.loads(server.call_tool(db, 'prepare_fact', dict(
        source_name='race_source', source_sha=source['sha'], quotes=['The review happens on Friday.'],
        name='race_proposal', description='Draft', type='user', body='Friday review.',
        project_id='race-scope', topic='review', facet='day'), _measure=True))['save_arguments']
    target = os.path.join(_MEM, 'race_proposal.md')
    foreign = '---\nname: race_proposal\nproject_id: other-scope\n---\nPreserve the competing record.\n'
    original_write, original_sha = rc.atomic_write, rc.file_sha
    raced = []
    def race_write(path, text, expected_sha):
        def race_sha(candidate):
            value = original_sha(candidate)
            if candidate == target and value is None and not raced:
                with open(target, 'w', encoding='utf-8') as f:
                    f.write(foreign)
                raced.append(True)
            return value
        with patch.object(rc, 'file_sha', side_effect=race_sha):
            return original_write(path, text, expected_sha)
    with patch.object(rc, 'atomic_write', side_effect=race_write):
        outcome = _j(db, **prepared)
    check('destination created after final SHA check returns conflict', bool(raced) and outcome.get('result') == 'conflict')
    check('concurrent foreign memory remains byte-identical', _read('race_proposal') == foreign)
    check('conflicting create cleans temporary files', not any(n.startswith('.brain-write-') for n in os.listdir(_MEM)))
    os.remove(target)
    outcome = _j(db, **prepared)
    text = _read('race_proposal')
    check('uncontended create and identical retry remain new and noop', outcome.get('result') == 'new' and
          _j(db, **prepared).get('result') == 'noop' and _read('race_proposal') == text)


def main() -> int:
    import subprocess
    cold=os.path.join(_TMP,'cold-review')
    requests=[dict(jsonrpc='2.0',id=1,method='initialize'),dict(jsonrpc='2.0',id=2,method='tools/list'),
              dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='review_identity',arguments=dict(names=['missing'])))]
    wire=subprocess.run([sys.executable,'-m','brain.server'],input=''.join(json.dumps(r)+'\n' for r in requests),
                        text=True,encoding='utf-8',errors='replace',capture_output=True,cwd=ROOT,env=dict(os.environ,BRAIN_HOME=cold),timeout=30)
    replies=[json.loads(line) for line in wire.stdout.splitlines() if line.strip()]
    last=replies[-1]['result'] if replies else {}
    check('cold MCP identity review and discovery do not create the index',not os.path.exists(cold) and
          last.get('isError') and json.loads(last['content'][0]['text'])['error']['code']=='index_pending',wire.stderr[-200:])
    db = _build()
    for tool in ('prepare_fact', 'prepare_revision', 'memory_review'):
        for existing in (False, True):
            home = os.environ['BRAIN_HOME'] if existing else os.path.join(_TMP, 'cold-' + tool)
            requests = [dict(jsonrpc='2.0', id=1, method='initialize'),
                        dict(jsonrpc='2.0', id=2, method='tools/list'),
                        dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name=tool, arguments={}))]
            before = db.total_changes
            wire = subprocess.run([sys.executable, '-m', 'brain.server'],
                input=''.join(json.dumps(r)+'\n' for r in requests), text=True, encoding='utf-8',
                capture_output=True, cwd=ROOT, env=dict(os.environ, BRAIN_HOME=home), timeout=30)
            replies = [json.loads(line) for line in wire.stdout.splitlines() if line.strip()]
            result = json.loads(replies[-1]['result']['content'][0]['text'])
            check('cold read-only '+tool+' existing='+str(existing),
                  (result.get('ok') is True or (result.get('error') or {}).get('code') == 'invalid_argument') if existing else
                  not os.path.exists(home) and (result.get('error') or {}).get('code') == 'index_pending', result)
            check('cold read-only call leaves open index unchanged '+tool, db.total_changes == before)
    from unittest.mock import patch
    original_connect = store.connect
    opened = []
    def read_connection(*args, **kwargs):
        opened.append(kwargs.get('readonly', False))
        return original_connect(*args, **kwargs)
    for tool in ('review_identity', 'prepare_fact', 'prepare_revision', 'memory_review'):
        opened.clear()
        request = json.dumps(dict(jsonrpc='2.0', id=1, method='tools/call', params=dict(name=tool, arguments={})))
        with patch.object(server, '_requests', return_value=iter([request])), patch.object(server, '_result'), \
                patch.object(store, 'connect', side_effect=read_connection), patch('brain.hosts.utf8_stdio'):
            server.serve()
        check('first dispatcher call opens only a read-only connection '+tool, opened == [True], opened)
    base = dict(description="harbour ledger rule", type="lesson", body="The ledger closes on friday.")

    print("=" * 72 + "\n① protected defaults\n" + "=" * 72)
    t1 = _j(db, name="default_note", **base)
    t2 = _j(db, name="default_note", description="harbour ledger rule", type="lesson", body="Changed by a person.")
    check("default save is structured; unversioned overwrite is refused",
          t1.get("result") == "new" and t2.get("result") == "conflict" and t1.get("idempotency_key", "").startswith("auto:"))
    check("original content is preserved", "Changed by a person." not in _read("default_note"))
    # A direct human-authored file remains a separate control, without API-generated metadata.
    with open(os.path.join(_MEM, "legacy_note.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: legacy_note\nmetadata:\n  type: lesson\n---\nChanged by a person.\n")

    print("\n" + "=" * 72 + "\n② new\n" + "=" * 72)
    a = _j(db, name="orbit_rule", idempotency_key="k1", project_id="proj-A", source="orbit task t1",
           observed_at="2026-10-01", format="json", **base)
    text = _read("orbit_rule")
    check("result new · stored · indexed_words", a.get("result") == "new" and a.get("stored") is True
          and a.get("indexed_words") is True, {k: a.get(k) for k in ("result", "stored", "indexed_words")})
    check("sha is the file's", a.get("sha") == rc.file_sha(os.path.join(_MEM, "orbit_rule.md")) and len(a.get("sha") or "") == 40, a.get("sha"))
    check("the frontmatter carries the contract fields",
          all(s in text for s in ("idempotency_key: k1", "payload_sha: ", "project_id: proj-A", "source: orbit task t1", "verified_at: 2026-10-01")), text[:260])
    check("verified_at is observed_at · project_id echoed · payload_sha 40 hex",
          a.get("verified_at") == "2026-10-01" and a.get("project_id") == "proj-A" and len(a.get("payload_sha") or "") == 40, a)
    check("embedded is false or unknown, never true right after saving", a.get("embedded") in (False, None), a.get("embedded"))
    check("served names the store situation", a.get("served") in ("n/a", "pending", "unknown"), a.get("served"))

    print("\n" + "=" * 72 + "\n③ noop\n" + "=" * 72)
    sha_before = a["sha"]
    b = _j(db, name="orbit_rule", idempotency_key="k1", project_id="proj-A", source="orbit task t1",
           observed_at="2026-10-01", format="json", **base)
    check("same key and payload → noop, same sha, file untouched",
          b.get("result") == "noop" and b.get("sha") == sha_before and _read("orbit_rule") == text, b.get("result"))
    check("the answer is the same as the first, apart from timing and index stats",
          _strip(dict(a, result="noop")) == _strip(b), (_strip(a), _strip(b)))

    print("\n" + "=" * 72 + "\n④ sha_mismatch · ⑤ updated\n" + "=" * 72)
    c = _j(db, name="orbit_rule", idempotency_key="k1", format="json",
           description="harbour ledger rule", type="lesson", body="The ledger closes on monday.")
    check("same key, other body, no expected_sha → conflict sha_mismatch, stored false, file untouched",
          c.get("result") == "conflict" and (c.get("conflict") or {}).get("reason") == "sha_mismatch"
          and c.get("stored") is False and _read("orbit_rule") == text and c["conflict"].get("current_sha") == sha_before, c.get("conflict"))
    d = _j(db, name="orbit_rule", idempotency_key="k1", format="json", expected_sha=sha_before,
           description="harbour ledger rule", type="lesson", body="The ledger closes on monday.")
    check("with expected_sha = current → updated, new sha, indexed",
          d.get("result") == "updated" and d.get("sha") != sha_before and d.get("indexed_words") is True
          and "monday" in _read("orbit_rule"), d.get("result"))

    print("\n" + "=" * 72 + "\n⑥ name_taken\n" + "=" * 72)
    sha_now = d["sha"]
    e = _j(db, name="orbit_rule", idempotency_key="k2", format="json", **base)
    check("another key without proof → conflict name_taken, file untouched",
          e.get("result") == "conflict" and (e.get("conflict") or {}).get("reason") == "name_taken"
          and rc.file_sha(os.path.join(_MEM, "orbit_rule.md")) == sha_now, e.get("conflict"))
    f = _j(db, name="legacy_note", idempotency_key="k3", format="json", **base)
    check("a memory written by a person (no key) is protected the same way",
          f.get("result") == "conflict" and (f.get("conflict") or {}).get("reason") == "name_taken"
          and "Changed by a person." in _read("legacy_note"), f.get("conflict"))
    g = _j(db, name="legacy_note", idempotency_key="k3", format="json", expected_sha=f["conflict"]["current_sha"], **base)
    check("with the version proved → updated, and the key is now k3",
          g.get("result") == "updated" and g.get("idempotency_key") == "k3" and "idempotency_key: k3" in _read("legacy_note"), g.get("result"))

    print("\n" + "=" * 72 + "\n⑦ partial success\n" + "=" * 72)
    real = store.reindex
    store.reindex = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("index unavailable"))  # noqa: E731
    try:
        h = _j(db, name="orbit_partial", idempotency_key="k4", format="json", **base)
    finally:
        store.reindex = real
    check("the file is stored but not indexed, and the error is named",
          h.get("result") == "new" and h.get("stored") is True and h.get("indexed_words") is False
          and "index unavailable" in str((h.get("index") or {}).get("error")), h)
    i = _j(db, name="orbit_partial", idempotency_key="k4", format="json", **base)
    check("the retry is a noop whose indexed_words is now true", i.get("result") == "noop" and i.get("indexed_words") is True, i.get("indexed_words"))

    print("\n" + "=" * 72 + "\n⑧ arguments are checked\n" + "=" * 72)
    bad = [
        ("expected_sha not 40 hex", _j(db, name="x1", idempotency_key="k9", format="json", expected_sha="abc", **base)),
        ("observed_at not a date", _j(db, name="x2", idempotency_key="k9", format="json", observed_at="yesterday", **base)),
        ("129-char key", _j(db, name="x3", idempotency_key="k" * 129, format="json", **base)),
        ("empty body", _j(db, name="x4", idempotency_key="k9", format="json", description="d", type="lesson", body="  ")),
    ]
    for label, r in bad:
        check("%s → invalid_argument" % label, r.get("ok") is False and (r.get("error") or {}).get("code") == "invalid_argument", r)
    check("(control) none of the refused calls wrote a file",
          not any(os.path.exists(os.path.join(_MEM, n + ".md")) for n in ("x1", "x2", "x3", "x4")))

    print("\n" + "=" * 72 + "\n⑨ the save path indexes the words and re-measures nothing\n" + "=" * 72)
    # A client with a 30 s timeout saw the file land but never the answer (2026-10-29): the default index pass
    # re-measured the hook threshold and the vector gate after every save (15–32 s on a 2,246-document copy).
    from brain import calibrate as _cal, vectors as _vec
    calls = {"threshold": 0, "gate": 0}
    real_cal, real_gate = _cal.calibrate, _vec.recalibrate_if_free
    _cal.calibrate = lambda db, *a, **k: (calls.__setitem__("threshold", calls["threshold"] + 1) or {"threshold": 9.0})  # noqa: E731
    _vec.recalibrate_if_free = lambda db, *a, **k: (calls.__setitem__("gate", calls["gate"] + 1) or None)  # noqa: E731
    try:
        j = _j(db, name="orbit_quick", idempotency_key="k7", format="json", **base)
        check("a contract save never re-measures the threshold or the gate",
              j.get("result") == "new" and j.get("indexed_words") is True and calls == {"threshold": 0, "gate": 0}, calls)
        store.reindex(db)
        check("(control) a default index pass does re-measure", calls["threshold"] >= 1, calls)
    finally:
        _cal.calibrate, _vec.recalibrate_if_free = real_cal, real_gate

    revision_checks(db)
    review_checks()
    extraction_checks(db)
    identity_checks(db)
    unreadable_fact_checks(db)
    source_failure_checks(db)
    staged_index_checks(db)
    history_batch_checks(db)
    content_snapshot_checks(db)
    duplicate_usage_checks(db)
    feedback_scope_checks(db)
    unicode_feedback_checks(db)
    recorded_feedback_checks(db)
    exclusive_create_checks(db)
    print("\n" + "=" * 72)
    if FAILS:
        print("❌ failed: %d — %s" % (len(FAILS), " · ".join(FAILS)))
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    from unittest.mock import patch
    # Contract fixtures share one database and assert exact preservation. Workers
    # have their own integration suite; do not let earlier saves launch concurrent
    # writers into later read-only or fault-injection cases. Queueing stays real.
    with patch("brain.deferred.kick", return_value=False):
        sys.exit(main())
