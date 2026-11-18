#!/usr/bin/env python3
"""Policy mechanics only: independent evidence, held-out rejection, rollback and isolation.
Synthetic rankings prove the guard can fail, not that retrieval quality improved.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
tmp = tempfile.TemporaryDirectory(prefix="brain-personal-")
os.environ['BRAIN_HOME'] = tmp.name
os.environ['BRAIN_CONFIG'] = str(Path(tmp.name) / 'config.json')
from brain import personal, store, vectors
from unittest.mock import patch

def evidence(db, n, winner, start=0):
    personal.ensure(db)
    with db:
        db.execute("INSERT OR IGNORE INTO docs(path,name,source,sha) VALUES('fixture','fixture','memory','sha')")
        for i in range(start, start+n):
            scores = {a: int(a == winner) for a in personal.ARMS}
            db.execute('INSERT INTO personal_events(identity,session,turn,query,path,sha,exposed,measured,scores,costs) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (personal.evidence_prefix()+str(i), 'session-'+str(i), 1, 'fixture', 'fixture', 'sha', 0, 1, json.dumps(scores),
                        json.dumps({a: .01 for a in personal.ARMS})))

def verify_semantic_route():
    import sqlite3
    from brain import observe, search, server, recalljson
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    store._init(db)
    base = [dict(name='route'+str(i), path='/route'+str(i)) for i in range(8)]
    hit = base[3]
    for row in base:
        db.execute("INSERT INTO docs(name,path,source,sha,title,description,body) VALUES(?,?,?,?,?,?,?)",
                   (row['name'],row['path'],'memory','sha','','','orchid' if row==hit else 'unrelated'))
    with patch.object(observe, 'write'), patch.object(vectors, '_qcache_get', return_value=None) as cache, \
         patch.object(server, '_words_unsure', return_value=True), \
         patch.object(search, 'recall', return_value=base), \
         patch.object(search, 'query_weights', return_value=[('orchid',1,['orchid'])]), \
         patch.object(search, 'union', return_value=([hit]+[r for r in base if r!=hit], '')) as union, \
         patch.object(search, 'meaning', return_value=([],'')) as meaning, \
         patch.object(server, '_note_recall'), \
         patch.object(recalljson, 'response', return_value={}), \
         patch.object(personal.time, 'monotonic', return_value=1):
        for i in range(12):
            personal.record(db,'orchid '+str(i),hit['path'],'route-session-'+str(i),1)
        result = personal.update(db)
        assert result['arm']=='baseline', 'cold observations certified a semantic policy'
        assert db.execute('SELECT COUNT(*) FROM personal_events WHERE measured=0').fetchone()[0]==12
        assert union.call_count==0, 'cold measurement requested semantic retrieval'
        cache.return_value=[1.0]
        healthy_union = union.return_value
        union.return_value = (base, 'semantic backend unavailable')
        meaning.return_value = ([], 'semantic backend unavailable')
        result = personal.update(db)
        assert result['arm']=='baseline', 'failed semantic retrieval certified a policy'
        assert db.execute('SELECT COUNT(*) FROM personal_events WHERE measured=0').fetchone()[0]==12, 'failed semantic retrieval consumed observations'
        with patch.object(personal, 'apply') as apply:
            server.call_tool(db, 'recall', {'query':'orchid live'})
        assert not apply.called, 'degraded live baseline used a certified reranker'
        union.return_value = healthy_union
        with patch.object(server, '_words_unsure', return_value=False):
            personal.update(db)
        assert db.execute('SELECT COUNT(*) FROM personal_events WHERE measured=0').fetchone()[0]==12, 'candidate-only semantic failure consumed observations'
        meaning.return_value = ([], '')
        union.reset_mock()
        with patch.object(server, 'call_tool', wraps=server.call_tool) as recall:
            personal.update(db)
        assert union.call_count==12, 'warm measurement did not use the automatic semantic route'
        assert db.execute('SELECT COUNT(*) FROM personal_events WHERE measured=1').fetchone()[0]==12
        assert all(c.args[2].get('semantic_k') is None for c in recall.call_args_list)
        assert personal.state(db)['arm']=='baseline', 'a losing reranker was promoted'
    db.close()
    print('PASS: cold evidence stays pending; warm evidence uses the actual automatic semantic route')


def verify_scoped_meaning():
    import sqlite3
    from brain import search, server
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
    for did,path in ((1,'/seed'),(2,'/a/route'),(3,'/b/route')):
        db.execute('INSERT INTO docs(id,path,name,project_id,source,body) VALUES(?,?,?,?,?,?)',
                   (did,path,'seed' if did==1 else 'route',path.split('/')[1],'memory','orchid'))
    db.commit()
    rows=[search._present(search._hydrate(db,did),1,[],None,[],db) for did in (1,2,3)]
    with patch.object(search,'recall',return_value=[rows[0]]) as recall, \
         patch.object(search,'query_weights',return_value=[('orchid',1,['orchid'])]), \
         patch.object(search,'union',return_value=([rows[0]],'')), \
         patch.object(vectors,'search',return_value=[(2,0.9,1.2),(3,0.8,1.1)]), \
         patch.object(vectors,'best_chunks',return_value={}), \
         patch.object(vectors,'_qcache_get',return_value=[1.0]), \
         patch.object(server,'_words_unsure',return_value=True), patch.object(server,'_note_recall'), \
         patch.object(personal,'apply',side_effect=lambda db,q,base,*a,**k:base) as apply:
        baseline=json.loads(server.call_tool(db,'recall',{'query':'orchid'}))
        assert {r['path'] for r in baseline['meaning']['rows']}=={'/a/route','/b/route'}
        apply.side_effect=None;apply.return_value=[rows[1]]
        ranked=json.loads(server.call_tool(db,'recall',{'query':'orchid'}))
        assert [r['path'] for r in ranked['results']]==['/a/route']
        assert [r['path'] for r in ranked['meaning']['rows']]==['/b/route'], 'same-name scope lost after reranking'
        # The initial meaning query must also exclude only the exact lexical path.
        recall.return_value=[rows[1]]
        with patch.object(server,'_words_unsure',return_value=False):
            initial=json.loads(server.call_tool(db,'recall',{'query':'orchid','semantic_k':3}))
        assert [r['path'] for r in initial['meaning']['rows']]==['/b/route'], 'same-name scope lost before reranking'
    db.close()
    print('PASS: actual MCP meaning preserves scoped homonyms and removes exact duplicates')


def verify_resolved_vector(real_semantic=False):
    from contextlib import nullcontext
    import array
    import sqlite3
    from brain import graphstore, observe, search, server, recalljson
    for case, ids in (('old_only',(2,)),('current_only',(3,)),('both',(2,3))):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db);vectors.ensure_table(db)
        for did,name,meta in ((1,'seed',{}),(2,'old',dict(topic='plant',facet='water',status='active')),
                              (3,'current',dict(topic='plant',facet='water',status='active',supersedes=['old']))):
            db.execute('INSERT INTO docs(id,name,path,source,sha,title,description,body,lifecycle) VALUES(?,?,?,?,?,?,?,?,?)',
                       (did,name,'/'+name,'memory','sha-'+str(did),'','', 'water',json.dumps(meta)))
        for did in ((1,)+ids if real_semantic else ids):
            db.execute('INSERT INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES(?,?,?,?,?,?)',
                       (did,0,'sha-'+str(did),'fixture',2,array.array('f',[0.2 if did==1 else 1.0 if did==2 else -1.0,0.98 if did==1 else 0.0]).tobytes()))
        # A same-name document in another scope must never replace the selected seed.
        db.execute("INSERT INTO docs(id,name,path,project_id,source,sha,body) VALUES(0,'seed','/other/seed','other','memory','other','water')")
        db.commit()
        base=[dict(name='seed',path='/seed',cosine=0.2)]
        clock=[0.0]
        def monotonic():
            clock[0]+=0.001
            return clock[0]
        def baseline_recall(*args,**kwargs):
            # Common synthetic retrieval work keeps this test about revision binding.
            # Dedicated boundary checks below validate the full serving cost.
            clock[0]+=1.0
            return [dict(r) for r in base]
        with patch.object(vectors,'_qcache_get',return_value=array.array('f',[1.0,0.0])), \
             patch.object(vectors,'model_tag',return_value='fixture'), patch.object(vectors,'DIM',2), \
             (nullcontext() if real_semantic else patch.object(search,'meaning',side_effect=lambda *a,**k:([dict(r) for r in base],''))), \
             patch.object(search,'query_weights',return_value=[('water',1,['water'])]), \
             patch.object(search,'recall',side_effect=baseline_recall), \
             patch.object(graphstore,'ask',return_value={1:{2}}) as neighbors, \
             patch.object(server,'_words_unsure',return_value=False), \
             patch.object(server,'_note_recall'), patch.object(observe,'write'), \
             patch.object(recalljson,'response',side_effect=lambda db,**kw:{'rows':kw['rows']}), \
             patch.object(personal.time,'monotonic',side_effect=monotonic):
            rows=personal.candidates(db,'water',base,'linked',8)
            assert neighbors.called and 1 in neighbors.call_args.args[2] and 0 not in neighbors.call_args.args[2], 'same-name seed crossed scope'
            current=[v for v in rows if v['name']=='current']
            assert bool(current)==(3 in ids), 'linked candidate version mismatch '+case
            if current:
                assert current[0]['cosine']==-1.0, 'old cosine attached to current body '+case
            for i in range(12):
                personal.record(db,'water '+str(i),'/current','revision-'+str(i),1)
            result=personal.update(db)
            assert result['arm'] in (('fusion','linked') if 3 in ids else ('baseline',)), ('measurement version mismatch',case,result,[dict(r) for r in db.execute('SELECT scores,costs FROM personal_events LIMIT 2')])
            served=json.loads(server.call_tool(db,'recall',{'query':'water live'}))['rows']
            current=[v for v in served if v['name']=='current']
            assert bool(current)==(3 in ids), ('serving version mismatch',case,real_semantic,personal.state(db),served)
            if current:
                assert current[0]['cosine']==-1.0, 'serving used the predecessor vector '+case
            if real_semantic and case=='old_only':
                db.execute('DELETE FROM vectors WHERE doc_id=1')
                db.commit()
                assert vectors.search(db,'retired only',k=8)==[], 'all-retired candidates must be empty'
                assert search.meaning(db,'retired only',8)==([],''), 'empty current pool is not a backend failure'
        db.close()
    print('PASS: old-only/current-only/both vectors bind to the resolved revision in measurement and serving')


def verify_retired_budget():
    import array
    import sqlite3
    from brain import search
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db);vectors.ensure_table(db)
    pinned={}
    for did in range(1,42):
        name='legacy_'+str(did);sha=('%040x'%did);pinned[name]=sha
        db.execute('INSERT INTO docs(id,name,path,source,sha,body) VALUES(?,?,?,?,?,?)',
                   (did,name,'/'+name,'memory',sha,'old arrangement'))
        db.execute('INSERT INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES(?,?,?,?,?,?)',
                   (did,0,sha,'fixture',2,array.array('f',[1.0,0.0]).tobytes()))
    meta=dict(topic='arrangement',facet='schedule',status='active',supersedes=list(pinned),legacy_whole_record_sha=pinned)
    db.execute('INSERT INTO docs(id,name,path,source,sha,body,lifecycle) VALUES(42,?,?,?,?,?,?)',
               ('current','/current','memory','current-sha','new arrangement',json.dumps(meta)))
    with patch.object(vectors,'_qcache_get',return_value=array.array('f',[1.0,0.0])), \
         patch.object(vectors,'model_tag',return_value='fixture'), patch.object(vectors,'DIM',2):
        assert vectors.search(db,'arrangement',k=8)==[], 'no current vector must stay empty'
        db.execute('INSERT INTO vectors(doc_id,chunk_no,sha,model,dim,vec) VALUES(42,0,?,?,?,?)',
                   ('current-sha','fixture',2,array.array('f',[0.6,0.8]).tobytes()))
        db.commit()
        hits=vectors.search(db,'arrangement',k=8)
        assert len(hits)==1 and hits[0][0]==42, 'retired legacy vectors exhausted the current candidate pool'
        assert abs(hits[0][1]-0.6)<0.0001 and abs(hits[0][2]-1.0)<0.0001, 'retired vectors polluted the reference distribution'
        rows,why=search.meaning(db,'arrangement',8)
        assert not why and len(rows)==1 and rows[0]['name']=='current' and rows[0]['cosine']==0.6
    db.close()
    print('PASS: 41 SHA-pinned legacy predecessors cannot exhaust k=8; absent current vector stays empty')


def verify_support_and_cost():
    import sqlite3
    from brain import lifecycle, search, server, observe, recalljson
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
    evidence(db,16,'fusion')
    db.execute('UPDATE docs SET lifecycle=? WHERE path=?',
               (json.dumps(dict(topic='plant',facet='water',status='active')),'fixture'));db.commit()
    with patch.object(vectors,'_qcache_get',return_value=[1.0]), patch.object(observe,'write'):
        assert personal.update(db)['arm']=='fusion'
        original=tuple(db.execute("SELECT id,sha,body,lifecycle FROM docs WHERE path='fixture'").fetchone())
        db.execute('INSERT INTO docs(path,name,source,sha,lifecycle) VALUES(?,?,?,?,?)',
                   ('successor','successor','memory','new',json.dumps(dict(topic='plant',facet='water',status='active',supersedes=['fixture']))));db.commit()
        assert not personal.certified(db,personal.state(db)), 'superseded support stayed certified'
        with patch.object(personal,'candidates',return_value=['reranked']) as candidate:
            assert personal.apply(db,'plant',['baseline'],8)==['baseline']
            assert not candidate.called
        evidence(db,16,'fusion',16)
        personal.record(db,'fresh old read','fixture','pending-retired',1)
        assert personal.update(db)['arm']=='baseline', 'superseded support recertified'
        assert db.execute("SELECT measured FROM personal_events WHERE session='pending-retired'").fetchone()[0]==-1
        assert tuple(db.execute("SELECT id,sha,body,lifecycle FROM docs WHERE path='fixture'").fetchone())==original
        with lifecycle.view('history'):
            assert original[0] in lifecycle.snapshot(db).resolve(original[0])
    db.close()
    for extra,gate,cache_cost,expected in ((0.0005,0,0,'fusion'),(0.005,0,0,'baseline'),(0.0005,0.002,0,'baseline'),(0.0005,0,0.002,'baseline')):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db);evidence(db,16,'fusion')
        live=[False];clock=[0.0];base=[dict(name='baseline',path='/baseline')];ranked=[dict(name='reranked',path='/reranked')]
        real_certified=personal.certified
        def certify(*args,**kwargs):
            clock[0]+=gate if live[0] else 0
            return real_certified(*args,**kwargs)
        def cached(*args,**kwargs):
            clock[0]+=cache_cost if live[0] else 0
            return [1.0]
        def recall(*args,**kwargs):
            clock[0]+=0.010
            return base
        def candidate(*args,**kwargs):
            clock[0]+=extra
            return ranked
        with patch.object(vectors,'_qcache_get',side_effect=cached), patch.object(personal,'certified',side_effect=certify), patch.object(observe,'write'), \
             patch.object(personal.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(search,'recall',side_effect=recall), patch.object(server,'_words_unsure',return_value=False), \
             patch.object(server,'_note_recall'), patch.object(personal,'candidates',side_effect=candidate), \
             patch.object(recalljson,'response',side_effect=lambda db,**kw:{'rows':kw['rows']}):
            assert personal.update(db)['arm']=='fusion'
            live[0]=True
            served=json.loads(server.call_tool(db,'recall',{'query':'plant'}))['rows']
            assert personal.state(db)['arm']==expected, ('total serving cost not enforced',extra)
            assert served==(ranked if expected=='fusion' else base)
        db.close()

    # A challenger must pay validation/cache costs when it is certified, too.
    for gate_cost,cache_cost in ((0.002,0),(0,0.002)):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db);evidence(db,16,'fusion')
        clock=[0.0];real_certified=personal.certified
        def certify(*args,**kwargs):
            clock[0]+=gate_cost
            return real_certified(*args,**kwargs)
        def cached(*args,**kwargs):
            clock[0]+=cache_cost
            return [1.0]
        with patch.object(personal,'certified',side_effect=certify), patch.object(vectors,'_qcache_get',side_effect=cached), \
             patch.object(personal.time,'monotonic',side_effect=lambda:clock[0]), patch.object(observe,'write'):
            assert personal.update(db)['arm']=='baseline', 'certification omitted validation/cache cost'
        db.close()
    print('PASS: superseded support revokes training and serving; total baseline plus rerank cost matches certification')


def main():
    # These policy/support fixtures supply synthetic 10ms scores, so validation
    # time must also be synthetic. Wall-clock cost boundaries are checked below.
    import itertools
    ticks = itertools.count(0, 0.000001)
    clock = patch.object(personal.time, 'monotonic', side_effect=lambda:next(ticks))
    clock.start()
    cache = patch.object(vectors, "_qcache_get", return_value=[1.0])
    cache.start()
    db = store.connect()
    assert personal.state(db)['arm'] == 'baseline'
    assert personal.update(db)['decision'] == 'insufficient'
    # Too few wins cannot promote, despite every observed outcome preferring a challenger.
    evidence(db, 2, 'fusion')
    assert personal.update(db)['arm'] == 'baseline'
    evidence(db, 14, 'fusion', 2)
    assert personal.update(db)['arm'] == 'fusion'
    assert personal.update(db)['decision'] == 'insufficient'  # consumed evidence cannot add confidence
    assert personal.state(db, 'terms')['arm'] == 'baseline'  # contexts cannot share a winner
    original = personal.candidates
    personal.candidates = lambda *args, **kwargs: ['candidate']
    os.environ['BRAIN_PERSONAL_MODE'] = 'observe'
    assert personal.apply(db, 'fixture', ['baseline'], 8) == ['baseline']
    del os.environ['BRAIN_PERSONAL_MODE']
    with patch.object(vectors, '_qcache_get', return_value=None):
        assert personal.apply(db, 'fixture', ['baseline'], 8) == ['baseline']
    assert personal.apply(db, 'fixture', ['baseline'], 8) == ['candidate']
    os.environ['BRAIN_PERSONAL_DISABLED'] = '1'
    assert personal.apply(db, 'fixture', ['baseline'], 8) == ['baseline']
    del os.environ['BRAIN_PERSONAL_DISABLED']
    personal.candidates = original
    evidence(db, 16, 'baseline', 16)
    assert personal.update(db)['arm'] == 'baseline'  # new independent harm rolls back
    # A candidate winning training and losing holdout must fail.
    evidence(db, 8, 'linked', 32)
    evidence(db, 8, 'baseline', 40)
    result = personal.update(db)
    assert result['arm'] == 'baseline'
    assert personal.state(db)['history'][-1]['reason'] == 'holdout_rejected'
    # Editing a turn withdraws its behavioral signal.
    personal.edited(db, 'session-47', 1)
    assert db.execute('SELECT edited FROM personal_events WHERE identity=?', (personal.evidence_prefix()+'47',)).fetchone()[0] == 1
    # Another user's home starts with no inherited policy or observations.
    os.environ['BRAIN_HOME'] = str(Path(tmp.name)/'another')
    other = store.connect()
    assert personal.state(other)['arm'] == 'baseline'
    assert personal.update(other)['observations'] == 0
    # A previous collector's records cannot bootstrap the new automatic policy.
    evidence(other, 16, 'fusion')
    other.execute("UPDATE personal_events SET identity='legacy-' || id")
    other.commit()
    assert personal.update(other)['arm'] == 'baseline'
    evidence(other, 16, 'fusion', 16)
    assert personal.update(other)['arm'] == 'fusion'
    assert personal.describe(other)['contexts']['query']['effective'] == 'fusion'
    personal.edited(other, 'session-20', 1)
    assert personal.apply(other, 'fixture', ['baseline'], 8) == ['baseline']
    assert personal.state(other)['history'][-1]['reason'] == 'support_invalidated'
    evidence(other, 16, 'fusion', 32)
    assert personal.update(other)['arm'] == 'fusion'
    selected = personal.state(other)
    # A changed measurement/embedding signature revokes serving even if all files remain.
    selected['signature'] = ['obsolete']
    store.set_meta(other, personal.KEY, json.dumps(selected))
    assert personal.apply(other, 'fixture', ['baseline'], 8) == ['baseline']
    evidence(other, 16, 'fusion', 48)
    assert personal.update(other)['arm'] == 'fusion'
    selected = personal.state(other)
    selected['cost_limit'] = 1e-12
    store.set_meta(other, personal.KEY, json.dumps(selected))
    personal.candidates = lambda *args, **kwargs: ['candidate']
    assert personal.apply(other, 'fixture', ['baseline'], 8) == ['baseline']
    assert personal.state(other)['history'][-1]['reason'] == 'serving_cost'
    personal.candidates = original
    assert personal.eligible({})
    for request in ({'view':'history'}, {'as_of':'2026-01-01'}, {'scope':'p'},
                    {'k':4}, {'semantic_k':0}, {'semantic_k':20}, {'sources':['x']}):
        assert not personal.eligible(request), request
    other.close(); db.close()
    clock.stop()
    cache.stop()
    verify_support_and_cost()
    verify_retired_budget()
    verify_semantic_route()
    verify_scoped_meaning()
    verify_resolved_vector()
    verify_resolved_vector(real_semantic=True)
    print('PASS: weak evidence retained, independent holdout promoted, drift rolled back, users isolated')

if __name__ == '__main__':
    main()
