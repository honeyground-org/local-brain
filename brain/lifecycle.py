"""Domain-neutral memory validity. Explicit evidence, never newest-file-wins.

A topic/facet identifies one changing fact within an exact scope. Revisions are
separate files connected by supersedes. Plans, decisions and facts use distinct
facets. Unknown legacy records remain searchable but are never certified current.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime
import difflib
import json
import re
from collections import OrderedDict

STATES = ('active', 'proposed', 'disputed', 'superseded', 'retracted', 'archived')
FIELDS = ('topic', 'facet', 'status', 'valid_from', 'valid_to', 'supersedes',
          'superseded_by', 'change_reason', 'evidence', 'legacy_whole_record_sha')
_VIEW = contextvars.ContextVar('brain_memory_view', default=('current', None))
_CACHE = OrderedDict()


def instant(value):
    if not isinstance(value, str):
        raise ValueError('time must be a string')
    if len(value) == 10:
        return datetime.datetime.combine(datetime.date.fromisoformat(value), datetime.time(), datetime.timezone.utc)
    parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp needs a timezone')
    return parsed.astimezone(datetime.timezone.utc)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def started(meta, at):
    return not meta.get('valid_from') or instant(meta['valid_from']) <= instant(at)


def ended(meta, at):
    return bool(meta.get('valid_to') and instant(at) >= instant(meta['valid_to']))



def validate(value):
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        return 'lifecycle must be an object containing only declared fields'
    for key in ('topic', 'facet'):
        v = value.get(key)
        if not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 200 or len(v.splitlines()) != 1:
            return key + ' must be a nonempty single-line identifier of at most 200 characters'
    if value.get('status', 'active') not in STATES:
        return 'invalid lifecycle status'
    for key in ('valid_from', 'valid_to'):
        if key in value:
            try:
                instant(value[key])
            except (ValueError, TypeError):
                return key + ' must be a date or ISO timestamp with timezone'
    if value.get('valid_to') and value.get('valid_from') and instant(value['valid_to']) <= instant(value['valid_from']):
        return 'valid_to must follow valid_from (exclusive end)'
    for key in ('supersedes', 'evidence'):
        values = value.get(key, [])
        if not isinstance(values, list) or len(values) > 50 or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values):
            return key + ' must contain at most 50 nonempty strings'
    for key in ('superseded_by', 'change_reason'):
        if key in value and (not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 2000):
            return key + ' must be a nonempty string of at most 2000 characters'
    legacy = value.get('legacy_whole_record_sha', {})
    if (not isinstance(legacy, dict) or len(legacy) > 50 or
            any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch('[0-9a-f]{40}', v)
                for k, v in legacy.items()) or not set(legacy) <= set(value.get('supersedes', []))):
        return 'legacy_whole_record_sha must pin supersedes names to exact SHA-1 versions'
    if value.get('supersedes') and (not value.get('valid_from') or not value.get('change_reason') or not value.get('evidence')):
        return 'a replacement needs valid_from, change_reason and evidence'
    return None


def from_meta(meta):
    raw = meta.get('lifecycle')
    if raw is None:
        # Honour explicit retirement in older manually authored notes too.
        if meta.get('status') in STATES and meta.get('status') != 'active':
            return {'status': meta['status'], 'superseded_by': meta.get('superseded_by') or None}
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {'status': 'invalid', 'issue': 'invalid lifecycle JSON'}
    issue = validate(value)
    return {'status': 'invalid', 'issue': issue} if issue else value


@contextlib.contextmanager
def view(mode='current', as_of=None):
    token = _VIEW.set((mode, as_of))
    try:
        yield
    finally:
        _VIEW.reset(token)


def snapshot(db):
    stamp = (db.total_changes, db.execute('PRAGMA data_version').fetchone()[0])
    held = _CACHE.get(db)
    if held is None or held[0] != stamp:
        held = (stamp, Snapshot(db))
        _CACHE[db] = held
        _CACHE.move_to_end(db)
        while len(_CACHE) > 4:
            _CACHE.popitem(last=False)
    return held[1]


class Snapshot:
    def __init__(self, db):
        self.docs = {}
        self.by_name = {}
        self.next = {}
        self.prev = {}
        self.issues = {}
        for row in db.execute('SELECT id,name,path,project_id,lifecycle,sha FROM docs'):
            d = dict(row)
            try:
                d['meta'] = json.loads(d['lifecycle'] or '{}')
            except ValueError:
                d['meta'] = {'status': 'invalid'}
            self.docs[d['id']] = d
            self.by_name.setdefault((d['project_id'], d['name']), []).append(d['id'])
        for did, d in self.docs.items():
            m = d['meta']
            for name in m.get('supersedes', []):
                old = self.by_name.get((d['project_id'], name), [])
                if len(old) != 1 or old[0] == did or not (self.same_fact(did, old[0]) or self.legacy_replacement(did, old[0])):
                    self.issues[did] = 'unresolved_or_mismatched_predecessor'
                    continue
                self.edge(old[0], did)
            name = m.get('superseded_by')
            if name:
                new = self.by_name.get((d['project_id'], name), [])
                if len(new) == 1 and new[0] != did and self.same_fact(did, new[0]):
                    self.edge(did, new[0])
                else:
                    self.issues[did] = 'unresolved_or_mismatched_successor'

    def legacy_replacement(self, new, old):
        successor, predecessor = self.docs[new], self.docs[old]
        return (not predecessor['meta'] and successor['project_id'] == predecessor['project_id'] and
                successor['meta'].get('legacy_whole_record_sha', {}).get(predecessor['name']) == predecessor['sha'])

    def same_fact(self, a, b):
        x, y = self.docs[a], self.docs[b]
        return (x['project_id'] == y['project_id'] and x['meta'].get('topic')
                and (x['meta'].get('topic'), x['meta'].get('facet')) ==
                    (y['meta'].get('topic'), y['meta'].get('facet')))

    def edge(self, old, new):
        self.next.setdefault(old, set()).add(new)
        self.prev.setdefault(new, set()).add(old)

    def eligible(self, did, day):
        m = self.docs[did]['meta']
        return (m.get('status', 'active') == 'active' and did not in self.issues
                and started(m, day) and not ended(m, day))

    def resolve(self, did):
        if did not in self.docs:
            return []
        mode, at = _VIEW.get()
        if mode == 'history':
            return [did]
        day = at or now()
        pending, seen, out = [did], set(), []
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            successors = [n for n in sorted(self.next.get(current, ()))
                          if self.docs[n]['meta'].get('status', 'active') in
                          ('active', 'superseded', 'retracted', 'archived', 'disputed')
                          and n not in self.issues and started(self.docs[n]['meta'], day)]
            if successors:
                pending.extend(reversed(successors))
            elif self.eligible(current, day):
                out.append(current)
        return out

    def info(self, did):
        d = self.docs.get(did)
        if not d:
            return {'status': 'untracked', 'current': None}
        m = d['meta']; mode, at = _VIEW.get()
        day = at or now()
        with view('current', at):
            resolved = self.resolve(did)
        status = m.get('status', 'active') if m else 'untracked'
        if not started(m, day):
            status = 'scheduled'
        elif ended(m, day):
            status = 'expired'
        elif did not in resolved and any(started(self.docs[n]['meta'], day) and self.docs[n]['meta'].get('status', 'active') != 'proposed' for n in self.next.get(did, ())):
            status = 'superseded'
        with view('current', at):
            peers = [n for n in self.docs if n != did and self.same_fact(did, n)
                     and self.eligible(n, day) and self.resolve(n) == [n]] if m.get('topic') and did in resolved else []
        if did in resolved and peers:
            status = 'disputed'
        return dict(m, status=status, current=(did in resolved and status == 'active') if m else (False if status == 'superseded' else None),
                    current_names=[self.docs[n]['name'] for n in resolved],
                    conflicts=[self.docs[n]['name'] for n in peers],
                    predecessors=[self.docs[n]['name'] for n in sorted(self.prev.get(did, ()))],
                    successors=[self.docs[n]['name'] for n in sorted(self.next.get(did, ()))],
                    issue=self.issues.get(did), as_of=day)


def remap_scores(db, scores, words, kinds=None, sources=None):
    snap = snapshot(db); out = {}; matches = {}; origins = {}
    for did, score in scores.items():
        for target in snap.resolve(did):
            if kinds or sources:
                row = db.execute('SELECT kind,source FROM docs WHERE id=?', (target,)).fetchone()
                if (kinds and row['kind'] not in kinds) or (sources and row['source'] not in sources):
                    continue
            if target != did:
                origins.setdefault(target, set()).add(snap.docs[did]['name'])
            if score > out.get(target, -1):
                out[target] = score
                matches[target] = words.get(did, [])
    return out, matches, {k: sorted(v) for k, v in origins.items()}


def check_write(db, name, args, current):
    value = args.get('lifecycle')
    if value is None:
        if current.get('lifecycle'):
            args['lifecycle'] = current['lifecycle']
        return None
    issue = validate(value)
    if issue:
        return issue
    old = current.get('lifecycle')
    if old and any(old.get(k) != value.get(k) for k in ('topic', 'facet')):
        return 'topic and facet cannot change in place; create a separate memory'
    if value.get('superseded_by'):
        return 'write the successor with supersedes; do not create a forward link from the write API'
    snap = snapshot(db)
    for predecessor in value.get('supersedes', []):
        ids = snap.by_name.get((args.get('project_id') or '', predecessor), [])
        if predecessor == name or len(ids) != 1:
            return 'supersedes must identify one existing memory in the same scope'
        prior = snap.docs[ids[0]]['meta']
        pinned = value.get('legacy_whole_record_sha', {}).get(predecessor)
        if pinned:
            from brain import remembercontract
            row = snap.docs[ids[0]]
            if prior or row['sha'] != pinned or remembercontract.file_sha(row['path']) != pinned:
                return 'legacy replacement requires an untracked predecessor at the exact file SHA you read'
            continue
        if not prior:
            return 'an untracked predecessor requires legacy_whole_record_sha for an explicit whole-record replacement'
        if (prior.get('topic'), prior.get('facet')) != (value['topic'], value['facet']):
            return 'supersedes must keep the same topic and facet'
        if prior.get('valid_from') and instant(value['valid_from']) < instant(prior['valid_from']):
            return 'a replacement cannot start before its predecessor'
    return None


def history(db, name, scope=None, offset=0, limit=25):
    snap = snapshot(db)
    seeds = [d for d, r in snap.docs.items() if r['name'] == name and (scope is None or r['project_id'] == scope)]
    if len(seeds) != 1:
        return {'ok': False, 'error': {'code': 'ambiguous_or_missing', 'message': 'Specify the exact memory name and scope'}}
    seed = seeds[0]
    members = {seed} | {n for n in snap.docs if snap.same_fact(seed, n)}
    pending = list(members)
    while pending:
        did = pending.pop()
        for linked in snap.prev.get(did, set()) | snap.next.get(did, set()):
            if linked not in members:
                members.add(linked)
                pending.append(linked)
    rows = []
    for did in members:
        r = db.execute('SELECT name,path,body,sha,evidence_date FROM docs WHERE id=?', (did,)).fetchone()
        record = dict(r, lifecycle=snap.info(did))
        record['body_truncated'] = len(record['body']) > 4000
        record['body'] = record['body'][:4000]
        record['changes'] = []
        predecessors = sorted(snap.prev.get(did, ()))
        record['changes_truncated'] = len(predecessors) > 5
        for predecessor in predecessors[:5]:
            before = db.execute('SELECT name,body FROM docs WHERE id=?', (predecessor,)).fetchone()
            delta = list(difflib.unified_diff(before['body'].splitlines(), r['body'].splitlines(),
                                            fromfile=before['name'], tofile=r['name'], lineterm=''))
            record['changes'].append({'from': before['name'], 'to': r['name'],
                                      'diff': '\n'.join(delta[:40])[:1000],
                                      'truncated': len(delta) > 40 or len('\n'.join(delta)) > 1000})
        rows.append(record)
    def history_order(record):
        effective = record['lifecycle'].get('valid_from')
        # ISO strings with different timezone offsets must not be sorted lexically.
        at = instant(effective) if effective else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        did = next(n for n in members if snap.docs[n]['path'] == record['path'])
        ancestors, pending = set(), list(snap.prev.get(did, ()))
        while pending:
            previous = pending.pop()
            if previous not in ancestors:
                ancestors.add(previous)
                pending.extend(snap.prev.get(previous, ()))
        return at, len(ancestors), record['name']
    rows.sort(key=history_order)
    current = [r['name'] for r in rows if r['lifecycle'].get('current')]
    from brain import revisions
    observed = revisions.history(snap.docs[seed]['path'], snap.docs[seed]['project_id'], offset, limit)
    return {'ok': True, 'name': name, 'current_names': current, 'unresolved': not bool(current),
            'revisions': rows[offset:offset+limit], 'document_history': observed, 'total': len(rows), 'offset': offset,
            'has_more': offset + limit < len(rows) or observed.get('has_more', False),
            'revision_has_more': offset + limit < len(rows), 'history_complete': False,
            'coverage': 'explicitly linked or same-topic/facet indexed records; unrecorded conversations are unknown'}


def format_line(row):
    from brain import i18n
    m = row.get("lifecycle") or {}
    text = i18n.t("memory.validity", status=m.get("status", "untracked"),
                  current=", ".join(m.get("current_names", [])) or "?",
                  reason=m.get("change_reason") or "?",
                  previous=", ".join(m.get("predecessors", [])) or "—")

    if (row.get("feedback") or {}).get("demoted"):
        text += " · " + i18n.t("memory.feedback_demoted")
    return text
