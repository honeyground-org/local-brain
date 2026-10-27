#!/usr/bin/env python3
"""Build and measure a local embedding profile in a new SQLite snapshot, never the live index.

A diagnostic runner, not an automatic promotion gate. Supply real labelled questions; no labels
are generated here. Reports first-pass versus repeated requests, not model cold-start performance.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-index', required=True)
    p.add_argument('--source-config', required=True)
    p.add_argument('--workspace', required=True, help='New directory; must not already exist')
    p.add_argument('--manifest', required=True)
    p.add_argument('--url', default='http://127.0.0.1:18081/v1')
    p.add_argument('--labels', nargs='+', required=True)
    p.add_argument('--dimension', type=int, choices=(128, 256, 512, 768), default=768)
    p.add_argument('--chunk-tokens', type=int, default=768)
    p.add_argument('--repeats', type=int, default=5)
    args = p.parse_args()
    if args.repeats < 1:
        p.error('repeats must be positive')
    source = Path(args.source_index).resolve()
    if not source.is_file():
        p.error('source index does not exist')
    config = json.loads(Path(args.source_config).read_text(encoding='utf-8'))
    cases, hashes = [], {}
    for filename in args.labels:
        data = Path(filename).read_bytes()
        hashes[Path(filename).name] = hashlib.sha256(data).hexdigest()
        value = json.loads(data)
        cases.extend(value if isinstance(value, list) else value.get('cases', value.get('A_memory_needed', [])))
    if not cases or any(not c.get('q') or not c.get('gold') for c in cases):
        p.error('labels must contain q/gold cases')
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
    for key in list(os.environ):
        if key.startswith('BRAIN_'):
            del os.environ[key]
    config.update(engines={'embed': {'provider': 'embeddinggemma', 'model': 'google/embeddinggemma-300m',
                                     'base_url': args.url}, 'judge': {'provider': 'none'}},
                  stores={'vector': {'backend': 'sqlite'}, 'graph': {'backend': 'sqlite'}})
    cfg = workspace/'config.json'
    cfg.write_text(json.dumps(config), encoding='utf-8')
    os.environ.update(BRAIN_HOME=str(workspace), BRAIN_CONFIG=str(cfg),
                      BRAIN_EMBED_MANIFEST=str(Path(args.manifest).resolve()),
                      BRAIN_EMBED_DIM=str(args.dimension), BRAIN_EMBED_CHUNK_TOKENS=str(args.chunk_tokens),
                      BRAIN_PERSONAL_DISABLED='1')
    original = sqlite3.connect('file:'+urllib.parse.quote(str(source))+'?mode=ro', uri=True)
    copied = sqlite3.connect(str(workspace/'index.db'))
    original.backup(copied)
    original.close(); copied.close()
    from brain import store, vectors, localembed, evalinit, server
    db = store.connect()
    report = {'status': 'running', 'model_space': localembed.client().manifest, 'labels': hashes,
              'dimension': args.dimension, 'chunk_tokens': args.chunk_tokens,
              'runtime': sys.version, 'backend': 'sqlite', 'repeats': args.repeats,
              'limitations': ['diagnostic only; no chronological holdout or promotion',
                              'resident model; first pass is not cold start',
                              'repeat requests are not independent quality samples'], 'cases': []}
    corpus = hashlib.sha256()
    for row in db.execute('SELECT id,sha FROM docs ORDER BY id'):
        corpus.update(json.dumps(tuple(row)).encode('utf-8'))
    report['corpus_hash'] = corpus.hexdigest()
    output = workspace/'measurement.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    try:
        report['build'] = vectors.build(db)
        for turn in range(args.repeats):
            for i, case in enumerate(cases):
                start = time.perf_counter()
                entry = {'case': i, 'repeat': turn}
                try:
                    cached = vectors._qcache_get(db, case['q']) is not None
                    hits = vectors.search(db, case['q'], k=8)
                    names = [db.execute('SELECT name FROM docs WHERE id=?', (r[0],)).fetchone()[0] for r in hits]
                    entry.update(vector_s=time.perf_counter()-start, query_cache_hit=cached,
                                 hit3=any(evalinit.gold_hit(evalinit.golds(case['gold']), n) for n in names[:3]),
                                 hit8=any(evalinit.gold_hit(evalinit.golds(case['gold']), n) for n in names))
                    start_mcp = time.perf_counter()
                    rows = []
                    result = server.call_tool(db, 'recall', {'query': case['q'], 'terms': case.get('terms', []), 'k': 8},
                                              _measure=True, _capture=rows)
                    direct = [r['name'] for r in rows if not r.get('related')]
                    entry.update(mcp_cached_query_s=time.perf_counter()-start_mcp, returned_chars=len(result),
                                 mcp_hit3=any(evalinit.gold_hit(evalinit.golds(case['gold']), n) for n in direct[:3]))
                except Exception as exc:
                    entry.update(error=type(exc).__name__, seconds=time.perf_counter()-start, hit3=False, hit8=False)
                report['cases'].append(entry)
            output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        first = [r for r in report['cases'] if r['repeat'] == 0]
        times = sorted(r['vector_s'] for r in first if 'vector_s' in r)
        report['summary'] = {'cases': len(first), 'errors': sum('error' in r for r in first),
                             'vector_hit3': sum(r['hit3'] for r in first), 'vector_hit8': sum(r['hit8'] for r in first),
                             'first_pass_vector_p95_s': times[max(0, math.ceil(.95*len(times))-1)] if times else None}
        report['status'] = 'diagnostic_complete'
    except Exception as exc:
        report.update(status='failed', error=type(exc).__name__)
        raise
    finally:
        db.close()
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report['summary']))


if __name__ == '__main__':
    main()
