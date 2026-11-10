"""Reproducible retrieval audit; outputs contain private data and must not be committed.

Supply --evaluation JSON cases: q, gold (name/list), optional terms, language,
intent, scope, view, as_of, forbidden (names), expect_no_answer. Exact gold names
are the default; --gold-match substring explicitly enables historical labels.
--snapshot pins the input DB; otherwise a read-only live DB is backed up first.
--reindex re-reads source files into the private copy with external sync disabled;
it refuses corpus drift. Use --config for the configured engines/source roots.
--compare checks identities before producing paired changes, never causal claims.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

ARMS = ('words', 'words_graph', 'meaning', 'mcp_auto', 'mcp_terms')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def percentile(values, q):
    """Nearest rank; unlike floor((n-1)*q), a three-item p95 includes the maximum."""
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * q) - 1)] if values else None


def labels(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else value


def matches(wanted, name, mode):
    return any(w == name if mode == 'exact' else w in name for w in labels(wanted))


def score(case, names, mode):
    rank = next((i + 1 for i, n in enumerate(names) if matches(case.get('gold'), n, mode)), None)
    return {'rank': rank, 'hit3': int(rank is not None and rank <= 3),
            'hit8': int(rank is not None and rank <= 8),
            'rr': 1.0 / rank if rank else 0.0,
            'forbidden': sum(matches(case.get('forbidden'), n, mode) for n in names),
            'abstained': not names}


def corpus_id(db):
    # File mtime/index timestamp are not content identity. Include scope and validity.
    return fingerprint([list(r) for r in db.execute(
        'SELECT name,path,sha,title,description,body,project_id,lifecycle,weight,prior '
        'FROM docs ORDER BY path')])


def paired(before, after):
    for key in ('corpus', 'evaluation', 'protocol', 'configuration', 'embedding'):
        if before['identity'][key] != after['identity'][key]:
            raise ValueError('Incomparable ' + key + '; keep data/configuration/protocol fixed')
    if len(before['cases']) != len(after['cases']):
        raise ValueError('Incomparable case count')
    result = {}
    for arm in after['arms']:
        wins, losses, diffs = [], [], []
        for i, (a, b) in enumerate(zip(before['cases'], after['cases'])):
            x, y = a['arms'][arm]['hit3'], b['arms'][arm]['hit3']
            diffs.append(y - x)
            if y > x:
                wins.append(i)
            elif y < x:
                losses.append(i)
        rng = random.Random(0)
        boot = [statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(1000)]
        result[arm] = {'improved_case_indices': wins, 'regressed_case_indices': losses,
                       'hit3_delta': statistics.mean(diffs),
                       'paired_bootstrap_95': [percentile(boot, .025), percentile(boot, .975)],
                       'p50_delta_ms': after['arms'][arm]['p50_ms'] - before['arms'][arm]['p50_ms']}
    return {'arms': result, 'meaning': 'Paired empirical uncertainty on these cases, not population or held-out validation.'}


def self_check():
    assert percentile([1, 2, 100], .95) == 100
    assert score({'gold': 'a', 'forbidden': ['old']}, ['old', 'a'], 'exact')['forbidden'] == 1
    assert not score({'gold': 'a'}, ['aa'], 'exact')['hit3']
    assert score({'gold': 'a'}, ['aa'], 'substring')['hit3']
    a = {'identity': {k: 'same' for k in ('corpus', 'evaluation', 'protocol', 'configuration', 'embedding')},
         'arms': {'words': {'p50_ms': 1}}, 'cases': [{'arms': {'words': {'hit3': 0}}}]}
    b = json.loads(json.dumps(a)); b['cases'][0]['arms']['words']['hit3'] = 1
    assert paired(a, b)['arms']['words']['improved_case_indices'] == [0]
    b['identity']['corpus'] = 'changed'
    try:
        paired(a, b)
    except ValueError:
        print('PASS metrics, paired wins/losses and rejection of changed data')
    else:
        raise AssertionError('Control: changed corpus must be rejected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--evaluation', type=Path)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--reindex', action='store_true')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--warmup', type=int, default=1)
    parser.add_argument('--arms', nargs='+', choices=ARMS, default=list(ARMS))
    parser.add_argument('--gold-match', choices=('exact', 'substring'), default='exact')
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check:
        self_check(); return 0
    if not args.evaluation or not args.output or args.repeat < 1 or args.warmup < 0:
        parser.error('evaluation/output required; repeat >= 1 and warmup >= 0')
    cases = json.loads(args.evaluation.read_text())
    if not isinstance(cases, list) or not cases:
        parser.error('evaluation must be a nonempty list')
    for c in cases:
        if (not isinstance(c, dict) or not isinstance(c.get('q'), str) or not c['q'].strip()
                or not (c.get('gold') or c.get('expect_no_answer'))):
            parser.error('each case needs q and gold, or explicit expect_no_answer')
        for key in ('gold', 'forbidden'):
            if any(not isinstance(x, str) or not x for x in labels(c.get(key))):
                parser.error(key + ' must contain nonempty names')
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / 'snapshot.db'
    if destination.exists():
        parser.error('output already has snapshot.db; use a new directory to preserve prior evidence')
    if args.config:
        os.environ['BRAIN_CONFIG'] = str(args.config.resolve())
    sys.path.insert(0, str(args.repo.resolve()))
    # Resolve the live source before isolating home; engine configuration is explicit.
    from brain import store, textindex, calibrate, vectors, search, server, lifecycle
    source = sqlite3.connect(args.snapshot.resolve().as_uri() + '?mode=ro', uri=True) if args.snapshot else store.connect(readonly=True)
    db = sqlite3.connect(str(destination)); source.backup(db); source.close(); db.row_factory = sqlite3.Row
    configuration = store.load_config()
    # Resolve the embedding manifest before moving auxiliary logs into an empty home.
    embedding = vectors.model_tag()
    manifest = Path(store.brain_home()) / 'embeddinggemma.json'
    if manifest.exists():
        os.environ.setdefault('BRAIN_EMBED_MANIFEST', str(manifest))
    initial = corpus_id(db)
    initial_code = calibrate.code_fingerprint()
    # Auxiliary logs and derived observations must not reach the user's home.
    with tempfile.TemporaryDirectory(prefix='brain-audit-home-') as isolated:
        os.environ['BRAIN_HOME'] = isolated
        if args.reindex:
            # Hold corpus membership fixed. Other sessions may add files while an
            # audit runs; those are not part of this snapshot. Changed/removed
            # snapshot records still fail the content-identity check below.
            held_paths = {r[0] for r in db.execute('SELECT path FROM docs')}
            original_iterator = store.iter_source_files
            store.iter_source_files = lambda cfg: (row for row in original_iterator(cfg) if row[0] in held_paths)
            try:
                store.reindex(db, configuration, full=True, recalibrate=False, refresh_history=False,
                              sync=False, heal=False, queue_embedding=False)
            finally:
                store.iter_source_files = original_iterator
            if corpus_id(db) != initial:
                raise ValueError('Source files changed the corpus during reindex; capture a new shared snapshot')
        if store.tokenizer_stale(db):
            raise ValueError('Snapshot tokenizer differs; use --reindex on the private copy')
        identity = {'corpus': initial, 'evaluation': fingerprint(cases),
                    'configuration': fingerprint(configuration), 'embedding': embedding,
                    'protocol': fingerprint({'arms': args.arms, 'repeat': args.repeat,
                                             'warmup': args.warmup, 'gold_match': args.gold_match,
                                             'personalization': 'measurement path', 'version': 2,
                                             'measurement_code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})}
        try:
            commit = subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', 'HEAD'], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = None
        records = []; all_names = [r[0] for r in db.execute('SELECT name FROM docs')]
        for i, c in enumerate(cases):
            entry = {'index': i, 'q': c['q'], 'gold': c.get('gold'),
                     'language': c.get('language', 'unlabelled'), 'intent': c.get('intent', 'unlabelled'),
                     'gold_present': any(matches(c.get('gold'), n, args.gold_match) for n in all_names), 'arms': {}}
            samples = {a: [] for a in args.arms}; rankings = {a: [] for a in args.arms}
            for turn in range(-args.warmup, args.repeat):
                # Rotate first/last position to reduce systematic cache/order advantage.
                shift = (i + turn + args.warmup) % len(args.arms)
                order = args.arms[shift:] + args.arms[:shift]
                for arm in order:
                    start = time.perf_counter(); extra = []; partial = False
                    with lifecycle.view(c.get('view', 'current'), c.get('as_of')):
                        if arm in ('words', 'words_graph'):
                            rows = search.recall(db, c['q'], k=8, graph=arm == 'words_graph', log=False, scope=c.get('scope'))
                            names = [r['name'] for r in rows if not r.get('related')]
                            extra = [r['name'] for r in rows if r.get('related')]
                        elif arm == 'meaning':
                            rows, why = search.meaning(db, c['q'], 8, scope=c.get('scope'))
                            names = [r['name'] for r in rows]; partial = bool(why)
                        else:
                            request = {'query': c['q'], 'k': 8, 'format': 'json'}
                            for key in ('scope', 'view', 'as_of'):
                                if key in c: request[key] = c[key]
                            if arm == 'mcp_terms': request['terms'] = c.get('terms', [])
                            response = json.loads(server.call_tool(db, 'recall', request, _measure=True))
                            if not response.get('ok'): raise ValueError(response)
                            names = [r['name'] for r in response.get('results', [])]
                            extra = [r['name'] for r in response.get('related', [])]
                            partial = bool(response.get('meta', {}).get('partial'))
                    elapsed = (time.perf_counter() - start) * 1000
                    if turn >= 0:
                        samples[arm].append(elapsed); rankings[arm].append(names)
                        if turn == 0:
                            entry['arms'][arm] = dict(score(c, names, args.gold_match), names=names,
                                                     related=extra, partial=partial)
            for arm in args.arms:
                entry['arms'][arm].update(ms=samples[arm], stable=all(n == rankings[arm][0] for n in rankings[arm]))
            records.append(entry)
            if (i + 1) % 10 == 0: print('measured', i + 1, '/', len(cases), flush=True)
        report = {'schema': 2, 'identity': identity, 'commit': commit,
                  'code_fingerprint': initial_code, 'tokenizer': textindex.TOKENIZER_VERSION,
                  'n': len(cases), 'corpus_docs': len(all_names), 'cases': records, 'arms': {}, 'groups': {}}
        for arm in args.arms:
            rs = [r['arms'][arm] for r in records]; timing = [t for r in rs for t in r['ms']]
            answerable = [r for r, c in zip(rs, cases) if c.get('gold')]
            report['arms'][arm] = {'answerable': len(answerable), 'hit3': sum(r['hit3'] for r in answerable),
                'hit8': sum(r['hit8'] for r in answerable), 'mrr': statistics.mean(r['rr'] for r in answerable) if answerable else None,
                'p50_ms': statistics.median(timing), 'p95_ms': percentile(timing, .95),
                'forbidden_exposures': sum(r['forbidden'] for r in rs), 'partial_cases': sum(r['partial'] for r in rs),
                'unstable_cases': sum(not r['stable'] for r in rs),
                'abstention_correct': sum(r['abstained'] for r, c in zip(rs, cases) if c.get('expect_no_answer')),
                'abstention_cases': sum(bool(c.get('expect_no_answer')) for c in cases)}
            for dimension in ('language', 'intent'):
                for value in sorted({r[dimension] for r in records}):
                    subset = [r for r in records if r[dimension] == value]
                    report['groups'].setdefault(dimension + ':' + value, {})[arm] = {
                        'n': len(subset), 'hit3': sum(r['arms'][arm]['hit3'] for r in subset),
                        'forbidden_exposures': sum(r['arms'][arm]['forbidden'] for r in subset)}
        report['missing_gold_indices'] = [i for i, (r, c) in enumerate(zip(records, cases)) if c.get('gold') and not r['gold_present']]
        report['limitations'] = ['Local personal labels are not a global-language benchmark.',
            'First measured repetition determines quality; repeated queries are not independent labels.',
            'Warmup and rotated ordering reduce but do not eliminate cache effects; not cold-start latency.',
            'Measurement path excludes policy reranking/logging; feedback weights in the snapshot remain.',
            'External databases are live read paths, not frozen copies; partial results are reported.',
            'A name hit is evidence retrieval, not answer factual accuracy; forbidden labels must be supplied.',
            'Bootstrap intervals describe this sample, not held-out model selection.']
        if calibrate.code_fingerprint() != initial_code:
            raise ValueError('Code changed during measurement; repeat against a stable checkout')
        if args.compare:
            report['comparison'] = paired(json.loads(args.compare.read_text()), report)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k: report[k] for k in ('n', 'arms', 'missing_gold_indices')}, indent=2))
    db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
