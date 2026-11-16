"""Paired native-host audit. A plan is inert until --execute is supplied.

Plans and results are private: commands, prompts and transcripts may identify work.
This measures explicitly configured CLI sessions, not simulated grep baselines.
Configuration matching is a declared control; review traces before causal claims.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time

HOSTS = ('codex', 'claude')
ARMS = ('native', 'brain')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def corpus_hash(root, names):
    result = {}
    for name in names:
        path = (root / name).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise ValueError('Corpus entries must be files inside the declared workspace')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(result)


def write_report(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(temporary), str(path))


def validate(plan):
    if set(plan.get('hosts', {})) != set(HOSTS):
        raise ValueError('Both codex and claude need explicit host configurations')
    if type(plan.get('repeats')) is not int or plan['repeats'] < 2:
        raise ValueError('Use at least two repetitions so arm order can alternate')
    if not isinstance(plan.get('timeout_s'), (int, float)) or not 0 < plan['timeout_s'] <= 3600:
        raise ValueError('Choose a positive timeout of at most 3600 seconds per trial')
    if not plan.get('corpus_files') or not plan.get('cases'):
        raise ValueError('Declare frozen corpus files and nonempty cases')
    ids = []
    for case in plan['cases']:
        ids.append(case['id'])
        if not isinstance(case.get('question'), str) or not case['question'].strip() or not isinstance(case.get('expected'), dict) or not case['expected']:
            raise ValueError('Each case needs a question and independently prepared expected JSON fields')
        if not isinstance(case.get('obsolete', {}), dict):
            raise ValueError('Obsolete values must be keyed by answer field, not prose substring matches')
    if len(set(ids)) != len(ids):
        raise ValueError('Case IDs must be unique')
    for host in HOSTS:
        cfg = plan['hosts'][host]
        for key in ('model', 'cli_version', 'reasoning', 'permissions', 'native_memory', 'settings_identity'):
            if not isinstance(cfg.get(key), str) or not cfg[key].strip():
                raise ValueError('Declare each host control: ' + key)
        if cfg.get('native_memory_preserved') is not True:
            raise ValueError('Do not weaken the native baseline by removing its normal memory')
        common = cfg.get('command')
        if not isinstance(common, list) or not common or any(not isinstance(x, str) or not x for x in common):
            raise ValueError('Host command must be an explicit argument array')
        for arm in ARMS:
            extra = cfg.get(arm + '_args')
            if not isinstance(extra, list) or any(not isinstance(x, str) for x in extra):
                raise ValueError('Declare native_args and brain_args; only integration should differ')
            forbidden = ('-m', '--model', '--effort', '--sandbox', '-s', '--permission-mode', '--profile', '-p', '--settings', '--setting-sources')
            if any(x in forbidden or any(x.startswith(flag+'=') for flag in forbidden) or x.startswith(('model=', 'model_reasoning_effort=', 'sandbox_mode=', 'approval_policy=')) for x in extra):
                raise ValueError('Model, reasoning, permissions and base settings belong in the shared command')
            if any(x in ('--bare', '--ignore-user-config') for x in common + extra):
                raise ValueError('A stripped host is not the normal native-memory baseline')
        for arm in ARMS:
            env = cfg.get(arm + '_env', {})
            if not isinstance(env, dict) or set(env) - {'BRAIN_HOOK_DISABLED'} or any(not isinstance(v, str) for v in env.values()):
                raise ValueError('Only BRAIN_HOOK_DISABLED may differ in the per-arm environment')
        if cfg.get('format') not in ('codex_jsonl', 'claude_json', 'adapter_json'):
            raise ValueError('Unknown output format')
    return plan


def parse_output(raw, output_format):
    """Keep missing usage unknown; never compare unlike host token definitions."""
    if output_format == 'adapter_json':
        value = json.loads(raw)
        if not isinstance(value, dict):raise ValueError('Adapter output must be an object')
        return value['answer'], value.get('usage'), value.get('model'), value.get('error')
    if output_format == 'claude_json':
        value = json.loads(raw)
        if not isinstance(value, dict):raise ValueError('Claude output must be an object')
        return value.get('result', ''), value.get('usage'), value.get('model'), value.get('error') or (value.get('subtype') if value.get('is_error') else None)
    answer, usage, error = None, None, None
    for line in raw.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):raise ValueError('Codex events must be objects')
        item = event.get('item', {})
        if not isinstance(item, dict):raise ValueError('Codex items must be objects')
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            answer = item.get('text')
        if event.get('type') == 'turn.completed':
            usage = event.get('usage')
        if event.get('type') in ('error', 'turn.failed'):
            error = event.get('message') or event.get('error') or 'host_failure'
    return answer, usage, None, error


def grade(answer, case):
    try:
        value = json.loads(answer) if isinstance(answer, str) else answer
    except ValueError:
        return dict(correct=False, obsolete=None, parse_ok=False)
    if not isinstance(value, dict):
        return dict(correct=False, obsolete=None, parse_ok=False)
    fields = {key: key in value and digest(value[key]) == digest(expected) for key, expected in case['expected'].items()}
    return dict(correct=all(fields.values()), field_correct=fields,
                obsolete=any(key in value and value[key] in values for key, values in case.get('obsolete', {}).items()), parse_ok=True)


def run_trial(command, prompt, cwd, directory, timeout, env):
    directory.mkdir()
    start = time.monotonic()
    timed_out = False
    interrupted = False
    with (directory / 'stdout').open('w') as out, (directory / 'stderr').open('w') as err:
        try:
            process = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.PIPE, stdout=out, stderr=err,
                                       text=True, encoding="utf-8", env=env, start_new_session=(os.name != 'nt'))
            try:
                process.communicate(prompt, timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as stop:
                timed_out = isinstance(stop, subprocess.TimeoutExpired)
                interrupted = isinstance(stop, KeyboardInterrupt)
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)
            code = process.returncode
        except OSError as error:
            return dict(exit_code=None, timed_out=False, seconds=time.monotonic()-start, launch_error=str(error))
    return dict(exit_code=code, timed_out=timed_out, interrupted=interrupted, seconds=time.monotonic()-start)


def summarize(rows):
    result = {}
    for host in HOSTS:
        arms = {}
        for arm in ARMS:
            selected = [r for r in rows if r['host'] == host and r['arm'] == arm]
            durations = sorted(r['seconds'] for r in selected)
            usage_keys = {key for r in selected for key, value in (r.get('usage') or {}).items() if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)}
            usage_summary = {}
            for key in sorted(usage_keys):
                values = [r['usage'][key] for r in selected if isinstance((r.get('usage') or {}).get(key), (int, float)) and not isinstance(r['usage'][key], bool) and math.isfinite(r['usage'][key])]
                usage_summary[key] = dict(observed=len(values), mean=statistics.mean(values))
            field_names = {key for r in selected for key in r.get('field_correct', {})}
            field_summary = {key: dict(graded=sum(key in r.get('field_correct', {}) for r in selected), correct=sum(r['status'] == 'ok' and r.get('field_correct', {}).get(key, False) for r in selected)) for key in sorted(field_names)}
            arms[arm] = dict(answer_fields=field_summary, usage_fields=usage_summary,attempted=len(selected), correct=sum(r.get('correct', False) for r in selected),
                             failed=sum(r['status'] != 'ok' for r in selected), obsolete=sum(r.get('obsolete') is True for r in selected),
                             ungraded=sum(not r.get('parse_ok', False) for r in selected),
                             p50_seconds=statistics.median(durations) if durations else None,
                             p95_seconds=durations[max(0, math.ceil(.95*len(durations))-1)] if durations else None,
                             usage_observed=sum(r.get('usage') is not None for r in selected))
        pairs = {}
        for row in rows:
            if row['host'] == host:
                pairs.setdefault((row['case'], row['repeat']), {})[row['arm']] = row
        wins = losses = ties = incomplete = 0
        deltas = []
        for pair in pairs.values():
            if set(pair) != set(ARMS):
                incomplete += 1; continue
            a, b = pair['native'], pair['brain']
            delta = int(b.get('correct', False))-int(a.get('correct', False))
            wins += delta > 0; losses += delta < 0; ties += delta == 0
            if a['status'] == b['status'] == 'ok':
                deltas.append(b['seconds']-a['seconds'])
        result[host] = dict(arms=arms, paired_wins=wins, paired_losses=losses, paired_ties=ties,
                            incomplete_pairs=incomplete, successful_pair_time_deltas_seconds=deltas)
    return result


def execute(plan, output):
    root = Path(plan['workspace']).resolve()
    output = output.resolve()
    if output == root or root in output.parents:
        raise ValueError('Keep grading labels and transcripts outside the agent workspace')
    frozen = corpus_hash(root, plan['corpus_files'])
    ruler = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    (output/'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = []
    planned = plan['repeats']*len(plan['cases'])*4
    initial = dict(plan_sha256=digest(plan),corpus_sha256=frozen,ruler_sha256=ruler,planned_trials=planned,recorded_trials=0,not_recorded_trials=planned,summary=summarize(rows))
    write_report(output/'report.json', initial)
    for repeat in range(plan['repeats']):
        for case_index, case in enumerate(plan['cases']):
            for host in HOSTS:
                cfg = plan['hosts'][host]
                order = ARMS if (repeat+case_index)%2 == 0 else tuple(reversed(ARMS))
                for arm in order:
                    directory = output / ('%04d' % len(rows))
                    command = cfg['command'] + cfg[arm+'_args']
                    prompt = case['question']
                    initial.update(active_trial=dict(host=host,arm=arm,case=case['id'],repeat=repeat))
                    write_report(output/'report.json', initial)
                    before = corpus_hash(root, plan['corpus_files'])
                    env = dict(os.environ, ORBIT='off')
                    env.update(cfg.get(arm+'_env', {}))
                    run = run_trial(command, prompt, root, directory, plan['timeout_s'], env)
                    row = dict(host=host, arm=arm, case=case['id'], repeat=repeat, language=case.get('language','unspecified'), intent=case.get('intent','unspecified'), declared_model=cfg['model'],
                               transcript=str(directory), **run)
                    row['status'] = 'ok' if run['exit_code'] == 0 and not run['timed_out'] else 'host_failure'
                    try:
                        stdout = directory/'stdout'
                        if stdout.stat().st_size > 64*1024*1024:
                            raise ValueError('Transcript exceeds parsing limit; raw file retained')
                        raw = stdout.read_text(encoding='utf-8')
                        answer, usage, model, error = parse_output(raw, cfg['format'])
                        row.update(answer=answer, usage=usage, observed_model=model,
                                   transcript_sha256=hashlib.sha256(raw.encode()).hexdigest())
                        if error:row.update(status='host_failure', host_error=error)
                        row.update(grade(answer, case))
                        if not row['parse_ok'] and row['status'] == 'ok':row['status'] = 'output_error'
                        if usage is not None and not isinstance(usage, dict):raise ValueError('Usage must be an object or unknown')
                    except (ValueError, KeyError, TypeError, OSError) as error:
                        row.update(status='output_error', parse_error=str(error), correct=False, parse_ok=False,usage=None)
                    try:
                        after = corpus_hash(root, plan['corpus_files'])
                    except (OSError, ValueError) as error:
                        after = None
                        row['corpus_error'] = str(error)
                    if before != frozen or after != frozen:
                        row.update(status='corpus_changed', correct=False)
                    if row['status'] != 'ok':row['correct'] = False
                    rows.append(row)
                    with (output/'trials.jsonl').open('a', encoding='utf-8') as file:file.write(json.dumps(row, ensure_ascii=False)+'\n')
                    report = dict(ruler_sha256=ruler, plan_sha256=digest(plan), corpus_sha256=frozen,
                                  planned_trials=planned, recorded_trials=len(rows),not_recorded_trials=planned-len(rows),
                                  summary=summarize(rows), by_language={language:summarize([r for r in rows if r['language']==language]) for language in sorted({r['language'] for r in rows})}, by_intent={intent:summarize([r for r in rows if r['intent']==intent]) for intent in sorted({r['intent'] for r in rows})}, interpretation='Descriptive paired observations. Configuration controls are declared, not proven. Independently review traces, source access, native-memory preservation and answer labels before causal claims. Repetitions are not independent new questions. Usage fields retain provider definitions; missing is unknown.')
                    write_report(output/'report.json', report)
                    initial=report
                    if row['status']=='corpus_changed' or run.get('interrupted'):return report
    return report


def self_check():
    assert grade('{"day":"Friday","previous":"Monday"}',dict(expected={'day':'Friday'},obsolete={'day':['Monday']}))['obsolete'] is False
    assert grade('{"day":"Monday"}',dict(expected={'day':'Friday'},obsolete={'day':['Monday']}))['obsolete'] is True
    assert not grade({'count': True}, dict(expected={'count': 1}))['correct']
    assert grade({'current': 'Friday', 'previous': 'wrong'}, dict(expected={'current': 'Friday', 'previous': 'Monday'}))['field_correct'] == {'current': True, 'previous': False}
    assert not grade('invalid',dict(expected={'day':'Friday'}))['correct']
    raw='{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}\n{"type":"turn.completed","usage":{"input_tokens":7}}'
    assert parse_output(raw,'codex_jsonl')[:2]==('answer',{'input_tokens':7})
    assert parse_output('{"result":"answer","is_error":true,"subtype":"limit"}','claude_json')[3]=='limit'
    rows=[dict(host='codex',arm=arm,case='one',repeat=0,seconds=1,status='ok',correct=(arm=='brain'),obsolete=False,parse_ok=True) for arm in ARMS]
    report=summarize(rows)['codex'];assert report['paired_wins']==1 and report['arms']['native']['usage_observed']==0
    rows[1].update(status='host_failure',correct=False)
    assert summarize(rows)['codex']['arms']['brain']['failed']==1
    assert summarize(rows[:1])['codex']['incomplete_pairs']==1
    import tempfile
    with tempfile.TemporaryDirectory(prefix='brain-host-audit-') as temporary:
        root = Path(temporary)/'workspace'
        root.mkdir()
        (root/'notes.txt').write_text('The meeting moved from Monday to Friday.')
        code = "import sys,json;sys.stdin.read();print(json.dumps({'answer':{'day':'Friday' if sys.argv[-1]=='brain' else 'Monday'},'usage':{'input_tokens':10},'model':'synthetic'}))"
        config = dict(command=[sys.executable,'-c',code],native_args=['native'],brain_args=['brain'],
                      model='synthetic',cli_version='fixture',reasoning='none',permissions='synthetic control',native_memory='notes.txt',
                      settings_identity='fixture',native_memory_preserved=True,format='adapter_json')
        plan = dict(workspace=str(root),corpus_files=['notes.txt'],cases=[dict(id='schedule',question='Return JSON with day.',expected={'day':'Friday'},obsolete={'day':['Monday']})],
                    hosts={host:dict(config) for host in HOSTS},repeats=2,timeout_s=10,controls_reviewed=True)
        validate(plan)
        result = execute(plan,root.parent/'results')
        assert result['recorded_trials']==result['planned_trials']==8
        assert all(result['summary'][h]['paired_wins']==2 for h in HOSTS)
        assert all(result['summary'][h]['arms']['native']['obsolete']==2 for h in HOSTS)
        trials=[json.loads(line) for line in (root.parent/'results/trials.jsonl').read_text().splitlines()]
        assert [r['arm'] for r in trials if r['host']=='codex']==['native','brain','brain','native']
        assert all(Path(r['transcript'],'stdout').exists() for r in trials)
        timeout=run_trial([sys.executable,'-c','import time;time.sleep(5)'],'',root,root/'timeout',.05,dict(os.environ))
        assert timeout['timed_out'] and timeout['exit_code']!=0
        bad=json.loads(json.dumps(plan));bad['hosts']['codex']['native_memory_preserved']=False
        try:validate(bad)
        except ValueError:pass
        else:raise AssertionError('A weakened native baseline was accepted')
        drift="import pathlib,json;pathlib.Path('notes.txt').write_text('changed');print(json.dumps({'answer':{'day':'Friday'}}))"
        plan['hosts']={host:dict(config,command=[sys.executable,'-c',drift]) for host in HOSTS}
        result=execute(plan,root.parent/'drift')
        assert result['recorded_trials']==1 and result['not_recorded_trials']==7
        assert result['summary']['codex']['arms']['native']['correct']==0 and result['summary']['codex']['arms']['native']['failed']==1
    print('PASS structured grading, obsolete values, output parsing, failures, unknown usage, real subprocess receipts, alternating pairs, timeout, corpus drift and baseline guard')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--execute',action='store_true',help='Run the explicitly configured native CLI commands; consumes their quotas')
    parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--example',action='store_true',help='Print an editable plan template; never executes')
    args=parser.parse_args()
    if args.self_check:self_check();return
    if args.example:
        hosts={}
        for host in HOSTS:
            hosts[host]=dict(model='PIN_MODEL',cli_version='RECORD_VERSION',reasoning='RECORD_REASONING',permissions='read-only',native_memory='RECORD_EQUIVALENT_INITIAL_MEMORY',settings_identity='RECORD_REVIEWED_SETTINGS_HASH',native_memory_preserved=True,
                             command=(['codex','exec','--json','--model','PIN_MODEL','--sandbox','read-only','-'] if host=='codex' else ['claude','--print','--output-format','json','--model','PIN_MODEL','--permission-mode','dontAsk']),
                             native_args=(['-c','mcp_servers.brain.enabled=false'] if host=='codex' else ['--disallowedTools','mcp__brain__*']),brain_args=(['-c','mcp_servers.brain.enabled=true'] if host=='codex' else []),
                             native_env={'BRAIN_HOOK_DISABLED':'1'},brain_env={'BRAIN_HOOK_DISABLED':''},format=('codex_jsonl' if host=='codex' else 'claude_json'))
        print(json.dumps(dict(workspace='/path/to/isolated-workspace',corpus_files=['notes/schedule.md'],repeats=2,timeout_s=180,controls_reviewed=False,hosts=hosts,
                             cases=[dict(id='schedule',language='en',intent='current_and_history',question='Read notes/schedule.md. Return only JSON with day and previous_day.',expected={'day':'Friday','previous_day':'Monday'},obsolete={'day':['Monday']})]),indent=2));return
    if not args.plan:parser.error('--plan is required')
    plan=validate(json.loads(args.plan.read_text(encoding='utf-8')))
    identity=corpus_hash(Path(plan['workspace']).resolve(),plan['corpus_files'])
    if not args.execute:
        print(json.dumps(dict(valid=True,corpus_sha256=identity,planned_trials=plan['repeats']*len(plan['cases'])*4,executed=False)));return
    if not args.output:parser.error('--output is required with --execute')
    workspace=Path(plan['workspace']).resolve()
    if args.plan.resolve()==workspace or workspace in args.plan.resolve().parents:parser.error('Keep the plan and hidden answer labels outside the agent workspace')
    if plan.get('controls_reviewed') is not True:parser.error('Review model, settings, normal memory, integration and source isolation; set controls_reviewed=true')
    report=execute(plan,args.output)
    print(json.dumps(report['summary'],indent=2))


if __name__=='__main__':main()
