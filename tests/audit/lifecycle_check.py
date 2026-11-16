"""Synthetic multilingual revision audit. Exit 1 reports a failed assertion in the summary.
Uses an isolated home and stub embeddings; does not measure semantic search quality.
"""
import os,sys,json,tempfile
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
parser.add_argument('--output',type=Path,required=True,help='Private output directory; may contain corpus data. Do not commit.')
parser.add_argument('--baseline-repo',type=Path,help='Compare history outputs and cost with this checkout on identical synthetic data')
cli=parser.parse_args();cli.output.mkdir(parents=True,exist_ok=True)
os.environ['BRAIN_EMBED_PROVIDER']='stub'
root=Path(tempfile.mkdtemp(prefix="brain-life-audit-")); mem=root/"memory";mem.mkdir()
os.environ["BRAIN_HOME"]=str(root/"home");os.environ["BRAIN_CONFIG"]=str(root/"config.json")
(root/"config.json").write_text(json.dumps({"sources":[{"name":"memory","path":str(mem),"include":["*.md"]}]}))
sys.path.insert(0,str(cli.repo))
from brain import server,store,lifecycle
db=store.connect();store.reindex(db,full=True,recalibrate=False,refresh_history=False)
for i in range(10):
 server.call_tool(db,"remember",dict(name="background_"+str(i),description="unrelated reference",type="reference",body="Reference archive unrelated astronomy cooking travel music insurance "+str(i)),_measure=True)
results=[]
for lang,topic,old,new in [("ko","\uc0dd\ud65c","\uc0b0\ucc45\uc740 \uc6d4\uc694\uc77c\uc785\ub2c8\ub2e4.","\uc0b0\ucc45\uc740 \uae08\uc694\uc77c\uc785\ub2c8\ub2e4."),("en","routine","Walk on Monday.","Walk on Friday."),("ja","\u65e5\u5e38","\u6563\u6b69\u306f\u6708\u66dc\u65e5\u3067\u3059\u3002","\u6563\u6b69\u306f\u91d1\u66dc\u65e5\u3067\u3059\u3002"),("ar","\u0631\u0648\u062a\u064a\u0646","\u0627\u0644\u0645\u0634\u064a \u064a\u0648\u0645 \u0627\u0644\u0627\u062b\u0646\u064a\u0646.","\u0627\u0644\u0645\u0634\u064a \u064a\u0648\u0645 \u0627\u0644\u062c\u0645\u0639\u0629."),("es","rutina","Caminar el lunes.","Caminar el viernes.")]:
 def save(name,body,meta):
  args=dict(name=name,description=body,type="user",body=body,lifecycle=meta,project_id=lang)
  prep=json.loads(server.call_tool(db,"remember",dict(args,prepare_only=True),_measure=True))
  return json.loads(server.call_tool(db,"remember",dict(args,expected_fact_sha=prep["expected_fact_sha"]),_measure=True))
 a=save(lang+"_old",old,dict(topic=topic,facet="walk-day",valid_from="2026-10-01",evidence=["user confirmed old schedule"]))
 b=save(lang+"_new",new,dict(topic=topic,facet="walk-day",valid_from="2026-11-01",supersedes=[lang+"_old"],change_reason="User changed weekly arrangement",evidence=["user confirmed new schedule"]))
 states={}
 for view,at in [("current","2026-11-09"),("history","2026-11-09"),("current","2026-10-15")]:
  with lifecycle.view(view,at):
   snap=lifecycle.snapshot(db)
   states[view+str(at)]={r["name"]:snap.info(r["id"]) for r in db.execute("select id,name from docs where project_id=?",(lang,))}
 recalls={}
 for view,at in [("current","2026-11-09"),("history","2026-11-09"),("current","2026-10-15")]:
  args=dict(query=old+" "+new,scope=lang,k=8,format="json",semantic_k=0,view=view)
  if at:args["as_of"]=at
  recalls[view+str(at)]=json.loads(server.call_tool(db,"recall",args,_measure=True))
 results.append(dict(lang=lang,recalls=recalls,first=a,second=b,states=states,history=json.loads(server.call_tool(db,"memory_history",dict(name=lang+"_old",scope=lang),_measure=True))))
out=cli.output/"lifecycle-results.json";out.write_text(json.dumps(results,ensure_ascii=False,indent=2))
checks=[]
for r in results:
 lang=r["lang"];names=lambda key:[x["name"] for x in r["recalls"][key]["results"]]
 state=r["states"]
 state_ok=state["current2026-11-09"][lang+"_old"]["status"]=="superseded" and state["current2026-11-09"][lang+"_new"]["current"] and state["current2026-10-15"][lang+"_old"]["current"]
 recall_ok=names("current2026-11-09")==[lang+"_new"] and set(names("history2026-11-09"))=={lang+"_old",lang+"_new"} and names("current2026-10-15")==[lang+"_old"]
 checks.append(dict(language=lang,guarded_save_ok=r["first"]["result"]==r["second"]["result"]=="new",lifecycle_ok=state_ok,lexical_recall_ok=recall_ok))
(cli.output/"lifecycle-summary.json").write_text(json.dumps(checks,indent=2))
print(json.dumps(checks,indent=2))
# This is an audit: failures are findings, not silently waived feature tests.

def history_comparison(baseline_repo):
 import hashlib, importlib.util, sqlite3, statistics, time, unicodedata
 spec=importlib.util.spec_from_file_location('baseline_lifecycle',baseline_repo/'brain/lifecycle.py')
 baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
 fixture=sqlite3.connect(':memory:');fixture.row_factory=sqlite3.Row
 fixture.execute('CREATE TABLE docs(id INTEGER PRIMARY KEY,name TEXT,path TEXT,project_id TEXT,lifecycle TEXT,sha TEXT,body TEXT,evidence_date TEXT)')
 def add(name,meta,scope='bench',body=None):
  fixture.execute('INSERT INTO docs(name,path,project_id,lifecycle,sha,body,evidence_date) VALUES(?,?,?,?,?,?,?)',
   (name,str(root/'synthetic'/scope/(name+'.md')),scope,json.dumps(meta),'fixture-sha',body or ('Common line.\n'*150+name),'2026-11-01'))
 for i in range(1500):
  add('background_'+str(i),dict(topic='other_'+str(i),facet='value',valid_from='2026-10-01'))
 for i in range(120):
  meta=dict(topic='routine',facet='schedule',valid_from='2026-11-01',status='active')
  if i:meta['supersedes']=['revision_'+str(i-1)]
  add('revision_'+str(i),meta)
 # Same effective time, future and proposed branches, cycles, unresolved links,
 # canonical Unicode equivalence, scope isolation and untracked legacy roots.
 for i,topic in enumerate(['caf\u00e9','\ub09c\ucd08','\u862d','\u0631\u0648\u062a\u064a\u0646','\u091c\u0940\u0935\u0928']):
  add('old_'+str(i),dict(topic=topic,facet='day',valid_from='2026-10-01'))
  add('new_'+str(i),dict(topic=unicodedata.normalize('NFD',topic),facet='day',valid_from='2026-11-01',supersedes=['old_'+str(i)]))
  add('proposal_'+str(i),dict(topic=topic,facet='day',status='proposed',valid_from='2026-11-02',supersedes=['new_'+str(i)]))
  add('future_'+str(i),dict(topic=topic,facet='day',valid_from='2026-12-01',supersedes=['new_'+str(i)]))
  add('other_scope_'+str(i),dict(topic=topic,facet='day',valid_from='2026-10-01'),'other')
 add('conflict_a',dict(topic='conflict',facet='day',valid_from='2026-10-01'))
 add('conflict_b',dict(topic='conflict',facet='day',valid_from='2026-10-01'))
 add('cycle_a',dict(topic='cycle',facet='day',supersedes=['cycle_b']))
 add('cycle_b',dict(topic='cycle',facet='day',supersedes=['cycle_a']))
 add('broken',dict(topic='broken',facet='day',supersedes=['missing']))
 add('legacy',{})
 add('legacy_new',dict(topic='legacy',facet='day',valid_from='2026-11-01',supersedes=['legacy'],legacy_whole_record_sha={'legacy':'fixture-sha'}))
 fixture.commit()
 cases=[('revision_0',0,5),('revision_0',55,5),('revision_119',119,5),('revision_0',120,5),
        ('conflict_a',0,5),('cycle_a',0,5),('broken',0,5),('legacy',0,5)]
 cases += [('old_'+str(i),offset,2) for i in range(5) for offset in (0,2,4)]
 equality=[]
 for at in ('2026-10-15','2026-11-16','2026-12-02'):
  for name,offset,limit in cases:
   outputs=[]
   for module in (baseline,lifecycle):
    with module.view('current',at): outputs.append(module.history(fixture,name,'bench',offset,limit))
   equality.append(outputs[0]==outputs[1])
 assert all(equality),'history output changed on a fixed-view control'
 # Updating the DB must rebuild the exact-fact index too.
 fixture.execute("UPDATE docs SET lifecycle=? WHERE name='conflict_b'",(json.dumps(dict(topic='separate',facet='day')),));fixture.commit()
 for module in (baseline,lifecycle):
  with module.view('current','2026-11-16'):
   assert module.history(fixture,'conflict_a','bench')['current_names']==['conflict_a']
 samples={'before':[],'after':[]};reads={'before':[],'after':[]}
 for repeat in range(7):
  pair=[('before',baseline),('after',lifecycle)]
  if repeat%2:pair.reverse()
  for label,module in pair:
   statements=[];fixture.set_trace_callback(statements.append)
   start=time.perf_counter()
   with module.view('current','2026-11-16'):module.history(fixture,'revision_0','bench',55,5)
   samples[label].append((time.perf_counter()-start)*1000);fixture.set_trace_callback(None)
   reads[label].append(sum('SELECT ' in q and 'body' in q and 'FROM docs' in q for q in statements))
 measurements={label:dict(p50_ms=round(statistics.median(values),3),max_ms=round(max(values),3),body_selects=max(reads[label]),samples_ms=[round(x,3) for x in values]) for label,values in samples.items()}
 assert measurements['after']['body_selects']<=6,'five consecutive revisions need at most six bodies'
 assert measurements['before']['body_selects']>measurements['after']['body_selects'],'control must demonstrate eliminated body reads'
 cold={}
 for label,module in (('before',baseline),('after',lifecycle)):
  durations=[]
  for repeat in range(5):
   module._CACHE.pop(fixture,None)
   start=time.perf_counter()
   with module.view('current','2026-11-16'):module.history(fixture,'revision_0','bench',55,5)
   durations.append((time.perf_counter()-start)*1000)
  cold[label]=dict(p50_ms=round(statistics.median(durations),3),max_ms=round(max(durations),3))
 report=dict(code_sha256={label:hashlib.sha256((path/'brain/lifecycle.py').read_bytes()).hexdigest() for label,path in [('before',baseline_repo),('after',cli.repo)]},cold_snapshot=cold, warm_order='alternating before/after, seven repetitions',corpus_docs=fixture.execute('SELECT count(*) FROM docs').fetchone()[0],revision_count=120,page_size=5,exact_output_cases=len(equality),all_equal=all(equality),measurements=measurements,
             limitation='Synthetic in-memory SQLite; fixed timestamps; cold means rebuilt lifecycle snapshot, not a cold OS cache; not a general recall or live backend benchmark')
 (cli.output/'history-performance.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report,indent=2))
 fixture.close()

if cli.baseline_repo:
 history_comparison(cli.baseline_repo)

sys.exit(0 if all(all(v for k,v in row.items() if k != "language") for row in checks) else 1)
