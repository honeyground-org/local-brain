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
import hashlib
import re
import unicodedata
from collections import OrderedDict

STATES = ('active', 'proposed', 'disputed', 'superseded', 'retracted', 'archived')
FIELDS = ('topic', 'facet', 'status', 'valid_from', 'valid_to', 'supersedes',
          'superseded_by', 'change_reason', 'evidence', 'legacy_whole_record_sha')
_VIEW = contextvars.ContextVar('brain_memory_view', default=('current', None))
_CACHE = OrderedDict()


def identity(meta):
    """Canonical Unicode, not translation, transliteration or case folding."""
    return tuple(unicodedata.normalize('NFC', meta.get(k) or '') for k in ('topic', 'facet'))


def legacy_family(snap, names, scope):
    """Whole-record replacement binds one legacy root, including proposed branches.

    Walk ancestors to find untracked roots, then include every descendant. This
    catches a differently named fact and a proposal promoted after another client
    claimed that root; matching topic/facet alone cannot see either case.
    """
    pending = [did for name in names for did in snap.by_name.get((scope, name), [])]
    seen, roots = set(), set()
    while pending:
        did = pending.pop()
        if did in seen:
            continue
        seen.add(did)
        if not snap.docs[did]['meta']:
            roots.add(did)
        pending.extend(snap.prev.get(did, set()))
    family, pending = set(), list(roots)
    while pending:
        did = pending.pop()
        if did in family:
            continue
        family.add(did)
        pending.extend(snap.next.get(did, set()))
    return family


def prepare(db, name, args):
    """Bounded review of one fact. Similar prose is never replacement authority."""
    from brain import remembercontract
    meta = args.get('lifecycle') or {}
    scope = args.get('project_id') or ''
    records, versions = [], []
    snap = snapshot(db)
    family = legacy_family(snap, meta.get('supersedes', []), scope)
    for did, record in snap.docs.items():
        if (record['project_id'] != scope or not meta.get('topic')
                or (identity(record['meta']) != identity(meta) and did not in family)):
            continue
        try:
            actual_sha = remembercontract.file_sha(record['path'])
        except (OSError, UnicodeError):
            actual_sha = None
        versions.append((record['name'], record['path'], record['sha'], actual_sha))
        row = db.execute('SELECT body FROM docs WHERE id=?', (did,)).fetchone()
        records.append({'name': record['name'], 'sha': record['sha'], 'source_matches_index': actual_sha == record['sha'],
                        'lifecycle': snap.info(did), 'body': (row['body'] or '')[:2000],
                        'body_truncated': len(row['body'] or '') > 2000})
    records.sort(key=lambda r: r['name'])
    raw = json.dumps([scope, identity(meta), sorted(versions)], ensure_ascii=False, separators=(',', ':'))
    return {'contract': remembercontract.CONTRACT, 'ok': True, 'stored': False, 'result': 'prepared', 'scope': scope,
            'expected_fact_sha': hashlib.sha256(raw.encode('utf-8')).hexdigest() if meta.get('topic') else None,
            'records': records[:50], 'total': len(records), 'truncated': len(records) > 50,
            'untracked': not bool(meta), 'source_matches_index': all(r['source_matches_index'] for r in records),
            'coverage': 'indexed records with the same exact scope and Unicode-normalized topic/facet, plus untracked predecessor families; semantic equivalence is not inferred',
            'next': 'Use recall to discover identifiers across languages, then reuse topic/facet. Keep one fact per memory. Read memory_history for omitted records. Confirm evidence before superseding; use proposed or disputed when uncertain.'}


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
        self.by_fact = {}
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
            if d['meta'].get('topic'):
                self.by_fact.setdefault((d['project_id'], identity(d['meta'])), []).append(d['id'])
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
                and identity(x['meta']) == identity(y['meta']))

    def fact_members(self, did):
        d = self.docs[did]
        return self.by_fact.get((d['project_id'], identity(d['meta'])), ())

    def history_partition(self, seed):
        """Keep fact branches separate even when they share an untracked source."""
        cache = getattr(self, '_history_cache', None)
        if cache is None:
            cache = self._history_cache = {}
        if seed in cache:
            return cache[seed]
        def neighbors(did):
            return (self.prev.get(did, set()) | self.next.get(did, set())
                    | set(self.fact_members(did)))
        component, pending = {seed}, [seed]
        while pending:
            for linked in neighbors(pending.pop()):
                if linked not in component:
                    component.add(linked)
                    pending.append(linked)
        groups = {}
        for did in component:
            meta = self.docs[did]['meta']
            if meta.get('topic'):
                groups.setdefault(identity(meta), set()).add(did)
        anchor = identity(self.docs[seed]['meta']) if self.docs[seed]['meta'].get('topic') else None
        if anchor is None and len(groups) == 1:
            anchor = next(iter(groups))
        members, pending = {seed}, [seed]
        while pending:
            for linked in neighbors(pending.pop()):
                meta = self.docs[linked]['meta']
                if meta.get('topic') and identity(meta) != anchor:
                    continue
                if linked not in members:
                    members.add(linked)
                    pending.append(linked)
        branches = [dict(topic=key[0], facet=key[1], members=sorted(ids))
                    for key, ids in sorted(groups.items()) if key != anchor] if len(groups) > 1 else []
        cache[seed] = members, branches
        return cache[seed]

    def history_members(self, seed):
        return self.history_partition(seed)[0]

    def history_branches(self, seed):
        return self.history_partition(seed)[1]

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
            peers = [n for n in self.fact_members(did) if n != did
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
    if old and identity(old) != identity(value):
        return 'topic and facet cannot change in place; create a separate memory'
    if value.get('superseded_by'):
        return 'write the successor with supersedes; do not create a forward link from the write API'
    snap = snapshot(db)
    scope = args.get('project_id') or ''
    family = legacy_family(snap, value.get('supersedes', []), scope)
    for did in family:
        other = snap.docs[did]['meta']
        if other and other.get('status', 'active') == 'active' and identity(other) != identity(value):
            return 'a whole-record predecessor already belongs to another active fact; reuse its identity and revise its successor'
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
        if identity(prior) != identity(value):
            return 'supersedes must keep the same topic and facet'
        if prior.get('valid_from') and instant(value['valid_from']) < instant(prior['valid_from']):
            return 'a replacement cannot start before its predecessor'
    return None


def reconcile_write(db, name, args, unchanged_fact=False):
    """Require an explicit decision for competing active facts; never newest-file-wins."""
    review = prepare(db, name, args)
    expected = args.get('expected_fact_sha')
    if expected and (expected != review['expected_fact_sha'] or not review['source_matches_index']):
        review['reason'] = 'source_not_indexed' if not review['source_matches_index'] else 'fact_snapshot_changed'
        return 'fact_changed', review
    if unchanged_fact:
        # The caller's immutable-revision check already proved the fact payload identical.
        # Renewing only retry metadata must not require a forbidden self-supersession.
        return None, review
    meta = args.get('lifecycle') or {}
    if not meta or meta.get('status', 'active') != 'active':
        return None, review
    snap = snapshot(db)
    scope = args.get('project_id') or ''
    day = meta.get('valid_from') or now()
    moments = {day}
    present = now()
    if started(meta, present) and not ended(meta, present):
        moments.add(present)
    for record in snap.docs.values():
        effective = record['meta'].get('valid_from')
        if (record['project_id'] == scope and identity(record['meta']) == identity(meta) and effective
                and instant(effective) >= instant(day) and not ended(meta, effective)):
            moments.add(effective)
    heads = set()
    # Check every known transition inside the proposed interval, including scheduled facts.
    # A bounded historical correction must not be forced to replace unrelated present state.
    for at in moments:
        with view('current', at):
            heads.update(r['name'] for did, r in snap.docs.items()
                         if r['project_id'] == scope and identity(r['meta']) == identity(meta)
                         and snap.eligible(did, at) and snap.resolve(did) == [did])
    missing = heads - set(meta.get('supersedes', []))
    if missing:
        review['unresolved_names'] = sorted(missing)
        return 'reconciliation_required', review
    return None, review


def history(db, name, scope=None, offset=0, limit=25):
    snap = snapshot(db)
    seeds = [d for d, r in snap.docs.items() if r['name'] == name and (scope is None or r['project_id'] == scope)]
    if len(seeds) != 1:
        return {'ok': False, 'error': {'code': 'ambiguous_or_missing', 'message': 'Specify the exact memory name and scope'}}
    seed = seeds[0]
    members = snap.history_members(seed)
    # Order lightweight metadata before fetching bodies or calculating diffs. The
    # full current-name summary still covers every revision, including other pages.
    def history_order(did):
        effective = snap.docs[did]['meta'].get('valid_from')
        at = instant(effective) if effective else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        ancestors, pending = set(), list(snap.prev.get(did, ()))
        while pending:
            previous = pending.pop()
            if previous not in ancestors:
                ancestors.add(previous)
                pending.extend(snap.prev.get(previous, ()))
        return at, len(ancestors), snap.docs[did]['name']
    ordered = sorted(members, key=history_order)
    states = {did: snap.info(did) for did in ordered}
    current = [snap.docs[did]['name'] for did in ordered if states[did].get('current')]
    bodies = {}
    def body_row(did):
        if did not in bodies:
            bodies[did] = db.execute('SELECT name,path,body,sha,evidence_date FROM docs WHERE id=?', (did,)).fetchone()
        return bodies[did]
    rows = []
    for did in ordered[offset:offset+limit]:
        r = body_row(did)
        record = dict(r, lifecycle=states[did])
        record['body_truncated'] = len(record['body']) > 4000
        record['body'] = record['body'][:4000]
        record['changes'] = []
        predecessors = sorted(snap.prev.get(did, ()))
        record['changes_truncated'] = len(predecessors) > 5
        for predecessor in predecessors[:5]:
            before = body_row(predecessor)
            delta = list(difflib.unified_diff(before['body'].splitlines(), r['body'].splitlines(),
                                            fromfile=before['name'], tofile=r['name'], lineterm=''))
            record['changes'].append({'from': before['name'], 'to': r['name'],
                                      'diff': '\n'.join(delta[:40])[:1000],
                                      'truncated': len(delta) > 40 or len('\n'.join(delta)) > 1000})
        rows.append(record)
    from brain import revisions
    observed = revisions.history(snap.docs[seed]['path'], snap.docs[seed]['project_id'], offset, limit)
    return {'ok': True, 'name': name, 'current_names': current, 'unresolved': not bool(current),
            'revisions': rows, 'document_history': observed, 'total': len(ordered), 'offset': offset,
            'has_more': offset + limit < len(ordered) or observed.get('has_more', False),
            'revision_has_more': offset + limit < len(ordered), 'history_complete': False,
            'branches': [dict(topic=b['topic'], facet=b['facet'],
                              names=[snap.docs[d]['name'] for d in b['members']])
                         for b in snap.history_branches(seed)],
            'coverage': 'same scoped fact with explicit predecessors; different facts sharing a legacy source are separate branches; unrecorded conversations are unknown'}


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
