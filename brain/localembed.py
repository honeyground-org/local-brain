"""Direct, loopback-only EmbeddingGemma adapter. No generation CLI or API fallback."""
from __future__ import annotations

import array
import json
import math
import os
import sqlite3
import time
import urllib.parse
import urllib.request

from brain import embedplan


class LocalEmbeddingError(RuntimeError):
    pass


class Client:
    def __init__(self, choice, timeout=5.0):
        from brain import store
        self.base = choice['base_url'].rstrip('/')
        u = urllib.parse.urlsplit(self.base)
        if u.scheme != 'http' or u.hostname not in ('127.0.0.1', '::1') or u.username or u.password:
            raise LocalEmbeddingError('EmbeddingGemma requires a numeric loopback HTTP endpoint')
        path = os.environ.get('BRAIN_EMBED_MANIFEST') or os.path.join(store.brain_home(), 'embeddinggemma.json')
        with open(path, encoding='utf-8') as fh:
            self.manifest = json.load(fh)
        if (self.manifest.get('model') != choice['model'] or self.manifest.get('format') != embedplan.VERSION
                or self.manifest.get('max_tokens') != embedplan.MAX_TOKENS):
            raise LocalEmbeddingError('EmbeddingGemma model/format manifest mismatch')
        self.space = self.manifest['space']
        if not isinstance(self.space, str) or len(self.space) != 64:
            raise LocalEmbeddingError('invalid vector space fingerprint')
        self.timeout = timeout
        # Never use proxy environment variables or follow redirects with personal text.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, route, body):
        body = dict(body, space=self.space)
        req = urllib.request.Request(self.base + '/' + route, data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        with self.http.open(req, timeout=self.timeout) as response:
            result = json.load(response)
        if result.get('space') != self.space:
            raise LocalEmbeddingError('runtime changed; refusing mixed vector spaces')
        return result

    def plan(self, text, title, budget):
        rows = self.request('plan', {'text': text, 'title': title, 'budget': budget})['chunks']
        end = 0
        for r in rows:
            if (r['start'] != end or not end < r['end'] <= len(text)
                    or r['text'] != text[end:r['end']] or not 0 < r['tokens'] <= budget
                    or r['input_hash'] != embedplan.digest(embedplan.formatted(r['text'], title=r['title']))):
                raise LocalEmbeddingError('invalid source coverage from tokenizer service')
            end = r['end']
        if end != len(text):
            raise LocalEmbeddingError('source suffix missing from tokenizer plan')
        return rows

    def embed(self, texts, dim, side='doc', titles=None):
        if dim not in embedplan.DIMS:
            raise LocalEmbeddingError('unsupported EmbeddingGemma output dimension')
        result = self.request('embeddings', {'input': list(texts), 'side': side,
                                           'titles': list(titles or [''] * len(texts)), 'dimensions': dim})
        rows = result['embeddings']
        if len(rows) != len(texts):
            raise LocalEmbeddingError('embedding response count mismatch')
        for row in rows:
            if len(row) != dim or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in row):
                raise LocalEmbeddingError('invalid embedding shape or values')
            if not any(row):
                raise LocalEmbeddingError('zero embedding vector')
        return rows


_CLIENT = None


def client():
    global _CLIENT
    if _CLIENT is None:
        from brain import vectors
        _CLIENT = Client(vectors._CHOICE, timeout=float(os.environ.get('BRAIN_LOCAL_EMBED_TIMEOUT', '5')))
    return _CLIENT


def budget():
    value = int(os.environ.get('BRAIN_EMBED_CHUNK_TOKENS', '768'))
    if not 32 <= value <= embedplan.MAX_TOKENS:
        raise LocalEmbeddingError('invalid EmbeddingGemma token budget')
    return value


def tag():
    return 'embeddinggemma:' + client().space + ':tokens=' + str(budget())


def ensure(db):
    db.execute('CREATE TABLE IF NOT EXISTS local_chunk_plans('
               'doc_id INTEGER, model TEXT, sha TEXT, plan TEXT, PRIMARY KEY(doc_id,model))')
    db.execute('CREATE TABLE IF NOT EXISTS local_chunk_cache('
               'space TEXT, input_hash TEXT, dim INTEGER, vec BLOB, '
               'PRIMARY KEY(space,input_hash,dim))')


def cached_plan(db, row, model):
    found = db.execute('SELECT sha,plan FROM local_chunk_plans WHERE doc_id=? AND model=?',
                       (row['id'], model)).fetchone()
    return json.loads(found[1]) if found and found[0] == row['sha'] else None


def stale(db):
    from brain import vectors
    ensure(db)
    model = vectors.model_tag()
    skip = set(vectors.no_embed_sources())
    result = []
    for row in db.execute('SELECT id,name,source,title,description,body,sha FROM docs'):
        if row['source'] in skip:
            continue
        planned = cached_plan(db, row, model)
        have = list(db.execute('SELECT chunk_no,sha FROM vectors WHERE doc_id=? AND model=? AND dim=?',
                               (row['id'], model, vectors.DIM)))
        if (planned is None or len(have) != len(planned)
                or {r[0] for r in have} != set(range(len(planned)))
                or any(r[1] != row['sha'] for r in have)):
            result.append(row)
    return result


def build(db, limit_docs=0, progress=False, only_ids=None):
    from brain import stores, vectors
    ensure(db)
    model, dim, service = vectors.model_tag(), vectors.DIM, client()
    todo = stale(db)
    if only_ids:
        todo = [r for r in todo if r['id'] in set(only_ids)]
    if limit_docs:
        todo = todo[:limit_docs]
    start = time.monotonic()
    calls = chunks = hits = done = 0
    for row in todo:
        planned = cached_plan(db, row, model)
        if planned is None:
            planned = service.plan((row['body'] or '').strip(), row['title'] or row['name'], budget())
            with db:
                db.execute('INSERT OR REPLACE INTO local_chunk_plans VALUES(?,?,?,?)',
                           (row['id'], model, row['sha'], json.dumps(planned)))
        # Incomplete documents stay unserved until every chunk is available for this version.
        blobs = []
        for item in planned:
            hit = db.execute('SELECT vec FROM local_chunk_cache WHERE space=? AND input_hash=? AND dim=?',
                             (service.space, item['input_hash'], dim)).fetchone()
            if hit:
                blobs.append(hit[0]); hits += 1
            else:
                blobs.append(None)
        missing = [i for i, blob in enumerate(blobs) if blob is None]
        size = max(1, min(vectors.BATCH, int(service.manifest['max_batch'])))
        for offset in range(0, len(missing), size):
            indices = missing[offset:offset+size]
            batch = [planned[i] for i in indices]
            values = service.embed([r['text'] for r in batch], dim, titles=[r['title'] for r in batch])
            calls += 1
            with db:
                for i, value in zip(indices, values):
                    blob = vectors._unit(value).tobytes()
                    db.execute('INSERT OR REPLACE INTO local_chunk_cache VALUES(?,?,?,?)',
                               (service.space, planned[i]['input_hash'], dim, blob))
                    blobs[i] = blob
                    chunks += 1
        with db:
            # A document may have changed while inference ran: do not activate stale work.
            current = db.execute('SELECT sha FROM docs WHERE id=?', (row['id'],)).fetchone()
            if not current or current[0] != row['sha']:
                continue
            stores.invalidate(db, ('vector',))
            db.execute('DELETE FROM vectors WHERE doc_id=? AND model=? AND dim=?', (row['id'], model, dim))
            db.executemany('INSERT INTO vectors VALUES(?,?,?,?,?,?)',
                           [(row['id'], i, row['sha'], model, dim, blob) for i, blob in enumerate(blobs)])
        done += 1
    cache_limit = int(os.environ.get('BRAIN_LOCAL_EMBED_CACHE_BYTES', str(256 * 1024 * 1024)))
    if cache_limit < 0:
        raise LocalEmbeddingError('cache byte budget must not be negative')
    with db:
        excess = db.execute('SELECT COALESCE(SUM(LENGTH(vec)),0) FROM local_chunk_cache').fetchone()[0] - cache_limit
        remove = []
        if excess > 0:
            for rid, size in db.execute('SELECT rowid,LENGTH(vec) FROM local_chunk_cache ORDER BY rowid'):
                remove.append((rid,))
                excess -= size
                if excess <= 0:
                    break
            db.executemany('DELETE FROM local_chunk_cache WHERE rowid=?', remove)
    pushed = stores.sync_if_chosen(db, ('vector',)).get('vector')
    return {'docs': done, 'chunks': chunks, 'calls': calls, 'cache_hits': hits,
            'docs_left': len(stale(db)), 'seconds': round(time.monotonic()-start, 3),
            'model': model, 'dim': dim, 'provider': 'embeddinggemma', 'daily_quota_reached': False,
            **({'store': pushed} if pushed else {})}


def spans(db, did):
    from brain import vectors
    try:
        row = db.execute('SELECT sha,body FROM docs WHERE id=?', (did,)).fetchone()
        plan_row = db.execute('SELECT sha,plan FROM local_chunk_plans WHERE doc_id=? AND model=?',
                              (did, vectors.model_tag())).fetchone()
        if row and plan_row and row[0] == plan_row[0]:
            return [(r['start'], r['end']) for r in json.loads(plan_row[1])]
    except sqlite3.Error:
        pass
    return []
