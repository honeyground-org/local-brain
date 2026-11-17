"""Read-only review of indexed validity, not semantic truth or automatic migration."""
from collections import Counter
import hashlib
import json

from brain import lifecycle


def _cycles(snap, members):
    """Iterative strongly connected components; long histories need no recursion."""
    seen, order = set(), []
    for root in members:
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(snap.next.get(root, ())))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child in members and child not in seen:
                seen.add(child)
                stack.append((child, iter(snap.next.get(child, ()))))
    seen, cycles = set(), set()
    for root in reversed(order):
        if root in seen:
            continue
        group, pending = set(), [root]
        seen.add(root)
        while pending:
            node = pending.pop()
            group.add(node)
            for parent in snap.prev.get(node, ()):
                if parent in members and parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        if len(group) > 1:
            cycles.update(group)
    return cycles


def _error(code, message):
    return {'ok': False, 'error': {'code': code, 'message': message}}


def review(db, args):
    if not isinstance(args, dict) or set(args) - {'scope', 'as_of', 'offset', 'limit', 'only_attention', 'expected_snapshot'}:
        return _error('invalid_argument', 'Use only declared memory_review fields')
    offset, limit = args.get('offset', 0), args.get('limit', 25)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        return _error('invalid_argument', 'offset must be nonnegative and limit must be 1..100')
    if 'scope' in args and not isinstance(args['scope'], str):
        return _error('invalid_argument', 'scope must be a string; empty means unscoped only')
    if type(args.get('only_attention', True)) is not bool:
        return _error('invalid_argument', 'only_attention must be boolean')
    expected = args.get('expected_snapshot')
    if expected is not None and (not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected)):
        return _error('invalid_argument', 'expected_snapshot must be a SHA-256')
    if (offset or expected) and (not expected or 'as_of' not in args):
        return _error('invalid_argument', 'Continue with both expected_snapshot and as_of from the first page')
    try:
        at = lifecycle.instant(args.get('as_of', lifecycle.now())).isoformat()
    except (ValueError, TypeError):
        return _error('invalid_argument', 'as_of must be an ISO date or timestamp with timezone')
    # Keep the metadata query and lifecycle snapshot on one SQLite read snapshot.
    db.execute('SAVEPOINT memory_review')
    try:
        return _review(db, args, at, offset, limit, expected)
    finally:
        db.execute('RELEASE memory_review')


def _review(db, args, at, offset, limit, expected):
    columns = 'id,name,path,sha,source,kind,project_id,lifecycle'
    sql, params = 'SELECT ' + columns + ' FROM docs', ()
    if 'scope' in args:
        sql += ' WHERE project_id=?'
        params = (args['scope'],)
    records = [dict(r) for r in db.execute(sql + ' ORDER BY id', params)]
    token = hashlib.sha256(json.dumps([at, args.get('scope'), args.get('only_attention', True), records],
                                     sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    if expected and token != expected:
        return _error('snapshot_changed', 'Indexed metadata changed; restart at offset 0')
    snap = lifecycle.snapshot(db)
    cycles = _cycles(snap, {r['id'] for r in records})
    states, reasons, kinds, sources = Counter(), Counter(), Counter(), Counter()
    rows, attention, current = [], 0, 0
    with lifecycle.view('current', at):
        for record in records:
            did = record['id']
            meta = snap.docs[did]['meta']
            info = snap.info(did)
            flags = []
            if did in cycles:
                flags.append('revision_cycle')
            if info.get('issue') or meta.get('status') == 'invalid':
                flags.append('invalid_lifecycle')
            if info['status'] == 'untracked':
                flags.append('untracked_validity')
            if info['status'] == 'disputed':
                flags.append('unresolved_conflict')
            if info['status'] == 'proposed':
                flags.append('pending_confirmation')
            if info['status'] in ('active', 'proposed', 'disputed'):
                if not meta.get('evidence'):
                    flags.append('missing_recorded_evidence')
                if not meta.get('valid_from'):
                    flags.append('missing_effective_start')
            if not record['kind'].strip():
                flags.append('missing_kind')
            states[info['status']] += 1
            kinds[record['kind']] += 1
            sources[record['source']] += 1
            reasons.update(flags)
            attention += bool(flags)
            current += info["current"] is True
            if flags or not args.get('only_attention', True):
                row = {k: record[k] for k in ('id', 'name', 'path', 'sha', 'source', 'kind', 'project_id')}
                row.update(status=info['status'], current=info['current'], topic=meta.get('topic'), facet=meta.get('facet'),
                           reasons=flags, issue=info.get('issue'), valid_from=meta.get('valid_from'), valid_to=meta.get('valid_to'),
                           evidence_count=len(meta.get('evidence', [])))
                for key in ('current_names', 'conflicts'):
                    row[key] = info[key][:10]
                    row[key + '_total'] = len(info[key])
                rows.append(row)
    # Structural errors first, then unresolved decisions, then untracked/metadata gaps.
    priorities = {'revision_cycle': 0, 'invalid_lifecycle': 0, 'unresolved_conflict': 1, 'pending_confirmation': 2}
    rows.sort(key=lambda r: (min((priorities.get(f, 3) for f in r['reasons']), default=4), r['project_id'], r['name'], r['id']))
    return {'ok': True, 'snapshot': token, 'as_of': at, 'scope': args.get('scope'),
            'coverage': 'indexed metadata only; no source refresh, body inspection, semantic contradiction detection or truth certification',
            'counts': {'documents': len(records), 'attention': attention, 'current': current, 'states': dict(states), 'reasons': dict(reasons),
                       'kinds': dict(kinds), 'sources': dict(sources)},
            'offset': offset, 'limit': limit, 'total': len(rows), 'has_more': offset + limit < len(rows),
            'items': rows[offset:offset + limit],
            'next': 'Read each source and memory_history before deciding. Discover related facts with recall; reuse stable scope/topic/facet. Use prepare_revision only for confirmed replacement; split mixed notes without retiring unrelated facts. Missing metadata is not proof of falsehood. Never merge kinds or identities solely by spelling, translation or similarity.'}
