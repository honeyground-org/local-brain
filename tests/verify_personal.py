#!/usr/bin/env python3
"""Policy mechanics only: independent evidence, held-out rejection, rollback and isolation.
Synthetic rankings prove the guard can fail, not that retrieval quality improved.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
tmp = tempfile.TemporaryDirectory(prefix="brain-personal-")
os.environ['BRAIN_HOME'] = tmp.name
os.environ['BRAIN_CONFIG'] = str(Path(tmp.name) / 'config.json')
from brain import personal, store

def evidence(db, n, winner, start=0):
    personal.ensure(db)
    with db:
        db.execute("INSERT OR IGNORE INTO docs(path,name,source,sha) VALUES('fixture','fixture','memory','sha')")
        for i in range(start, start+n):
            scores = {a: int(a == winner) for a in personal.ARMS}
            db.execute('INSERT INTO personal_events(identity,session,turn,query,path,sha,exposed,measured,scores,costs) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (str(i), 'session-'+str(i), 1, 'fixture', 'fixture', 'sha', 0, 1, json.dumps(scores),
                        json.dumps({a: .01 for a in personal.ARMS})))

def main():
    db = store.connect()
    assert personal.state(db)['arm'] == 'baseline'
    assert personal.update(db)['decision'] == 'insufficient'
    # Too few wins cannot promote, despite every observed outcome preferring a challenger.
    evidence(db, 2, 'fusion')
    assert personal.update(db)['arm'] == 'baseline'
    evidence(db, 14, 'fusion', 2)
    assert personal.update(db)['arm'] == 'fusion'
    assert personal.update(db)['decision'] == 'insufficient'  # consumed evidence cannot add confidence
    assert personal.state(db, 'terms')['arm'] == 'baseline'  # contexts cannot share a winner
    original = personal.candidates
    personal.candidates = lambda *args, **kwargs: ['candidate']
    assert personal.apply(db, 'fixture', ['baseline'], 8) == ['baseline']
    os.environ['BRAIN_PERSONAL_EXPERIMENT'] = '1'
    assert personal.apply(db, 'fixture', ['baseline'], 8) == ['candidate']
    os.environ['BRAIN_PERSONAL_DISABLED'] = '1'
    assert personal.apply(db, 'fixture', ['baseline'], 8) == ['baseline']
    del os.environ['BRAIN_PERSONAL_DISABLED']
    del os.environ['BRAIN_PERSONAL_EXPERIMENT']
    personal.candidates = original
    evidence(db, 16, 'baseline', 16)
    assert personal.update(db)['arm'] == 'baseline'  # new independent harm rolls back
    # A candidate winning training and losing holdout must fail.
    evidence(db, 8, 'linked', 32)
    evidence(db, 8, 'baseline', 40)
    result = personal.update(db)
    assert result['arm'] == 'baseline'
    assert personal.state(db)['history'][-1]['reason'] == 'holdout_rejected'
    # Editing a turn withdraws its behavioral signal.
    personal.edited(db, 'session-47', 1)
    assert db.execute('SELECT edited FROM personal_events WHERE identity="47"').fetchone()[0] == 1
    # Another user's home starts with no inherited policy or observations.
    os.environ['BRAIN_HOME'] = str(Path(tmp.name)/'another')
    other = store.connect()
    assert personal.state(other)['arm'] == 'baseline'
    assert personal.update(other)['observations'] == 0
    other.close(); db.close()
    print('PASS: weak evidence retained, independent holdout promoted, drift rolled back, users isolated')

if __name__ == '__main__':
    main()
