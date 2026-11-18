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
            db.execute('INSERT INTO personal_events(identity,session,turn,query,path,sha,exposed,measured,scores,costs,settled) VALUES(?,?,?,?,?,?,?,?,?,?,1)',
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
            personal.settle(db,'route-session-'+str(i),1)
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


def verify_request_replay_order():
    import sqlite3
    from brain import observe, server
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
    terms=['zebra','amber','cedar','ivory']
    for i,word in enumerate(terms):
        store._index_one(db,dict(name='fixture'+str(i+1),path='/fixture'+str(i+1),source='memory',kind='reference',title='',description='',body=word,mtime=1,size=len(word),sha='sha'+str(i),links=[]))
    store._rebuild_terms(db);db.commit()
    def recall(args):
        return [r['name'] for r in json.loads(server.call_tool(db,'recall',args,_measure=True))['results'] if not r.get('related')][:3]
    with patch.object(observe,'write'),patch.object(server,'_words_unsure',return_value=False):
        for words in (terms,terms+[terms[0]],list(reversed(terms))):
            args=dict(query='unmatchedquery',terms=words,k=8)
            original=recall(args)
            personal.record(db,args['query'],'/fixture1','ordered-session',1,terms=words)
            row=dict(db.execute('SELECT * FROM personal_events ORDER BY id DESC LIMIT 1').fetchone())
            assert json.loads(row['terms'])==words,'stored terms lost order or multiplicity'
            assert recall(personal._event_request(row))==original,'replay changed top-three boundary'
        assert recall(dict(query='unmatchedquery',terms=terms)) != recall(dict(query='unmatchedquery',terms=sorted(terms))), 'fixture did not exercise a meaningful tie boundary'
    db.close()
    print('PASS: actual lexical recall and stored-request replay preserve top-three ties, term order and multiplicity')


def verify_malformed_policy_view():
    import sqlite3
    from brain import dashboard, dashview, scorecard
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
    db.execute("INSERT INTO docs(path,name,source,body) VALUES('/kept','kept_memory','memory','Preserved body')");db.commit()
    broken=[[],None,'text',1,True,{'arm':'fusion','support':None}, {'arm':'baseline','history':{}}, {'arm':'baseline','history':[1]}, {'arm':'baseline','after':'bad'}]
    valid=dict(arm='fusion',after=0,history=[],support=[1],signature=personal.signature(),cost_limit=.1)
    broken += [dict(valid,cost_limit=c) for c in (float('nan'),float('inf'),-1,True,'fast')]
    broken += [dict(valid,support=v) for v in ('ids',{},[True],[1,1],[0])]
    with patch.object(scorecard,'history',return_value=[]):
        for value in broken:
            for key in (personal.KEY,personal.KEY+'_terms'):store.set_meta(db,key,json.dumps(value))
            db.commit();before='\n'.join(db.iterdump())
            data=dashboard.payload(db);html=dashview.render(data)
            assert len(data['memories'])==1 and 'kept_memory' in html
            assert data['performance'] is None
            assert all(s['effective']=='baseline' for s in data['policy']['contexts'].values())
            assert before=='\n'.join(db.iterdump()),'policy fallback rewrote the index'
    db.close()
    print('PASS: malformed scalar/object policy states retain readonly memory/performance views and baseline')


def verify_serving_cache_fallback():
    import array, hashlib, sqlite3
    from brain import observe, search, server
    for mode in ('query','terms'):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
        with patch.object(observe,'write'),patch.object(vectors,'DIM',2),patch.object(vectors,'query_tag',return_value='fixture-cache'),patch.object(personal.time,'monotonic',return_value=1):
            vectors._qcache_get(db,'fixture')
            vectors._qcache_put(db,'fixture',array.array('f',[1,0]))
            evidence(db,12,'fusion')
            if mode=='terms':db.execute("UPDATE personal_events SET terms='[\"orchid\"]'");db.commit()
            assert personal.update(db,mode)['arm']=='fusion'
            selected=personal.state(db,mode);assert personal.certified(db,selected)
            key=personal.KEY+('_terms' if mode=='terms' else '')
            row=dict(name='fixture',path='fixture',source='memory',kind='reference',description='fixture',title='fixture',age_days=0,stale=False,why='fixture',excerpt='orchid',related=False,score=1)
            args=dict(query='fixture',terms=['orchid'] if mode=='terms' else [],format='json')
            with patch.object(search,'recall',return_value=[row]),patch.object(server,'_words_unsure',return_value=False),patch.object(server,'_note_recall'),patch.object(vectors,'embed',side_effect=AssertionError('no embedding requested')):
                for blob in (b'x','not a blob',array.array('f',[1]).tobytes(),array.array('f',[float('nan'),0]).tobytes(),array.array('f',[float('inf'),0]).tobytes()):
                    db.execute('UPDATE vec_qcache SET vec=?',(blob,));db.commit()
                    with patch.object(personal,'candidates',side_effect=AssertionError('bad cache reranked')):
                        answer=json.loads(server.call_tool(db,'recall',args))
                    assert answer['results'][0]['name']=='fixture','corrupt cache lost baseline'
                    assert vectors._qcache_get(db,'fixture') is None
                    with patch.object(server,'_words_unsure',return_value=True),patch.object(search,'union',return_value=([row],'semantic unavailable')),patch.object(search,'meaning',return_value=([],'semantic unavailable')):
                        assert json.loads(server.call_tool(db,'recall',args))['results'][0]['name']=='fixture','uncertain route lost baseline'
                vectors._qcache_put(db,'fixture',array.array('f',[1,0]))
                with patch.object(personal,'candidates',return_value=[row]) as healthy:
                    assert json.loads(server.call_tool(db,'recall',args))['results'][0]['name']=='fixture'
                    assert healthy.called,'healthy certified cache never applied'
                for stage in ('state','certified','candidates','_qcache_get'):
                    store.set_meta(db,key,json.dumps(selected))
                    module=vectors if stage=='_qcache_get' else personal
                    with patch.object(module,stage,side_effect=ValueError('optional stage failed')),patch.object(personal,'_save',side_effect=sqlite3.OperationalError('rollback unavailable')):
                        assert json.loads(server.call_tool(db,'recall',args))['results'][0]['name']=='fixture',stage
        db.close()
    print('PASS: certified public recall retains baseline for malformed caches and optional policy failures, even if rollback cannot persist; healthy cache still applies')


def verify_rejected_observations(check_collection=True, modes=('query','terms')):
    import array, hashlib, sqlite3
    from brain import hosts, observe, recalljson, search, server, tail
    hosts.pin('claude-code')
    def user(text):return dict(type='user',message=dict(role='user',content=text))
    def tool(tid,name,args):return dict(type='assistant',message=dict(content=[dict(type='tool_use',id=tid,name=name,input=args)]))
    def result(tid,text):return dict(type='user',message=dict(content=[dict(type='tool_result',tool_use_id=tid,content=text)]))
    bad_requests = [dict(query='cached query',terms=['term'+str(i) for i in range(101)]),
                    dict(query='cached query',terms='words'), dict(query='cached query',terms={'x':1}),
                    dict(query='x'*24001), dict(query=' '), {}, dict(query='cached query',k=False),
                    dict(query='cached query',format='invalid'), dict(query='cached query',scope='')]
    if check_collection:
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db);personal.ensure(db)
        db.execute("INSERT INTO docs(path,name,source,sha,body) VALUES('/valid','valid','memory','sha','orchid')");db.commit()
        with patch.object(tail,'_db',return_value=db),patch.object(tail,'_close_db'),patch.object(observe,'write'):
            for i,args in enumerate(bad_requests):
                answer=server.call_tool(db,'recall',args)
                assert json.loads(answer)['error']['code']=='invalid_argument'
                rec={'session_id':'rejected'+str(i)};trace=tail._blank_tail()
                for event in (user('request'),tool('bad','mcp__brain__recall',args),result('bad',answer),
                              tool('read','Read',{'file_path':'/valid'}),result('read','orchid'),user('next real turn')):
                    tail._apply(json.dumps(event),trace,rec)
                assert db.execute('SELECT COUNT(*) FROM personal_events').fetchone()[0]==0, 'rejected recall entered observations'
                if 'terms' in args or not args.get('query','').strip() or len(args.get('query',''))>24000:
                    personal.record(db,args.get('query'),'/valid','direct'+str(i),1,terms=args.get('terms',[]))
                assert db.execute('SELECT COUNT(*) FROM personal_events').fetchone()[0]==0, 'rejected recall entered observations'
                for event in (tool('good-read','Read',{'file_path':'/valid'}),result('good-read','orchid'),user('confirmed')):
                    tail._apply(json.dumps(event),trace,rec)
                assert db.execute('SELECT query,settled FROM personal_events').fetchone()[:] == ('next real turn',1)
                db.execute('DELETE FROM personal_events');db.commit()
        db.close()
    original_call=server.call_tool
    for mode in modes:
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db);personal.ensure(db)
        db.execute("INSERT INTO docs(path,name,source,sha,body) VALUES('/valid','valid','memory','sha','orchid')");db.commit()
        oversized=json.dumps(['term'+str(i) for i in range(101)])
        malformed=[('cached query',oversized),('cached query','{'),('cached query','"words"'),(' ','[]'),('cached query',None),('cached query','')]
        for i,(query,terms) in enumerate(malformed):
            db.execute('INSERT INTO personal_events(identity,session,turn,query,path,sha,exposed,terms,settled) VALUES(?,?,?,?,?,?,0,?,1)',
                       (personal.evidence_prefix()+'bad'+str(i),'bad'+str(i),1,query,'/valid','sha',terms))
        db.commit()
        def replay(db,name,args,**kw):
            if args['query']=='degraded exception':
                raise ValueError('one replay failed')
            values={'degraded missing':None,'degraded nan':float('nan'),'degraded negative':-1,'degraded text':'fast'}
            if args['query'] in values:
                value=values[args['query']]
                if value is not None:kw['_retrieval']['baseline_seconds']=value
                return '{}'
            return original_call(db,name,args,**kw)
        def candidate(db,q,base,arm,*a,**kw):
            if q=='degraded candidate':raise ValueError('one candidate failed')
            return base if arm=='baseline' else [dict(path='/valid')]
        with patch.object(vectors,'query_tag',return_value='fixture-cache'),patch.object(observe,'write'), \
             patch.object(search,'recall',return_value=[]),patch.object(search,'meaning',return_value=([],'')), \
             patch.object(server,'_words_unsure',return_value=True),patch.object(server,'call_tool',side_effect=replay), \
             patch.object(personal,'candidates',side_effect=candidate), \
             patch.object(vectors,'embed',side_effect=AssertionError('measurement requested an embedding')):
            vectors._qcache_get(db,'cached query')
            vectors._qcache_put(db,'cached query',array.array('f',[1.0]*vectors.DIM))
            for query in ('degraded missing','degraded nan','degraded negative','degraded text','degraded exception','degraded cache','degraded candidate'):
                personal.record(db,query,'/valid',query,1,terms=['term'] if mode=='terms' else [])
                personal.settle(db,query,1)
                vectors._qcache_put(db,query,array.array('f',[1.0]*vectors.DIM))
                if query=='degraded cache':
                    db.execute('UPDATE vec_qcache SET vec=? WHERE qsha=?',(b'x',hashlib.sha1(query.encode()).hexdigest()));db.commit()
                    assert vectors._qcache_get(db,query) is None, 'corrupt cache was not a miss'
            for i in range(12):
                query='valid query '+str(i);sid='valid'+str(i)
                personal.record(db,query,'/valid',sid,1,terms=['term'] if mode=='terms' else [])
                personal.settle(db,sid,1)
                vectors._qcache_put(db,query,array.array('f',[1.0]*vectors.DIM))
            assert personal.update(db,mode)['observations']==12
            assert db.execute("SELECT COUNT(*) FROM personal_events WHERE session LIKE 'bad%' AND measured=-1").fetchone()[0]==6
            assert db.execute("SELECT COUNT(*) FROM personal_events WHERE session LIKE 'valid%' AND measured=1").fetchone()[0]==12
            assert db.execute("SELECT COUNT(*) FROM personal_events WHERE session LIKE 'degraded%' AND measured=0").fetchone()[0]==7
        db.close()
    print('PASS: rejected requests never enter learning; malformed persisted rows are excluded and degraded replays cannot block valid observations in either mode')


def verify_turn_edit_order():
    import sqlite3
    from brain import hosts, observe, server, tail
    hosts.pin('claude-code')
    def tool(tid,name,args):
        return {'type':'assistant','message':{'content':[{'type':'tool_use','id':tid,'name':name,'input':args}]}}
    for order in ('edit_read','read_edit','remember_read','read_remember','shell_read','read_shell','legacy_read','preview_read','read_only'):
        is_edited = order not in ('read_only','preview_read')
        path=Path(tmp.name)/(order+'.db')
        db=sqlite3.connect(str(path));db.row_factory=sqlite3.Row;store._init(db)
        db.execute("INSERT INTO docs(path,name,source,sha,body) VALUES('/note','note','memory','sha','orchid')");db.commit()
        def baseline(db,name,args,**kwargs):
            kwargs['_retrieval']['baseline_seconds']=0.01
            return '{}'
        with patch.object(tail,'_db',side_effect=lambda:db), patch.object(observe,'write'), \
             patch.object(vectors,'_qcache_get',return_value=[1.0]), \
             patch.object(server,'call_tool',side_effect=baseline), \
             patch.object(personal,'candidates',side_effect=lambda db,q,base,arm,*a,**k:[] if arm=='baseline' else [dict(path='/note')]), \
             patch.object(personal.time,'monotonic',return_value=0):
            for i in range(12):
                rec={'session_id':order+str(i)}; trace=tail._blank_tail()
                def feed(value):tail._apply(json.dumps(value),trace,rec)
                feed({'type':'user','message':{'role':'user','content':'orchid '+order+str(i)}})
                if order=='legacy_read':
                    rec['use'].pop('collector_version')  # old saved offset, earlier edits unknown
                edit=tool('edit','Edit',{'file_path':'/code.py','old_string':'old','new_string':'new'})
                if 'remember' in order or order=='preview_read':
                    edit=tool('save','mcp__brain__remember',{'name':'note','body':'new','prepare_only':order=='preview_read'})
                if 'shell' in order:
                    edit=tool('shell','Bash',{'command':"printf changed >> /note"})
                def edited_event():
                    feed(edit)
                    if 'shell' in order:
                        feed({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'shell','content':'done'}]}})
                if order in ('edit_read','remember_read','preview_read','shell_read'):
                    edited_event()
                    # Persisted turn state survives a new connection and resumed transcript.
                    db.close();db=sqlite3.connect(str(path));db.row_factory=sqlite3.Row
                    rec=json.loads(json.dumps(rec))
                feed(tool('read','Read',{'file_path':'/note'}))
                feed({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'read','content':'orchid'}]}})
                if order in ('read_edit','read_remember','read_shell'):edited_event()
                feed({'type':'user','message':{'role':'user','content':'next independent turn'}})
            rows=db.execute('SELECT edited FROM personal_events').fetchall()
            assert len(rows)==12 and all(bool(r[0])==is_edited for r in rows), (order,[tuple(r) for r in rows])
            result=personal.update(db)
            assert result['arm']==('fusion' if order=='read_only' else 'baseline'), (order,result)
            if order=='preview_read':
                assert all(r[0] for r in db.execute('SELECT exposed FROM personal_events'))
            if order=='read_only':
                assert personal.certified(db,personal.state(db))
                db.execute("UPDATE personal_events SET identity='v14:' || substr(identity,5)");db.commit()
                assert not personal.certified(db,personal.state(db)), 'previous collector remained certified'
                assert personal.update(db)['arm']=='baseline'
            else:
                personal.record(db,'next turn','/note',order+'0',2)
                personal.record(db,'another session','/note','unrelated',1)
                assert all(not r[0] for r in db.execute('SELECT edited FROM personal_events WHERE query IN (?,?)',('next turn','another session')))
        db.close()
    print('PASS: actual transcript edit/save/shell read orders are excluded; clean reads promote; turn veto survives restart and stays isolated; v14 support rejected')


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
                personal.settle(db,'revision-'+str(i),1)
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


def verify_exposure_parity(formats=('text','json'), lanes=('direct','related','meaning','hook_direct','hook_related','hook_meaning'), delays=(0,10), name='known note', context_tool=None, expected_exposed=True, history_language=None):
    import sqlite3
    from brain import hosts, observe, server, tail, recalljson, hook, i18n, lifecycle
    hosts.pin('claude-code')
    for fmt in formats:
      for lane in lanes:
       for delay in delays:
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
        db.execute("INSERT INTO docs(path,name,source,sha,body) VALUES('/shown-note',?,'memory','sha','orchid')",(name,));db.commit()
        row=dict(name=name,path='/shown-note',source='memory',kind='reference',description='fixture',title='fixture',
                 age_days=0,stale=False,why='fixture',excerpt='orchid',related=lane in ('related','hook_related'),via_vector=lane=='hook_meaning',cosine=.8,via_graph='seed',score=1)
        if lane=='history':
            current=dict(topic='history-fixture',facet='value',status='active',supersedes=['past_note'])
            old=dict(topic='history-fixture',facet='value',status='superseded',superseded_by=name)
            db.execute('UPDATE docs SET lifecycle=? WHERE path=?',(json.dumps(current),'/shown-note'))
            db.execute("INSERT INTO docs(path,name,source,sha,body,lifecycle) VALUES('/past-note','past_note','memory','old-sha','previous value',?)",(json.dumps(old),));db.commit()
            old_id=db.execute("SELECT id FROM docs WHERE path='/past-note'").fetchone()[0]
            row.update(name='past_note',path='/past-note',lifecycle=lifecycle.snapshot(db).info(old_id))
            assert row['lifecycle']['current_names']==[name]
        rows=[] if lane=='meaning' else [row];meaning=[row] if lane=='meaning' else []
        if fmt=='text':
            with patch.object(i18n,'_lang',history_language or 'en'):
                answer=server._fmt_recall(rows)
                if meaning:answer+='\n'+server._fmt_meaning(meaning,'','cached',1)
        else:
            answer=json.dumps(recalljson.response(db,query='orchid',terms=[],k=8,rows=rows,mrows=meaning,why_none='',
                 asked_meaning=8 if meaning else 0,auto_meaning=True,request_id=None,unsure=False,bridged=[],
                 reordered=False,embed_cached=True,t0=0,shown=800))
        context_args = {'memory_history': {'name': name}, 'review_identity': {'names': [name]},
                        'memory_review': {'only_attention': False}, 'neighbors': {'name': name},
                        'timeline': {}, 'brain_analyse': {'limit': 10}, 'brain_status': {},
                        'brain_suggest': {}, 'brain_capabilities': {}}.get(context_tool, {})
        if context_tool == 'brain_suggest':
            db.execute("INSERT INTO usage(path,wrong) VALUES('/shown-note',2)");db.commit()
        if context_tool == 'timeline':
            db.execute("UPDATE docs SET mtime=?",(__import__('time').time(),));db.commit()
        if context_tool:
            answer = (server.call_tool(db, context_tool, context_args) if context_tool != 'future_context'
                      else json.dumps({'body': 'orchid'}))
        def user(t):return dict(type='user',message=dict(role='user',content=t))
        def tool(tid,name,args):return dict(type='assistant',message=dict(content=[dict(type='tool_use',id=tid,name=name,input=args)]))
        def result(tid,text):return dict(type='user',message=dict(content=[dict(type='tool_result',tool_use_id=tid,content=text)]))
        def baseline(db,name,args,**kw):kw['_retrieval']['baseline_seconds']=.01;return '{}'
        with patch.object(tail,'_db',return_value=db),patch.object(tail,'_close_db'),patch.object(observe,'write'), \
             patch.object(vectors,'_qcache_get',return_value=[1.0]),patch.object(server,'call_tool',side_effect=baseline), \
             patch.object(personal,'candidates',side_effect=lambda db,q,base,arm,*a,**k:base if arm=='baseline' else [dict(path='/shown-note')]), \
             patch.object(personal.time,'monotonic',return_value=0):
            for i in range(12):
                sid=fmt+lane+str(delay)+'-'+str(i);events=[user('orchid '+sid)]
                if i==11:
                    if lane.startswith('hook_'):
                        for language in i18n.LANGS:
                            with patch.object(i18n,'_lang',language):rendered=hook._format([row])
                            assert tail._names(db,rendered,tail._HOOK_LINE)==[name],(lane,language)
                        events.append(dict(type='attachment',attachment=dict(type='hook_additional_context',content=[rendered])))
                    else:
                        request=context_args if context_tool else dict(query='orchid '+sid,format=fmt)
                        if lane=='history':request['view']='history'
                        events.extend([tool('recall','mcp__brain__'+(context_tool or 'recall'),request),result('recall',answer)])
                        if lane=='history':events.append(user('after history'))
                    for j in range(delay):
                        events.extend([user('later '+str(j)),tool('empty'+str(j),'mcp__brain__recall',dict(query='empty'+str(j))),result('empty'+str(j),'{}')])
                events.extend([tool('read','Read',dict(file_path='/shown-note')),result('read','orchid'),user('next actual turn')])
                path=Path(tmp.name)/(sid+'.jsonl');path.write_bytes(('\r\n'.join(json.dumps(e) for e in events)+'\r\n').encode())
                tail.scan(str(path),dict(session_id=sid,offset=0,tail=tail._blank_tail(),saves=0))
            observed=db.execute('SELECT exposed FROM personal_events WHERE session=?',(sid,)).fetchone()[0]
            assert personal.update(db)['arm']==('baseline' if expected_exposed else 'fusion'),(context_tool,fmt,lane,delay,'shown memory promoted a policy')
            assert observed==int(expected_exposed),(context_tool,fmt,lane,delay,'shown memory recorded as independent')
            assert personal.certified(db,personal.state(db)) is (not expected_exposed)
            assert (personal.apply(db,'orchid',['baseline'],8)==['baseline']) is expected_exposed
            # A genuinely independent session can still supply the twelfth clean vote.
            personal.record(db,'fresh independent','/shown-note','independent-control',1)
            personal.settle(db,'independent-control',1)
            assert personal.update(db)['arm']=='fusion',(fmt,lane,delay,'clean control was lost')
        db.close()
    print('PASS: real text/JSON direct, related, meaning and hook exposure all exclude reads, including after rank-window eviction')


def verify_unsettled_evidence(cases=('unfinished','failed_veto','compact_summary','meta','sidechain')):
    import sqlite3
    from brain import hosts, observe, server, tail
    hosts.pin('claude-code')
    for case in cases:
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
        db.execute("INSERT INTO docs(path,name,source,sha,body) VALUES('/note','note','memory','sha','orchid')");db.commit()
        def user(text):return dict(type='user',message=dict(role='user',content=text))
        def tool(tid,name,args):return dict(type='assistant',message=dict(content=[dict(type='tool_use',id=tid,name=name,input=args)]))
        def baseline(db,name,args,**kw):kw['_retrieval']['baseline_seconds']=0.01;return '{}'
        with patch.object(tail,'_db',return_value=db), patch.object(tail,'_close_db'), patch.object(observe,'write'), \
             patch.object(vectors,'_qcache_get',return_value=[1.0]), patch.object(server,'call_tool',side_effect=baseline), \
             patch.object(personal,'candidates',side_effect=lambda db,q,base,arm,*a,**k:base if arm=='baseline' else [dict(path='/note')]), \
             patch.object(personal.time,'monotonic',return_value=0):
            records=[]
            for i in range(12):
                sid=case+str(i);path=Path(tmp.name)/(sid+'.jsonl')
                events=[user('orchid '+sid),tool('read','Read',{'file_path':'/note'}),
                        dict(type='user',message=dict(content=[dict(type='tool_result',tool_use_id='read',content='orchid')]))]
                if case!='unfinished':
                    if i==11:
                        if case!='failed_veto':
                            synthetic=user('Host-generated continuation context')
                            synthetic[dict(compact_summary='isCompactSummary',meta='isMeta',sidechain='isSidechain')[case]]=True
                            events.append(synthetic)
                        events.append(tool('shell','Bash',{'command':'python3 change.py'}))
                    events.append(user('next turn'))
                path.write_bytes(('\r\n'.join(json.dumps(e) for e in events)+'\r\n').encode())
                rec=dict(session_id=sid,offset=0,tail=tail._blank_tail(),saves=0)
                if case=='failed_veto' and i==11:
                    with patch.object(personal,'edited',side_effect=sqlite3.OperationalError('locked')):tail.scan(str(path),rec)
                    assert rec['offset']<path.stat().st_size
                else:tail.scan(str(path),rec)
                records.append((path,rec))
            assert personal.update(db)['arm']=='baseline',case+' certified an unfinished turn'
            assert personal.apply(db,'orchid',['baseline'],8)==['baseline']
            assert not personal.certified(db,personal.state(db))
            # Even premeasured rows must pass the settled-turn gate during serving.
            with db:
                db.execute("UPDATE personal_events SET measured=1,scores=?,costs=?",(json.dumps(dict(baseline=0,fusion=1,linked=1)),json.dumps(dict(baseline=.01,fusion=.01,linked=.01))))
            prospective=dict(signature=personal.signature(),support=[r[0] for r in db.execute('SELECT id FROM personal_events')],cost_limit=.02)
            assert not personal.certified(db,prospective),case+' certified premeasured pending evidence'
            for path,rec in records:
                if case=='unfinished':
                    with path.open('ab') as f:f.write((json.dumps(user('next turn'))+'\r\n').encode())
                tail.scan(str(path),json.loads(json.dumps(rec)))
            result=personal.update(db)
            assert result['arm']==('fusion' if case=='unfinished' else 'baseline'),(case,result)
            if case!='unfinished':
                assert db.execute('SELECT edited,settled FROM personal_events WHERE session=?',(case+'11',)).fetchone()[:]==(1,1)
                assert records[-1][1]['use']['turn']==(1 if case=='failed_veto' else 2), 'synthetic input advanced the turn'
            else:assert personal.certified(db,personal.state(db))
        db.close()
    print('PASS: EOF is not turn completion; failed-veto retry cannot train or certify; next user confirms clean turns')


def verify_disputed_support():
    import sqlite3
    from brain import lifecycle, observe
    for preexisting in (False, True):
        db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row; store._init(db)
        evidence(db,16,'fusion')
        meta=dict(topic='plant',facet='water',status='active')
        db.execute('UPDATE docs SET lifecycle=? WHERE path=?',(json.dumps(meta),'fixture'));db.commit()
        # Lifecycle eligibility is independent of runner speed; cost gates have their own checks.
        with patch.object(personal.time,'monotonic',return_value=0.0), patch.object(vectors,'_qcache_get',return_value=[1.0]), patch.object(observe,'write'):
            if preexisting:
                assert personal.update(db)['arm']=='fusion'
                assert personal.certified(db,personal.state(db))
            db.execute('INSERT INTO docs(path,name,source,sha,lifecycle) VALUES(?,?,?,?,?)',
                       ('conflict','conflict','memory','different',json.dumps(meta)));db.commit()
            did=db.execute("SELECT id FROM docs WHERE path='fixture'").fetchone()[0]
            with lifecycle.view('current'):
                snap=lifecycle.snapshot(db)
                assert did in snap.resolve(did) and snap.info(did)['status']=='disputed'
            assert did not in personal._current_ids(db,{did})
            if preexisting:
                assert not personal.certified(db,personal.state(db))
            personal.record(db,'read disputed','fixture','disputed-pending',1)
            assert personal.update(db)['arm']=='baseline'
            assert db.execute("SELECT measured FROM personal_events WHERE session='disputed-pending'").fetchone()[0]==-1
            assert db.execute('SELECT COUNT(*) FROM docs').fetchone()[0]==2
        db.close()
    print('PASS: disputed memories remain retrievable but cannot train or retain a policy certificate')


def verify_support_and_cost():
    import sqlite3
    from brain import lifecycle, search, server, observe, recalljson
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
    evidence(db,16,'fusion')
    db.execute('UPDATE docs SET lifecycle=? WHERE path=?',
               (json.dumps(dict(topic='plant',facet='water',status='active')),'fixture'));db.commit()
    # Support revocation is a state check; the following cases measure cost explicitly.
    with patch.object(personal.time,'monotonic',return_value=0.0), patch.object(vectors,'_qcache_get',return_value=[1.0]), patch.object(observe,'write'):
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
    for name in ('known note', 'orchid [release]', 'orchid ← release',
                 'orchid [release] ← next [version]', 'orchid ←', 'orchid ← ←', 'orchid [',
                 ' orchid', '  orchid', '\torchid', 'orchid ', 'orchid\t',
                 '\u3000orchid', 'orchid\u3000', 'orchid\nrelease', '\norchid', 'orchid\n',
                 '  orchid ←\n[release]  '):
        verify_exposure_parity(name=name)
    from brain import hosts, server, i18n
    for language in i18n.LANGS:
        verify_exposure_parity(lanes=('history',),name='current_note',history_language=language)
    assert {tool['name'] for tool in server.TOOLS} <= hosts.BRAIN_TOOLS
    for tool in ('memory_history', 'review_identity', 'memory_review', 'neighbors',
                 'timeline', 'brain_analyse', 'brain_status', 'brain_suggest', 'future_context'):
        verify_exposure_parity(formats=('json',), lanes=('direct',), context_tool=tool)
    verify_exposure_parity(formats=('json',), lanes=('direct',), context_tool='brain_capabilities', expected_exposed=False)
    verify_unsettled_evidence()
    verify_disputed_support()
    verify_support_and_cost()
    verify_retired_budget()
    verify_semantic_route()
    verify_rejected_observations()
    verify_serving_cache_fallback()
    verify_request_replay_order()
    verify_malformed_policy_view()
    verify_turn_edit_order()
    verify_scoped_meaning()
    verify_resolved_vector()
    verify_resolved_vector(real_semantic=True)
    print('PASS: weak evidence retained, independent holdout promoted, drift rolled back, users isolated')

if __name__ == '__main__':
    main()
