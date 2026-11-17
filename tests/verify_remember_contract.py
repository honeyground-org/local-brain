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
    def prepare(args):
        return json.loads(server.call_tool(db,"prepare_fact",args,_measure=True))
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
        meta,_=store.parse_frontmatter(_read(target))
        check("provenance survives in the file "+str(i),json.loads(meta['extracted_from'])==payload['extracted_from'])
        snap=lifecycle.snapshot(db);did=snap.by_name[(scope,target)][0]
        check("proposal excluded from current resolution "+str(i),snap.resolve(did)==[] and snap.info(did)['status']=='proposed')
        check("mixed original remains byte-identical "+str(i),_read(source)==original)
        check("wrong quotes are refused "+str(i),prepare(dict(args,name=target+'_bad',quotes=['invented quote']))['error']['code']=='quote_mismatch')
        check("scope cannot be borrowed "+str(i),prepare(dict(args,name=target+'_other',project_id='unrelated'))['error']['code']=='source_unavailable')
        direct=copy.deepcopy(payload);direct['name']=target+'_active';direct['lifecycle']['status']='active'
        check("direct extraction cannot bypass proposal status "+str(i),_j(db,**direct)['error']['code']=='invalid_argument')
        direct=prepare(dict(args,name=target+'_tampered'))['save_arguments'];direct['extracted_from']['spans'][0]['start']+=1;direct['extracted_from']['spans'][0]['end']+=1
        check("save validates the span again "+str(i),_j(db,**direct)['error']['code']=='quote_mismatch')
        waiting=prepare(dict(args,name=target+'_waiting'))['save_arguments']
        with open(os.path.join(_MEM,source+'.md'),'a',encoding='utf-8') as f:f.write('\nNew source information.\n')
        check("stale source rejected on preparation "+str(i),prepare(dict(args,name=target+'_stale'))['error']['code']=='source_changed')
        check("stale source rejected on save "+str(i),_j(db,**waiting)['error']['code']=='source_changed' and not os.path.exists(os.path.join(_MEM,target+'_waiting.md')))
        check("successful old request remains a noop after source edit "+str(i),_j(db,**payload).get('result')=='noop')
        # Confirm only the extracted proposal, never the mixed original.
        confirmation=json.loads(server.call_tool(db,'prepare_revision',dict(predecessor=target,name=target+'_confirmed',description='Confirmed schedule',type='user',body=quote,project_id=scope,status='active',valid_from='2026-11-17',change_reason='Explicit confirmation',evidence=['The source owner confirmed this fact']),_measure=True))
        final=_j(db,**confirmation['save_arguments'])
        check("confirmation revises only the proposal "+str(i),final.get('result')=='new' and final['lifecycle']['supersedes']==[target] and source not in final['lifecycle']['supersedes'])
    saved=_j(db,name='repeated_quote_source',description='Repeated passage',type='reference',body='same quote; same quote')
    amb=prepare(dict(source_name='repeated_quote_source',source_sha=saved['sha'],quotes=['same quote'],name='ambiguous_fact',description='One fact',type='user',body='same quote',topic='one',facet='fact'))
    check("ambiguous repeated quotation requires more context",amb['error']['code']=='ambiguous_quote')
    before=capabilities.tool_flags(server.TOOLS)
    without=capabilities.tool_flags([t for t in server.TOOLS if t['name']!='prepare_fact'])
    check("extraction capability follows its declaration",before['remember']['prepare_fact'] and not without['remember']['prepare_fact'])


def main() -> int:
    db = _build()
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
    print("\n" + "=" * 72)
    if FAILS:
        print("❌ failed: %d — %s" % (len(FAILS), " · ".join(FAILS)))
        return 1
    print("✅ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
