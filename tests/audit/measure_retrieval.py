"""Compare retrieval paths using a private SQLite snapshot and caller-supplied labels.
Reads configured live backends; query caches may change. _measure excludes personal
reranking; earlier arms warm caches. Output is private, not a multilingual benchmark.
"""
import sys,json,time,statistics,sqlite3
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
parser.add_argument('--output',type=Path,required=True,help='Private output directory; may contain corpus data. Do not commit.')
parser.add_argument('--evaluation',type=Path,required=True,help='JSON list of {q,gold,terms?}; supply your own labelled cases.')
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(args.repo))
from brain import store,search,server,evalinit
out=args.output
src=store.connect();db=sqlite3.connect(str(out/'snapshot.db'));src.backup(db);db.close();db=sqlite3.connect(str(out/'snapshot.db'));db.row_factory=sqlite3.Row
cases=json.loads(args.evaluation.read_text());cases=[c for c in cases if isinstance(c,dict) and c.get('gold')]
if not cases: raise SystemExit('No labelled evaluation cases')
result=[]
for i,c in enumerate(cases):
 row={'q':c['q'],'gold':c['gold'],'arms':{}}
 for arm in ['words','words_graph','meaning','mcp_auto','mcp_terms']:
  t=time.perf_counter()
  if arm in ['words','words_graph']:
   got=search.recall(db,c['q'],k=8,log=False,graph=arm=='words_graph');names=[r['name'] for r in got if not r.get('related')];extra=[r['name'] for r in got if r.get('related')];meta={}
  elif arm=='meaning':
   got,why=search.meaning(db,c['q'],8);names=[r['name'] for r in got];extra=[];meta={'why':why}
  else:
   args={'query':c['q'],'k':8,'format':'json'}
   if arm=='mcp_terms':args['terms']=c.get('terms',[])
   d=json.loads(server.call_tool(db,'recall',args,_measure=True));names=[r['name'] for r in d.get('results',[])];extra=[r['name'] for r in d.get('related',[])];meta=d.get('meta',{})
  rank=next((j+1 for j,n in enumerate(names) if evalinit.gold_hit(c['gold'],n)),None)
  row['arms'][arm]={'rank':rank,'ms':round((time.perf_counter()-t)*1000,2),'names':names,'related':extra,'meta':meta}
 result.append(row)
 (out/'retrieval-progress.json').write_text(json.dumps(result,ensure_ascii=False))
 if (i+1)%10==0:print('measured',i+1,'/',len(cases),flush=True)
summary={'n':len(result),'corpus_docs':db.execute('select count(*) from docs').fetchone()[0],'note':'Frozen SQLite snapshot; configured live Qdrant/Neo4j + local embed. _measure disables personal reranking and recall feedback logging. Query cache warms after meaning arm. Personal historical evaluation, not global language benchmark.','arms':{}}
for arm in result[0]['arms']:
 rows=[r['arms'][arm] for r in result];ms=sorted(r['ms'] for r in rows);summary['arms'][arm]={'hit1':sum(r['rank']==1 for r in rows),'hit3':sum(r['rank'] is not None and r['rank']<=3 for r in rows),'hit8':sum(r['rank'] is not None for r in rows),'p50_ms':statistics.median(ms),'p95_ms':ms[int((len(ms)-1)*.95)],'related_rows':sum(len(r['related']) for r in rows)}
(out/'retrieval-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
