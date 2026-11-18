"""Dashboard language check — ★does the ★whole thing★ come out in the chosen language★. (local · budget 0)

## Why this check exists (2026-09-02, caught by the user)

Labels went into 6 languages and it looked "done," but when the user opened the English screen the
comparison table was ★entirely in Korean★. A full audit found ★761 pieces★, from five different sources.

    graph_html (legend·meta·tooltips)   1,668 chars   ← the picture was carrying its own words
    details_html (the old table)          600 chars   ← thought folding it away was enough (it wasn't)
    bench_rows (scorecard)                18 cells   ← held only Korean sentences, nothing to translate
    issues/metrics (health)               18 cells
    kpi subtitle                           1 cell

★Lesson: translating a screen is not translating labels.★ ★Every producer★ that sends text to the
screen has to carry a key. So this check looks not at labels but at the ★rendered page★ —
it catches it no matter where it came from.

## ⛔ Splits data from UI

A memory's ★name★ (`feedback_no_self_merge`) and its kind value are ★data★ — translating them is wrong.
What gets translated is only the human-facing words. So this check counts only ★Hangul syllables★:
a memory name is ASCII so it never gets caught, and a Korean UI string always does.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import dashboard, dashview, i18n, store                # noqa: E402

HANGUL = re.compile(r"[가-힣]")
FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def visible(html: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", "\n", body)


def verify_cli_readonly():
    import hashlib
    import sqlite3
    import subprocess
    import tempfile
    from pathlib import Path
    from unittest.mock import call, patch
    from brain import cli
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "missing"
        env = dict(os.environ, BRAIN_HOME=str(home), BRAIN_CONFIG=str(root / "config.json"),
                   BRAIN_LANG="en")
        def run():
            return subprocess.run([sys.executable, "-m", "brain.cli", "dashboard", "--no-open",
                                   "--out", str(root / "page.html")], env=env,
                                  capture_output=True, text=True, encoding="utf-8")
        result = run()
        check("CLI missing index is not created", result.returncode == 2 and not home.exists(),
              result.stdout + result.stderr)
        with patch.dict(os.environ, env):
            db = store.connect()
            path = Path(store.db_path())
            store.set_meta(db, "schema_version", "5")
            db.commit()
            db.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = run()
        check("CLI incompatible schema is not migrated", result.returncode == 2
              and hashlib.sha256(path.read_bytes()).hexdigest() == before
              and not (root / "page.html").exists(), result.stdout + result.stderr)
        db = sqlite3.connect(str(path))
        db.execute("UPDATE meta SET v=? WHERE k='schema_version'", (store.SCHEMA_VERSION,))
        db.commit()
        db.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = run()
        check("CLI current index renders without changing bytes", result.returncode == 0
              and (root / "page.html").exists()
              and hashlib.sha256(path.read_bytes()).hexdigest() == before,
              result.stdout + result.stderr)
        with patch.dict(os.environ, env), patch.object(store, "connect", wraps=store.connect) as connect:
            check("CLI explicitly requests read-only connection",
                  cli.main(["dashboard", "--no-open", "--out", str(root / "page.html")]) == 0
                  and connect.call_args_list == [call(readonly=True)])


def verify_observatory():
    import datetime
    import json
    import sqlite3
    import tempfile
    from pathlib import Path
    from brain import observatory
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    store._init(db)
    for n in (1, 2, 3):
        db.execute("INSERT INTO docs(id,name,path,source,kind,sha,evidence_date,description) VALUES(?,?,?,?,?,?,?,?)",
                   (n, "note_%d" % n, "/notes/%d.md" % n, "memory", "project" if n == 1 else "lesson",
                    str(n), "2026-01-02", "</script><script>bad()</script>" if n == 2 else "Evidence"))
    db.execute("INSERT INTO links VALUES(2,'note_1')")
    for day in ("2026-01-01", "2026-01-02"):
        db.execute("INSERT INTO recalls(ts,query,hits,top) VALUES(?,?,?,?)",
                   (day+"T12:00:00", "repeat", 1, '["note_2"]'))
    db.commit()
    before = list(db.iterdump())
    original_home = observatory.analysis_home
    with tempfile.TemporaryDirectory() as temp:
        observatory.analysis_home = lambda: Path(temp)
        try:
            now = datetime.datetime(2026, 1, 3)
            data = observatory.collect(db, days=7, now=now)
            check("explorer counts recorded repeats and distinct days", data["repeated"][0]["count"] == 2 and data["repeated"][0]["days"] == 2)
            check("explorer edges are explicit and directions preserved", data["edges"] == [(2, 1)])
            check("explorer classification carries provenance", data["nodes"][1]["basis"] == "linked")
            packet = {"snapshot": observatory.snapshot_id(db), "question": "Explore evidence", "lenses": [
                {"title": "A new perspective", "why": "Observed connection", "categories": [{"label": "A group", "ids": [1, 2]}]}],
                "insights": [{"kind": "hypothesis", "text": "A possibility", "ids": [2]}]}
            saved = observatory.save_analysis(db, packet)
            observatory.record_feedback({"revision": saved["revision"], "index": 0, "action": "revise", "comment": "Separate these categories"})
            next_context = observatory.analysis_context(db, offset=0, limit=2)
            check("host receives prior revision and correction on the next analysis", next_context["previous"]["revision"] == saved["revision"] and next_context["feedback"][0]["action"] == "revise")
            check("host context is paginated without silently dropping the rest", next_context["total"] == 3 and next_context["next_offset"] == 2)
            for label, changed in (("unknown evidence", dict(packet, lenses=[{"title": "Bad", "why": "Invalid", "categories": [{"label": "Unknown", "ids": [999]}]}])),
                                   ("outdated snapshot", dict(packet, snapshot="obsolete"))):
                rejected = False
                try:
                    observatory.save_analysis(db, changed)
                except ValueError:
                    rejected = True
                check("control: reject " + label, rejected)
            data = observatory.collect(db, days=7, now=now)
            check("current cited analysis can be used", data["analysis_current"])
            page = observatory.render(data)
            check("embedded evidence cannot terminate its JSON script", "</script><script>bad()" not in page and "\\u003c/script" in page)
            check("offline page blocks network calls", "connect-src 'none'" in page)
            for lang in i18n.LANGS:
                check("explorer ships translated controls for " + lang, '"'+lang+'"' in page and i18n.catalog(lang)["obs.title"] in page)
            check("read and publish leave canonical DB untouched", before == list(db.iterdump()))
            db.execute("UPDATE docs SET sha='changed' WHERE id=3")
            check("unrelated changes preserve cited lenses", observatory.collect(db, now=now)["analysis_current"])
            db.execute("UPDATE docs SET sha='changed' WHERE id=2")
            check("control: changed cited evidence disables old lenses", not observatory.collect(db, now=now)["analysis_current"])
            check("only valid published revisions exist", len(list(Path(temp).glob("analysis-*.json"))) == 1)
        finally:
            observatory.analysis_home = original_home
            db.close()


def verify_workspace_controls():
    """Run the real handlers against a small DOM fixture when Node is available."""
    import json
    import shutil
    import subprocess
    if not shutil.which('node'):
        print('SKIP workspace handler execution: Node unavailable; browser QA still required')
        return
    fixture = r"""
const vm=require('vm'),assert=require('assert');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.events={};this.dataset={};this.attrs={};this.value='';this.textContent='';this.classList={contains:()=>false,toggle:()=>true};}
 appendChild(n){this.children.push(n);return n;}
 replaceChildren(){this.children=[];this.textContent='';}
 get options(){return this.children;}
 addEventListener(k,fn){this.events[k]=fn;}
 setAttribute(k,v){this.attrs[k]=v;}
 removeAttribute(k){delete this.attrs[k];}
 focus(){}
}
const elements={};['workspace-data','workspace-i18n','source','scope','validity','search','detail','memory-list','count','prev','next','mtoggle'].forEach(id=>elements[id]=new Element(id));
function memory(id,name,scope,status,history){return {id,name,project_id:scope,source:'memory',kind:'user',preview:'text',body_chars:4,path:'/private/'+name,validity:{status},history};}
const records=[memory(1,'old','','superseded',[1,2]),memory(2,'new','','active',[1,2]),memory(3,'foreign','garden','active',[3])];
elements['workspace-data'].textContent=JSON.stringify({memories:records,states:{superseded:1,active:2}});
elements['workspace-i18n'].textContent=JSON.stringify({en:{'dash.inspect.all':'All','dash.inspect.unscoped':'Unscoped','dash.inspect.select':'Select a memory'}});
const langs=[new Element('button')];langs[0].dataset.lang='en';
const doc={documentElement:new Element('html'),createElement:t=>new Element(t),getElementById:id=>elements[id],querySelectorAll:q=>q==='.langs button'?langs:[],addEventListener:(event,fn)=>{doc.ready=fn;}};doc.documentElement.lang='en';
const context={document:doc,Option:function(text,value){this.text=text;this.value=value;},window:{matchMedia:()=>({matches:false,addEventListener(){}})},localStorage:{getItem:()=>null,setItem(){}},Map,Set,JSON};
vm.runInNewContext(CODE,context);doc.ready();
assert.equal(elements.count.textContent,'1–3 / 3');
assert.equal(elements.scope.options[0].value,'');
const unscoped=elements.scope.options.find(o=>o.text==='Unscoped');assert(unscoped&&unscoped.value!=='');
elements.scope.value=unscoped.value;elements.scope.events.change();assert.equal(elements.count.textContent,'1–2 / 2');
elements['memory-list'].children[0].events.click();assert.equal(elements.detail.children[0].textContent,'old');
const history=elements.detail.children.find(n=>n.className==='history-list');history.children[1].events.click();assert.equal(elements.detail.children[0].textContent,'new');
elements.scope.value=elements.scope.options.find(o=>o.text==='garden').value;elements.scope.events.change();assert.equal(elements.count.textContent,'1–1 / 1');assert.equal(elements.detail.children[0].textContent,'Select a memory');
elements.search.value='absent';elements.search.events.input();assert.equal(elements.count.textContent,'0–0 / 0');assert(elements.next.disabled&&elements.prev.disabled);
console.log('PASS actual handlers: all/unscoped separation, version navigation, excluded detail clearing and empty results');
"""
    run = subprocess.run(['node', '-e', 'const CODE='+json.dumps(dashview.WORKSPACE_JS)+';\n'+fixture],
                         capture_output=True, text=True, encoding='utf-8')
    check('actual workspace controls preserve filters and version navigation',run.returncode==0,run.stdout.strip() or run.stderr[-700:])


def verify_workspace():
    import json
    import sqlite3
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from brain import scorecard
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    store._init(db)
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / 'source.html'
        source.write_text('canonical source', encoding='utf-8')
        for did, name, scope, meta in (
                (1, 'older', 'garden', dict(topic='plant', facet='water', status='active')),
                (2, 'current', 'garden', dict(topic='plant', facet='water', status='active', supersedes=['older'])),
                (3, 'current', 'other', dict(topic='plant', facet='water', status='active')),
                (4, 'unknown', '', {})):
            db.execute("INSERT INTO docs(id,name,path,source,kind,sha,project_id,lifecycle,body,description) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (did,name,str(source) if did==1 else str(Path(temp)/('%s.md'%did)), 'memory','reference',str(did),scope,
                        json.dumps(meta) if meta else '', '</script><script>bad()</script>'+'x'*900, 'A description'))
        db.commit()
        before=list(db.iterdump())
        old=dict(at='2000-01-01',axis_version=1,pipeline='word',total=42,axes={'precision':None,'reach':42})
        with patch.object(scorecard,'history',return_value=[old]), patch.object(scorecard,'collect',side_effect=AssertionError('fresh measurement')), patch.object(scorecard,'bench',side_effect=AssertionError('fresh benchmark')):
            p=dashboard.payload(db)
            page=dashview.render(p)
        rows={r['id']:r for r in p['memories']}
        check('workspace resolves current and superseded without cross-scope history', rows[1]['validity']['status']=='superseded' and rows[2]['validity']['current'] is True and rows[1]['history']==[1,2] and rows[3]['history']==[3])
        check('untracked validity stays unknown, never a current claim', rows[4]['validity']['current'] is None and p['states']['untracked']==1)
        check('body preview is bounded with original length retained', len(rows[1]['preview'])==800 and rows[1]['body_chars']>800)
        check('historical measurement retains date and missing values', p['performance']['at']=='2000-01-01' and p['performance']['axes']['precision'] is None)
        check('memory JSON cannot terminate its script', '</script><script>bad()' not in page and r'\u003c/script' in page)
        check('view blocks outbound connections and uses text-only memory rendering', "connect-src 'none'" in page and 'innerHTML' not in dashview.WORKSPACE_JS)
        check('opening the workspace leaves the database unchanged', before==list(db.iterdump()))
        for target in (source, Path(temp)/'alias.html', Path(temp)/'output.md'):
            if target.name=='alias.html':
                os.link(source,target)
            rejected=False
            try:
                dashboard.write(db,str(target),False)
            except ValueError:
                rejected=True
            check('control: reject source/alias/non-HTML output '+target.name,rejected)
        check('source bytes survive rejected output',source.read_text(encoding='utf-8')=='canonical source')
        from brain import lifecycle
        for did, name, scope, meta in (
                (5, 'legacy', 'garden', {}),
                (6, 'replacement', 'garden', dict(topic='legacy-topic', facet='state', status='active',
                    supersedes=['legacy'], legacy_whole_record_sha={'legacy':'5'*40},
                    valid_from='2026-01-01', change_reason='Explicit correction', evidence=['source'])),
                (7, 'legacy', 'other', {}),
                (8, 'replacement', 'other', dict(topic='legacy-topic', facet='state', status='active'))):
            db.execute("INSERT INTO docs(id,name,path,source,sha,project_id,lifecycle,body) VALUES(?,?,?,?,?,?,?,?)",
                       (did,name,str(Path(temp)/('%s.md'%did)),'memory',str(did)*40,scope,
                        json.dumps(meta) if meta else '', 'Original body'))
        db.commit()
        before = list(db.iterdump())
        with patch.object(scorecard, 'history', return_value=[]):
            linked = dashboard.payload(db)
        records = {r['id']:r for r in linked['memories']}
        for did in (5,6):
            canonical = lifecycle.history(db, records[did]['name'], scope='garden')
            check('workspace includes pinned legacy history from either version '+str(did),
                  records[did]['history']==[5,6]
                  and {r['name'] for r in canonical['revisions']} ==
                      {records[i]['name'] for i in records[did]['history']})
        check('legacy history stays scoped and untracked records include themselves',
              records[7]['history']==[7] and records[8]['history']==[8]
              and records[4]['history']==[4])
        check('linked history inspection does not change the index', before==list(db.iterdump()))
        empty=sqlite3.connect(':memory:');empty.row_factory=sqlite3.Row;store._init(empty)
        with patch.object(scorecard,'history',return_value=[]):
            data=dashboard.payload(empty)
            check('empty corpus and absent measurements render without false zero score',not data['memories'] and data['performance'] is None and 'workspace-data' in dashview.render(data))
    db.close()


def main():
    print("=" * 72 + "\ndashboard — does everything come out in the chosen language\n" + "=" * 72)
    db = store.connect()
    saved_env, saved_lang = os.environ.get("BRAIN_LANG"), i18n._lang
    try:
        pages = {}
        for code in i18n.LANGS:
            os.environ["BRAIN_LANG"] = code
            i18n._lang = None
            i18n._cache.clear()
            i18n.lang(refresh=True)
            pages[code] = dashview.render(dashboard.payload(db))

        # ── ① not a single Hangul character remains on a non-Korean language ──────────
        for code, html in pages.items():
            if code == "ko":
                continue
            left = [l.strip() for l in visible(html).split("\n")
                    if l.strip() and HANGUL.search(l)]
            check("%s: no Korean remains on screen" % code, not left,
                  "%d found %s" % (len(left), [x[:34] for x in left[:2]]) if left else "")

        # ── ② the Korean screen comes out in Korean (the switcher isn't dead) ────────
        check("ko: the screen comes out in Korean",
              HANGUL.search(visible(pages["ko"])) is not None)

        # ── ③ ★per source★, was it actually translated (looking only at labels misses it) ──────────
        en = pages["en"]
        for what, needle in (("comparison table row name", "Answer found"),
                             ("diagnostic tile name", "Broken links"),
                             ("graph legend", "Rules"),
                             ("graph meta", "connected"),
                             ("budget legend", "held for interactive recall"),
                             ("axis name", "Reach")):
            check("the English screen has %s in English" % what, needle in en, needle)

        # ── ③-b ⛔ ★does it switch on the client side too★ — pins down a spot frozen at server language
        #    (2026-09-02: the catalog had the translation, but `UI_PREFIX` dropped `bench.`·`health.`,
        #     and the comparison table on the Japanese screen stayed in English. ★Make a key and it has
        #     to go into that list too★ — and this check enforces that.)
        from brain import dashview as _dv
        cat_en = i18n.catalog("en")
        used_prefixes = set()
        for html in pages.values():
            for k in re.findall(r'data-t="([A-Za-z0-9_.\-]+)"', html):
                used_prefixes.add(k.split(".")[0] + ".")
        missing_pref = sorted(pfx for pfx in used_prefixes
                              if pfx not in _dv.UI_PREFIX)
        check("★every prefix the screen uses is in the client-side table★",
              not missing_pref, ", ".join(missing_pref) or str(sorted(used_prefixes)))

        # And are the translations of those keys actually in the page (one-language sample)
        ja = i18n.catalog("ja")
        for k in ("bench.answer", "health.dangling", "kind.feedback", "graph.meta"):
            check("the Japanese translation is in the page: %s" % k,
                  ja.get(k) and ja[k] in pages["en"], (ja.get(k) or "")[:24])

        # ── ④ ⛔ a memory's ★name★ is never translated (it's data) ───────────────
        # ⛔ checked with ★this corpus's own names★ — it used to look for the author's prefixes
        names = [r[0] for r in db.execute(
            "SELECT name FROM docs WHERE source='memory' ORDER BY name LIMIT 400")]
        if names:
            check("★a memory name stays as-is★ (translating data is wrong)",
                  any(n in en for n in names), "%d names looked for" % len(names))
        else:
            print("⏭ a memory name stays as-is — no notes here to look for")

        # ── ⑤ all six languages are ★in the page★ (switching is offline) ────
        for code in i18n.LANGS:
            check("%s's catalog is in the page (never calls a CDN)" % code,
                  '"%s"' % code in pages["en"])
        check("no request goes outward (0 http references)",
              not re.search(r'<(?:script|link|img)[^>]+(?:src|href)=[\"\']https?://', pages["en"]) and "connect-src 'none'" in pages["en"])

        # ── ⑤-b ⛔ ★the JS ships without a syntax error★ — a raw newline inside a string kills it
        #    (2026-09-02: a `\n` inside a Python string became an actual newline, `Invalid or
        #     unexpected token` — ★the whole language switcher died while the screen looked fine★.)
        for code, html in pages.items():
            js = re.search(r"<script>(.*?)</script>", html, re.S)
            check("%s: no broken string in the JS" % code, js is not None)
            if js:
                bad = [l for l in js.group(1).split("\n")
                       if l.count("'") % 2 == 1 and "var T=" not in l]
                check("%s: quotes are balanced (no raw newline)" % code, not bad,
                      (bad[0][:50] if bad else ""))

        # ── ⑥ a placeholder never leaks through as literal text ──────────────────────────
        for code, html in pages.items():
            raw = re.findall(r"\{(?:at|pct|names|n|e)\}", visible(html))
            check("%s: no placeholder leaks onto the screen" % code, not raw, str(raw[:3]))
    finally:
        os.environ.pop("BRAIN_LANG", None)
        if saved_env:
            os.environ["BRAIN_LANG"] = saved_env
        i18n._lang = saved_lang
        i18n._cache.clear()

    verify_cli_readonly()
    verify_workspace_controls()
    verify_workspace()
    verify_observatory()
    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
