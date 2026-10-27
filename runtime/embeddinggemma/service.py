#!/usr/bin/env python3
"""Optional, offline model service. Install its requirements in a separate Python 3.11+ environment.

Core Brain never imports this module. The only non-standard dependencies are explicit in main().
One bounded priority queue owns model execution; query jobs precede background document batches.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import http.server
import importlib.metadata
import itertools
import json
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from brain import deadline, embedplan

MODEL = 'google/embeddinggemma-300m'


class Runtime:
    def __init__(self, model, manifest, cache_path, max_batch=4, queue_size=32, timeout=30, cache_bytes=268435456):
        self.model = model
        self.manifest = manifest
        self.cache_path = cache_path
        self.max_batch, self.timeout, self.cache_bytes = max_batch, timeout, cache_bytes
        self.jobs = queue.PriorityQueue(maxsize=queue_size)
        self.sequence = itertools.count()
        self.worker = threading.Thread(target=self._work, daemon=True, name='embeddinggemma')
        self.worker.start()

    def count(self, text):
        return len(self.model.tokenizer.encode(text, add_special_tokens=True, truncation=False))

    def submit(self, route, body):
        if body.get('space') != self.manifest['space']:
            raise ValueError('vector space mismatch')
        job = {'route': route, 'body': body, 'done': threading.Event(), 'expires': time.monotonic()+self.timeout}
        priority = 0 if body.get('side') == 'query' else 1 if route == 'plan' else 2
        self.jobs.put_nowait((priority, next(self.sequence), job))
        if not job['done'].wait(self.timeout):
            raise TimeoutError('local embedding queue deadline exceeded')
        if 'error' in job:
            raise job['error']
        return dict(job['result'], space=self.manifest['space'])

    def _work(self):
        db = sqlite3.connect(self.cache_path)
        db.execute('CREATE TABLE IF NOT EXISTS embeddings(space TEXT, input_hash TEXT, vec TEXT, '
                   'PRIMARY KEY(space,input_hash))')
        self.bytes_used = db.execute('SELECT COALESCE(SUM(LENGTH(vec)),0) FROM embeddings').fetchone()[0]
        while True:
            _, _, job = self.jobs.get()
            if job is None:
                self.jobs.task_done()
                db.close()
                return
            wd = deadline.watchdog(max(0.01, job['expires']-time.monotonic()), lambda: None, exit_code=3)
            try:
                if time.monotonic() > job['expires']:
                    raise TimeoutError('queued embedding expired before execution')
                job['result'] = self._run(db, job['route'], job['body'])
            except Exception as exc:
                job['error'] = exc
            finally:
                if wd.claim():
                    wd.cancel()
                job['done'].set()
                self.jobs.task_done()

    def close(self):
        self.jobs.put((99, next(self.sequence), None))
        self.worker.join(timeout=self.timeout)

    def _run(self, db, route, body):
        if route == 'plan':
            return {'chunks': embedplan.plan(body['text'], body.get('title', ''), self.count,
                                             int(body.get('budget', 768)))}
        if route != 'embeddings':
            raise ValueError('unknown endpoint')
        texts, side = body['input'], body.get('side', 'doc')
        dim = body.get('dimensions', 768)
        titles = body.get('titles') or [''] * len(texts)
        if (not isinstance(texts, list) or not 0 < len(texts) <= self.max_batch
                or len(titles) != len(texts) or dim not in embedplan.DIMS):
            raise ValueError('invalid batch, titles or dimension')
        inputs = []
        for text, title in zip(texts, titles):
            if not isinstance(text, str) or not isinstance(title, str):
                raise ValueError('inputs must be strings')
            value = embedplan.formatted(text, side, title)
            if self.count(value) > embedplan.MAX_TOKENS:
                raise ValueError('input exceeds 2048 tokens; split it, never truncate')
            inputs.append(value)
        # Ordered unique inputs coalesce repeated items and serialized concurrent requests.
        held, missing = {}, {}
        for value in inputs:
            key = embedplan.digest(value)
            row = db.execute('SELECT vec FROM embeddings WHERE space=? AND input_hash=?',
                             (self.manifest['space'], key)).fetchone()
            if row:
                held[key] = array.array('f')
                held[key].frombytes(row[0])
            else:
                missing[key] = value
        if missing:
            # Inputs already carry the task prompt; prompt='' prevents double formatting.
            result = self.model.encode(list(missing.values()), prompt='', batch_size=self.max_batch,
                                       normalize_embeddings=True, show_progress_bar=False)
            if len(result) != len(missing):
                raise ValueError('encoder response count mismatch')
            with db:
                for key, row in zip(missing, result):
                    vector = [float(v) for v in row]
                    if len(vector) != 768 or not all(math.isfinite(v) for v in vector) or not any(vector):
                        raise ValueError('invalid encoder vector')
                    held[key] = vector
                    blob = array.array('f', vector).tobytes()
                    db.execute('INSERT OR REPLACE INTO embeddings VALUES(?,?,?)',
                               (self.manifest['space'], key, blob))
                    self.bytes_used += len(blob)
                # One ordered pass, not repeated full-cache scans during eviction.
                remove = []
                if self.bytes_used > self.cache_bytes:
                    for rid, size in db.execute('SELECT rowid,LENGTH(vec) FROM embeddings ORDER BY rowid'):
                        remove.append((rid,))
                        self.bytes_used -= size
                        if self.bytes_used <= self.cache_bytes:
                            break
                    db.executemany('DELETE FROM embeddings WHERE rowid=?', remove)
        output = []
        for value in inputs:
            vector = held[embedplan.digest(value)][:dim]
            norm = math.sqrt(sum(v*v for v in vector))
            if not norm:
                raise ValueError('zero shortened vector')
            output.append([v/norm for v in vector])
        return {'embeddings': output, 'computed': len(missing), 'cache_hits': len(inputs)-len(missing)}


def handler(runtime):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # no private inputs or access paths in logs

        def setup(self):
            super().setup()
            self.connection.settimeout(runtime.timeout)

        def do_POST(self):
            try:
                if self.headers.get('Origin') or self.headers.get_content_type() != 'application/json':
                    raise ValueError('only non-browser JSON clients are supported')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4 * 1024 * 1024:
                    raise ValueError('request body size exceeded')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('request must be an object')
                if self.path not in ('/v1/plan', '/v1/embeddings'):
                    raise ValueError('unknown endpoint')
                result = runtime.submit(self.path.rsplit('/', 1)[-1], body)
                status = 200
            except queue.Full:
                status, result = 503, {'error': 'local embedding queue full'}
            except TimeoutError:
                status, result = 503, {'error': 'local embedding deadline exceeded'}
            except Exception as exc:
                status, result = 400, {'error': type(exc).__name__}
            data = json.dumps(result, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-path', required=True, help='Previously downloaded local model snapshot')
    p.add_argument('--revision', required=True, help='Pinned 40-character model commit')
    p.add_argument('--manifest', required=True, help='Manifest read by Brain')
    p.add_argument('--cache', required=True, help='Local inference cache SQLite path')
    p.add_argument('--port', type=int, default=18081)
    p.add_argument('--device', choices=('cpu', 'mps', 'cuda'), default='cpu')
    p.add_argument('--batch', type=int, default=4)
    p.add_argument('--queue-size', type=int, default=32)
    p.add_argument('--timeout', type=float, default=30)
    args = p.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.revision) or not Path(args.model_path).is_dir():
        p.error('a local snapshot and pinned revision are required')
    if not 1 <= args.batch <= 64 or args.timeout <= 0 or args.queue_size <= 0:
        p.error('invalid runtime bounds')
    # Startup is offline too: the service never downloads code or model artifacts.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from sentence_transformers import SentenceTransformer
    import torch
    model = SentenceTransformer(args.model_path, device=args.device, local_files_only=True,
                                trust_remote_code=False, model_kwargs={'torch_dtype': torch.float32})
    if model.get_sentence_embedding_dimension() != 768 or model.max_seq_length != 2048:
        raise ValueError('the snapshot is not the selected EmbeddingGemma 300M profile')
    files = []
    for path in sorted(Path(args.model_path).rglob('*')):
        if path.is_file() and (path.suffix in ('.json', '.safetensors', '.model')):
            h = hashlib.sha256()
            with path.open('rb') as fh:
                for block in iter(lambda: fh.read(1024*1024), b''):
                    h.update(block)
            files.append([str(path.relative_to(args.model_path)), h.hexdigest()])
    identity = {'model': MODEL, 'revision': args.revision, 'files': files, 'device': args.device,
                'dtype': 'float32', 'format': embedplan.VERSION,
                'implementation': embedplan.digest(Path(__file__).read_text(encoding='utf-8') + Path(embedplan.__file__).read_text(encoding='utf-8')),
                'libraries': {n: importlib.metadata.version(n) for n in ('sentence-transformers', 'transformers', 'torch')}}
    manifest = {'model': MODEL, 'revision': args.revision, 'format': embedplan.VERSION, 'max_tokens': 2048,
                'dimensions': list(embedplan.DIMS), 'max_batch': args.batch,
                'space': embedplan.digest(json.dumps(identity, sort_keys=True)), 'identity': identity}
    for path in (args.cache, args.manifest):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    runtime = Runtime(model, manifest, args.cache, args.batch, args.queue_size, args.timeout)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler(runtime))
    # Publish only after binding successfully, so another instance cannot replace the manifest.
    temp = args.manifest + '.tmp-' + str(os.getpid())
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2)
    os.replace(temp, args.manifest)
    print(json.dumps({'status': 'ready', 'port': args.port, 'space': manifest['space']}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.close()


if __name__ == '__main__':
    main()
