"""Observe a fresh MCP RPC and actual configured Qdrant/Neo4j HTTP queries.
Does not certify every already-running host. Prints private query results to stdout.
Requires configured live services; run only when live retrieval is intended.
"""
from pathlib import Path

import sys,json,subprocess,urllib.request,collections,os
import argparse, select
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
parser.add_argument('--query',required=True,help='Live query; results may contain private corpus names')
parser.add_argument('--vector-port',type=int,default=6333)
parser.add_argument('--graph-port',type=int,default=7474)
args=parser.parse_args();repo=args.repo.resolve()
sys.path.insert(0,str(repo))
from brain import store,server,search
cmd=[sys.executable,str(repo/'bin/_run.py'),'brain.server']
p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
print("RPC_PROCESS",json.dumps(dict(pid=p.pid,argv=cmd,checkout=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip())))
try:
 for i,method,params in [(1,"initialize",{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"orbit-audit","version":"1"}}),(2,"tools/call",{"name":"brain_capabilities","arguments":{}})]:
  p.stdin.write(json.dumps(dict(jsonrpc="2.0",id=i,method=method,params=params))+"\n");p.stdin.flush()
  if not select.select([p.stdout],[],[],30)[0]: raise TimeoutError("MCP RPC exceeded 30 seconds")
  line=p.stdout.readline();r=json.loads(line)
  if i==2:
   d=json.loads(r["result"]["content"][0]["text"])
   print("LIVE_RPC_CAPABILITIES",json.dumps({k:d.get(k) for k in ["contract","server","code_generation","stores"]},ensure_ascii=False))
  else:print("INITIALIZE",line.strip())
finally:p.terminate();p.wait(timeout=5)
seen=collections.Counter();network=[]
real=urllib.request.urlopen
def urlopen(req,*a,**kw):
 url=req.full_url if hasattr(req,"full_url") else str(req)
 resp=real(req,*a,**kw)
 network.append(dict(url=url.split("?")[0],status=resp.status));return resp
urllib.request.urlopen=urlopen
def profile(frame,event,arg):
 if event=="call" and "/brain/" in frame.f_code.co_filename:
  seen[Path(frame.f_code.co_filename).name+":"+frame.f_code.co_name]+=1
sys.setprofile(profile)
db=store.connect()
rows,why=search.meaning(db,args.query,8)
r=json.loads(server.call_tool(db,"recall",dict(query=args.query,k=8,format="json"),_measure=True))
sys.setprofile(None)
print("MEANING",json.dumps(dict(names=[x["name"] for x in rows],why=why),ensure_ascii=False))
print("RECALL",json.dumps(dict(names=[x["name"] for x in r["results"]],related=r["related"],meta=r["meta"]),ensure_ascii=False))
print("NETWORK",json.dumps(network))
print("DB_FUNCTION_CALLS",json.dumps({k:v for k,v in seen.items() if any(s in k for s in ["qdrant","neo4j","remote","graphstore","vectorstore"])}))
assert rows and r["results"]
assert any(urllib.parse.urlparse(n["url"]).port == args.vector_port for n in network),"No observed Qdrant HTTP query"
assert any(urllib.parse.urlparse(n["url"]).port == args.graph_port for n in network),"No observed Neo4j HTTP query"
print("PASS live RPC and real Qdrant/Neo4j query paths observed; this does not certify every pre-existing host process")
