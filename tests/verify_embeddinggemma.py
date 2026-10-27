#!/usr/bin/env python3
"""Local adapter contract and failure controls. Synthetic encoder; no retrieval-quality claim."""
import hashlib
import http.server
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))
from brain import embedplan


def main():
    with tempfile.TemporaryDirectory(prefix='brain-egm-') as folder:
        home = Path(folder)
        os.environ.update(BRAIN_HOME=str(home/'brain'), BRAIN_CONFIG=str(home/'config.json'),
                          BRAIN_VECTOR_STORE='sqlite', BRAIN_GRAPH_STORE='sqlite',
                          BRAIN_EMBED_PROVIDER='embeddinggemma', BRAIN_EMBED_CHUNK_TOKENS='128')
        manifest = {'model': 'google/embeddinggemma-300m', 'format': embedplan.VERSION,
                    'max_tokens': 2048, 'space': 'a'*64, 'max_batch': 4}
        mf = home/'manifest.json'
        mf.write_text(json.dumps(manifest), encoding='utf-8')
        os.environ['BRAIN_EMBED_MANIFEST'] = str(mf)
        spec = importlib.util.spec_from_file_location('egm_service', ROOT/'runtime/embeddinggemma/service.py')
        service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service)

        class Tokenizer:
            def encode(self, text, **kw):
                assert kw == {'add_special_tokens': True, 'truncation': False}
                return [0] * (len(text.encode('utf-8')) + 2)

        class Encoder:
            tokenizer = Tokenizer()
            calls = 0
            seen = []
            def encode(self, texts, **kw):
                assert kw['prompt'] == ''
                self.calls += 1
                self.seen.extend(texts)
                out = []
                for text in texts:
                    raw = hashlib.sha256(text.encode('utf-8')).digest()
                    out.append([(raw[i % len(raw)] + 1)/256 for i in range(768)])
                return out

        model = Encoder()
        runtime = service.Runtime(model, manifest, str(home/'runtime.sqlite'))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), service.handler(runtime))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        os.environ['BRAIN_EMBED_BASE_URL'] = 'http://127.0.0.1:%d/v1' % server.server_address[1]
        try:
            from brain import localembed, vectors, store, search
            import _stores_fixture as fx
            client = localembed.client()
            count = runtime.count
            text = 'alpha\n\nbeta ' + '\uac00\ub098\ub2e4\U0001f600'*100 + '\n\ntail fact'
            plan = client.plan(text, 'A very long title '*40, 128)
            assert ''.join(r['text'] for r in plan) == text
            assert all(count(embedplan.formatted(r['text'], title=r['title'])) <= 128 for r in plan)
            assert plan[-1]['end'] == len(text)
            assert client.plan('', '', 128) == []
            # Control: oversized inputs must fail, rather than return a prefix vector.
            try:
                client.embed(['x'*3000], 768)
                raise AssertionError('silent truncation')
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            before = model.calls
            vectors.embed(['unique question'], side='query')
            assert model.seen[-1] == 'task: search result | query: unique question'
            vectors.embed(['unique question'], side='query')
            assert model.calls == before + 1
            short = client.embed(['unique question'], 256, side='query')[0]
            assert model.calls == before + 1  # MRL reuses the full cached embedding
            assert abs(sum(v*v for v in short)-1) < 1e-6
            # Runtime, not a forgiving caller, must reject a mismatched space.
            try:
                runtime.submit('embeddings', {'space': 'b'*64, 'input': ['x']})
                raise AssertionError('mixed spaces accepted')
            except ValueError:
                pass
            choice = dict(vectors._CHOICE, base_url='https://example.com/v1')
            try:
                localembed.Client(choice)
                raise AssertionError('non-local endpoint accepted')
            except localembed.LocalEmbeddingError:
                pass

            mem = fx.write_corpus(folder)
            fx.write_note(mem, 'long_note', ('first section.\n\n'*200)+'tail evidence')
            db = store.connect()
            store.reindex(db, recalibrate=False)
            row = db.execute('SELECT * FROM docs WHERE name="long_note"').fetchone()
            # A legacy primary key must migrate without losing its previously earned space.
            db.execute('CREATE TABLE IF NOT EXISTS vectors(doc_id INTEGER,chunk_no INTEGER,sha TEXT,model TEXT,dim INTEGER,vec BLOB,PRIMARY KEY(doc_id,chunk_no))')
            with db:
                db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?)',
                           (row['id'], 0, row['sha'], 'other-model', 3, vectors._unit([1,0,0]).tobytes()))
            legacy = sqlite3.connect(':memory:')
            legacy.execute('CREATE TABLE vectors(doc_id INTEGER,chunk_no INTEGER,sha TEXT,model TEXT,dim INTEGER,vec BLOB,PRIMARY KEY(doc_id,chunk_no))')
            legacy.execute("INSERT INTO vectors VALUES(1,0,'sha','old',3,X'0102')")
            vectors.ensure_table(legacy)
            legacy.execute("INSERT INTO vectors VALUES(1,0,'sha','new',3,X'0304')")
            assert legacy.execute('SELECT COUNT(*) FROM vectors').fetchone()[0] == 2
            legacy.close()
            store.set_meta(db, 'vec_min_ratio', '1.001')
            assert vectors.min_ratio(db) == vectors.MIN_RATIO_FALLBACK
            first = vectors.build(db, only_ids=[row['id']])
            assert first['chunks'] > 12
            assert db.execute('SELECT COUNT(*) FROM vectors WHERE model="other-model"').fetchone()[0] == 1
            calls = model.calls
            assert vectors.build(db, only_ids=[row['id']])['calls'] == 0
            assert model.calls == calls
            query = 'tail evidence'
            vectors.search(db, query)
            calls = model.calls
            vectors.search(db, query)
            assert model.calls == calls
            best = vectors.best_chunks(db, query, [row['id']])
            assert row['id'] in best
            chunk = best[row['id']][0]
            evidence = search.evidence(db, query, row, chunk_no=chunk)
            lo, hi = localembed.spans(db, row['id'])[chunk]
            assert lo <= evidence['start'] <= evidence['end'] <= hi
            # Insert content at the beginning: identical later inputs reuse earned vectors.
            fx.write_note(mem, 'long_note', 'new paragraph.\n\n'+('first section.\n\n'*200)+'tail evidence')
            store.reindex(db, recalibrate=False)
            changed = vectors.build(db, only_ids=[row['id']])
            assert changed['cache_hits'] > 0
            # A killed/failed batch must checkpoint reusable work without publishing half a document.
            fx.write_note(mem, 'resume_note', '\n\n'.join('checkpoint section %d %s' % (i, 'x'*30) for i in range(30)))
            store.reindex(db, recalibrate=False)
            resume = db.execute('SELECT * FROM docs WHERE name="resume_note"').fetchone()
            original = client.embed
            attempts = [0]
            def interrupt(*args, **kwargs):
                attempts[0] += 1
                if attempts[0] == 2:
                    raise RuntimeError('injected interruption')
                return original(*args, **kwargs)
            client.embed = interrupt
            try:
                vectors.build(db, only_ids=[resume['id']])
                raise AssertionError('control did not interrupt')
            except RuntimeError as exc:
                assert str(exc) == 'injected interruption'
            finally:
                client.embed = original
            assert db.execute('SELECT COUNT(*) FROM vectors WHERE doc_id=? AND model=?',
                              (resume['id'], vectors.model_tag())).fetchone()[0] == 0
            resumed = vectors.build(db, only_ids=[resume['id']])
            assert resumed['cache_hits'] >= manifest['max_batch']
            assert resume['id'] not in {r['id'] for r in vectors.stale_docs(db)}
            # A different chunk budget keeps query embeddings but preserves old chunk spaces.
            old_tag = vectors.model_tag()
            os.environ['BRAIN_EMBED_CHUNK_TOKENS'] = '256'
            assert vectors._qcache_get(db, query) is not None
            vectors.build(db, only_ids=[row['id']])
            assert db.execute('SELECT COUNT(*) FROM vectors WHERE model=?', (old_tag,)).fetchone()[0] > 0
            assert vectors.model_tag() != old_tag
            # Exercise the reproducible evaluator as a real child process, still with fake inference.
            labels = home/'fixture-labels.json'
            labels.write_text(json.dumps([{'q': 'tail evidence', 'gold': ['long_note']}]), encoding='utf-8')
            before_count = db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]
            measured = subprocess.run([sys.executable, str(ROOT/'runtime/embeddinggemma/evaluate.py'),
                '--source-index', str(home/'brain/index.db'), '--source-config', str(home/'config.json'),
                '--workspace', str(home/'measurement'), '--manifest', str(mf),
                '--url', os.environ['BRAIN_EMBED_BASE_URL'], '--labels', str(labels),
                '--dimension', '256', '--chunk-tokens', '128', '--repeats', '1'],
                capture_output=True, text=True, encoding='utf-8', timeout=30)
            assert measured.returncode == 0, measured.stderr[-1000:]
            report = json.loads((home/'measurement/measurement.json').read_text(encoding='utf-8'))
            assert report['status'] == 'diagnostic_complete' and report['summary']['cases'] == 1
            assert db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0] == before_count
            # An unavailable runtime manifest must leave lexical MCP recall usable.
            from brain import server as mcp_server
            old_manifest = os.environ['BRAIN_EMBED_MANIFEST']
            os.environ['BRAIN_EMBED_MANIFEST'] = str(home/'missing-manifest.json')
            localembed._CLIENT = None
            try:
                assert vectors._qcache_get(db, query) is None
                answer = mcp_server.call_tool(db, 'recall',
                    {'query': 'tail evidence', 'semantic_k': 0}, _measure=True)
                assert 'long_note' in answer
            finally:
                os.environ['BRAIN_EMBED_MANIFEST'] = old_manifest
                localembed._CLIENT = client
            db.close()
            print('PASS: token coverage, no truncation, exact prompts, cache/MRL reuse, multi-space migration, source spans and incrementality')
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()


if __name__ == '__main__':
    main()
