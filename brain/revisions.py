"""Private, durable observations of document bodies, separate from the rebuildable index.

Observation time is not effective time. These snapshots preserve edits; only explicit
lifecycle records can say when a fact became valid. Files that no longer belong to the
index are not made searchable by this archive. No archive body enters retrieval ranks.
"""
from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path

FIELDS = ('name', 'path', 'source', 'kind', 'title', 'description', 'body', 'sha',
          'project_id', 'lifecycle', 'evidence_date')


def directory():
    from brain import store
    return Path(store.brain_home()) / 'document-history'


def location(path):
    return directory() / (hashlib.sha256(os.path.abspath(path).encode('utf-8')).hexdigest() + '.json')


def _record(value):
    item = {key: dict(value).get(key) or '' for key in FIELDS}
    if isinstance(item['lifecycle'], str):
        item['lifecycle'] = json.loads(item['lifecycle'] or '{}')
    item['project_id'] = item['project_id'] or ''
    return item


def preserve(before, after):
    """Write before changing the index; fail visibly rather than lose the prior body."""
    from brain import remembercontract
    root = directory()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = location(after['path'])
    with remembercontract.write_lock(str(root)):
        prior = path.read_text(encoding='utf-8') if path.exists() else None
        archive = json.loads(prior) if prior is not None else {'version': 1, 'observations': []}
        rows = archive['observations']
        if not rows and before:
            rows.append({'record': _record(before), 'observed_at': None, 'kind': 'index_baseline'})
        record = _record(after)
        if rows and rows[-1]['record'] == record:
            # A new archive still needs its baseline written even on a full unchanged rebuild.
            if prior is not None:
                return
        else:
            rows.append({'record': record, 'kind': 'observed',
                         'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        expected = hashlib.sha1(prior.encode('utf-8')).hexdigest() if prior is not None else None
        remembercontract.atomic_write(str(path), json.dumps(archive, ensure_ascii=False), expected)


def history(path, scope='', offset=0, limit=25):
    target = location(path)
    try:
        saved = json.loads(target.read_text(encoding='utf-8'))['observations']
    except FileNotFoundError:
        saved = []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'available': False, 'error': type(exc).__name__, 'versions': [], 'history_complete': False}
    # Moving a file between scopes must not expose its previous project's body.
    rows = [r for r in saved if r['record'].get('project_id', '') == (scope or '')]
    versions = []
    for i, row in enumerate(rows[offset:offset + limit], offset):
        record = dict(row['record'])
        body = record['body']
        record['body'] = body[:4000]
        record['body_truncated'] = len(body) > 4000
        delta = '\n'.join(difflib.unified_diff(rows[i-1]['record']['body'].splitlines(), body.splitlines(),
                         fromfile=rows[i-1]['record']['sha'], tofile=record['sha'], lineterm='')) if i else ''
        versions.append(dict(record, observed_at=row['observed_at'], observation_kind=row['kind'],
                             latest_observed=i == len(rows)-1, diff=delta[:4000], diff_truncated=len(delta)>4000,
                             validity='unknown_without_explicit_lifecycle'))
    return {'available': True, 'versions': versions, 'total': len(rows), 'offset': offset,
            'has_more': offset + limit < len(rows), 'history_complete': False,
            'coverage': 'observed indexed bodies, including a baseline where available; observation is not effective time'}
