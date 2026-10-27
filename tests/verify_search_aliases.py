#!/usr/bin/env python3
"""Explicit memory vocabulary is document-scoped, replaceable and backward-compatible."""
import json, os, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
tmp=tempfile.TemporaryDirectory(prefix='brain-aliases-')
os.environ['BRAIN_HOME']=str(Path(tmp.name)/'home')
os.environ['BRAIN_CONFIG']=str(Path(tmp.name)/'config.json')
from brain import store, search, server
import _stores_fixture as fx
fx.write_corpus(tmp.name)
db=store.connect()
args={'name':'vocabulary_note','description':'fixture vocabulary','type':'memory','body':'A rule for the orchard.'}
server._do_remember(db,args)
assert not any(r['name']=='vocabulary_note' for r in search.recall(db,'zephyrword',log=False))
args['search_aliases']=['zephyrword',' zephyrword ']
server._do_remember(db,args)
assert any(r['name']=='vocabulary_note' for r in search.recall(db,'zephyrword',log=False))
p=Path(server._memory_dir())/'vocabulary_note.md'
assert json.loads(store.parse_frontmatter(p.read_text(encoding="utf-8"))[0]['search_aliases'])==['zephyrword']
args['search_aliases']=[]
server._do_remember(db,args)
assert not any(r['name']=='vocabulary_note' for r in search.recall(db,'zephyrword',log=False))
print('PASS: alias-free control, explicit lookup, normalised deduplication, removal')
db.close()
