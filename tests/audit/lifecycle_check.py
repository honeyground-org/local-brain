"""Synthetic multilingual revision audit. Exit 1 reports a failed assertion in the summary.
Uses an isolated home and stub embeddings; does not measure semantic search quality.
"""
import os,sys,json,tempfile
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
parser.add_argument('--output',type=Path,required=True,help='Private output directory; may contain corpus data. Do not commit.')
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

sys.exit(0 if all(all(v for k,v in row.items() if k != "language") for row in checks) else 1)
