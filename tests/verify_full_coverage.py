#!/usr/bin/env python3
"""Whole-body ingestion resumes complete batches without paid calls. Includes a capped-prefix control."""
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
tmp=tempfile.TemporaryDirectory(prefix='brain-coverage-')
os.environ['BRAIN_HOME']=str(Path(tmp.name)/'home')
os.environ['BRAIN_CONFIG']=str(Path(tmp.name)/'config.json')
os.environ['BRAIN_VECTOR_STORE']='sqlite'
os.environ['BRAIN_GRAPH_STORE']='sqlite'
from brain import store, vectors
import _stores_fixture as fx

mem=fx.write_corpus(tmp.name)
fx.write_note(mem,'long_note','a'*35000+' tail evidence')
db=store.connect()
store.reindex(db,recalibrate=False)
# All output is deterministic and local. No provider is called.
vectors.BATCH=4
vectors.DIM=3
vectors._wait_for_quota=lambda n: 0
vectors._mark_sent=lambda n: None
calls=[]
def embed(texts, **kw):
    calls.extend(texts)
    return [[1,0,0] for _ in texts]
vectors.embed=embed
long=db.execute('SELECT * FROM docs WHERE name="long_note"').fetchone()
want=len(vectors.chunk_spans(long['body']))
assert want>12
vectors.ensure_table(db)
with db:
    for i in range(12):
        db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?)',(long['id'],i,long['sha'],vectors.model_tag(),3,vectors._unit([1,0,0]).tobytes()))
assert any(r['id']==long['id'] for r in vectors.stale_docs(db))  # old prefix must be detected
first=True
def interrupt(texts, **kw):
    global first
    if first:
        first=False
        return embed(texts,**kw)
    raise vectors.DailyQuotaReached('fixture quota')
vectors.embed=interrupt
out=vectors.build(db,only_ids=[long['id']])
assert out['daily_quota_reached']
assert db.execute('SELECT COUNT(*) FROM vectors WHERE doc_id=?',(long['id'],)).fetchone()[0]==16
vectors.embed=embed
vectors.build(db,only_ids=[long['id']])
assert len(calls)==want-12  # neither old prefix nor completed batch was charged again
assert not any(r['id']==long['id'] for r in vectors.stale_docs(db))
assert 'tail evidence' in calls[-1]
assert max(b for a,b in vectors.chunk_spans(long['body']))==len(long['body'].strip())
print('PASS: capped prefix detected, quota stops, batches resume, tail covered without duplicate embedding')
db.close()
