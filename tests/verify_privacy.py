"""The outgoing door check — ★does a secret ever leave over the network★.

## Why this check exists

Measured 2026-08-26: of 619 memories, 21 had a credential pattern, and a 420-character excerpt from
one of them ★actually went out to the judge★. The only defence was one source-level `no_embed` switch.

⛔ **The most dangerous failure is a silent one** — masking can be missing with no error, and the fact
   that it went out leaves no trace in the log. So this check ★intercepts HTTP and looks at the string that
   actually leaves★. It asks not "was scrub called" but "is there a secret in what went out."
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The outbound doors are tested with an engine ★named★ (a key alone chooses nothing, §engines) — the calls
# are intercepted, nothing leaves.
os.environ.setdefault("BRAIN_JUDGE_PROVIDER", "gemini")
os.environ.setdefault("BRAIN_EMBED_PROVIDER", "gemini")
from brain import privacy   # noqa: E402

FAIL = []


def check(ok, label, detail=""):
    print("%s %s%s" % ("✅" if ok else "❌", label, ("  " + detail) if detail else ""))
    if not ok:
        FAIL.append(label)
    return ok


def head(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


SECRET_IN_TRANSIT = [
    ("credential URI", re.compile(r"\b(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis)"
                            r"://[^\s`\"'<>]*@[^\s`\"'<>]+")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("PEM", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("an AI provider's key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("a GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("a JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ")),
    ("a secret assignment", re.compile(r"(?i)\b(password|secret|api[_-]?key|token)\s*[:=]\s*"
                          r"['\"]?[A-Za-z0-9._\-]{8,}")),
]

# ⛔⛔ ★fixtures are joined from pieces — never a literal sitting whole in the file★ (2026-09-03)
#
# All invented values, but ★shaped exactly like the real thing★. Write one whole in the file and
# GitHub's secret scanning refuses the push itself (push protection), and that alert singles out
# this repo and org by name. What's under test is whether `privacy.scrub` catches ★a completed
# string★, so joining it from pieces leaves the test exactly as valid.
_AWS = "AKIA" + "ZZ1234567890ABCD"
_GH = "ghp_" + "abcdefghijklmnopqrstuvwxyz012345"
_URI = "mongodb://admin:" + "S3cr3tPass99" + "@db.example.com:27017/appdb"
_PEM_B, _PEM_E = "-----BEGIN RSA PRIVATE" + " KEY-----", "-----END RSA PRIVATE" + " KEY-----"
# the AI providers' own keys (2026-10-10 — they went out whole; only `api_key = …` was caught)
_OPENAI = "sk-" + "proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z_AbCdEfGh"
_ANTHROPIC = "sk-" + "ant-api03-" + "x7Y2Q" * 16
_GOOGLE = "AI" + "za" + "Sy0123456789abcdefghijklmnopqrstuvw"
_GH_PAT = "github" + "_pat_" + "11ABCDEFG0" * 4
_JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"

DIRTY = (
    "connect using %s.\n" % _URI
    + "AWS_ACCESS_KEY_ID=%s\n" % _AWS
    + "export GH_TOKEN=%s\n" % _GH
    + "password: hunter2secret\n"
    + "%s\nMIIEow\n%s\n" % (_PEM_B, _PEM_E)
    + "the OpenAI key we rotated was %s and Anthropic's %s\n" % (_OPENAI, _ANTHROPIC)
    + "maps: %s · deploy with %s · the session cookie held %s\n" % (_GOOGLE, _GH_PAT, _JWT)
)
CLEAN = (
    "the test uses mongodb://localhost:27117/test. ES is at 198.51.100.7:9200.\n"
    "token: str, api_key = ${MY_KEY}, and secret comes from env(APP_SECRET).\n"
    "we use sk-learn for the task-list and a risk-assessment-for-the-q3-plan; AIzawhat is not a key.\n"
)


def leaks(text):
    return [name for name, pat in SECRET_IN_TRANSIT if pat.search(text)]


# ─────────────────────────────────────────────────────────────────────────
head("① true positive — a real secret gets masked")
out, hits = privacy.scrub(DIRTY)
check(not leaks(out), "no secret pattern remains after masking", "left over %s" % leaks(out))
check(len(set(hits)) >= 4, "reports what it masked", ", ".join(sorted(set(hits))))
check("password" in out, "★the key name stays★ — only the value is masked, so search still works")

head("② false positive — never touches what isn't a secret")
out2, hits2 = privacy.scrub(CLEAN)
check(out2 == CLEAN, "a localhost URI · IP:port · placeholder is left as-is",
      "" if out2 == CLEAN else "changed: %s" % hits2)

# ─────────────────────────────────────────────────────────────────────────
head("③ ★the outgoing door — intercept HTTP and look at the actual transmitted body★")
import urllib.request                                                    # noqa: E402

SENT = []


class _FakeResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _spy(req, *a, **kw):
    body = req.data.decode("utf-8", "replace") if req.data else ""
    SENT.append(body)
    # the minimal response shape both doors expect
    return _FakeResp({
        "embeddings": [{"values": [0.0, 0.0]}],
        "candidates": [{"content": {"parts": [{"text": "[{\"i\":0,\"s\":5}]"}]}}],
    })


_real = urllib.request.urlopen
urllib.request.urlopen = _spy
try:
    from brain import rerank, vectors                                    # noqa: E402

    # door ① embeddings — the stub provider never touches HTTP, so gemini is forced
    SENT.clear()
    prov, key = vectors.PROVIDER, os.environ.get("GEMINI_API_KEY")
    try:
        vectors.PROVIDER = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key-not-real"
        vectors.embed([DIRTY])
    except Exception as exc:                                             # noqa: BLE001
        print("   (embedding call exception: %s — only looking at the transmitted body)" % type(exc).__name__)
    finally:
        vectors.PROVIDER = prov
        if key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = key
    check(bool(SENT), "the embedding actually tried to send (interception worked)")
    if SENT:
        bad = leaks(SENT[0])
        check(not bad, "★no secret in the body the embedding sent out★", "leaked %s" % bad)

    # door ② the judge
    # ⛔ ★tie this check to the budget state and, on a day the cap is used up, it goes quietly silent★
    #    (measured 2026-08-26 — actually happened). The judge, when it hits the daily wall (500/day),
    #    sends no HTTP at all, but this check needs to see ★the body it sends★. So it's given not the
    #    real budget but ★an empty temp DB★ — urlopen is intercepted anyway here, so no real call
    #    goes out regardless. This check measures ★masking★, not budget.
    import shutil as _sh
    import tempfile as _tf
    from brain import store as _store
    SENT.clear()
    os.environ["GEMINI_API_KEY"] = "test-key-not-real"
    _home, _tmp = os.environ.get("BRAIN_HOME"), _tf.mkdtemp(prefix="brain-privacy-")
    try:
        os.environ["BRAIN_HOME"] = _tmp
        _db = _store.connect()
        rerank.score(DIRTY, [{"name": "n", "description": DIRTY, "excerpt": DIRTY}],
                     db=_db)
        _db.close()
    except Exception as exc:                                             # noqa: BLE001
        print("   (judge call exception: %s)" % type(exc).__name__)
    finally:
        os.environ.pop("GEMINI_API_KEY", None)
        if _home is None:
            os.environ.pop("BRAIN_HOME", None)
        else:
            os.environ["BRAIN_HOME"] = _home
        _sh.rmtree(_tmp, ignore_errors=True)
    check(bool(SENT), "the judge actually tried to send")
    if SENT:
        bad = leaks(SENT[0])
        check(not bad, "★no secret in the body the judge sent out★ (query · excerpt · description, all of it)",
              "leaked %s" % bad)
finally:
    urllib.request.urlopen = _real

# ─────────────────────────────────────────────────────────────────────────
head("④ does it notice when a new door appears (a static check)")
import glob                                                              # noqa: E402

doors = []
for p in sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "brain", "*.py"))):
    src = open(p, encoding="utf-8").read()
    if "urlopen" in src and os.path.basename(p) not in ("privacy.py",):
        doors.append((os.path.basename(p), "privacy" in src))
print("   modules that call out remotely: %s" % ", ".join(n for n, _ in doors))
# ★A door that only downloads is not an outgoing door★ — but it has to stay that way, so it is named
#  here with its reason and pinned to one shape: a GET of the model archive, nothing sent (2026-11-06).
DOWNLOAD_ONLY = {"embedruntime.py": "fetches the pinned model archive from a fixed URL; sends no memory text",
                 "nativestores.py": "fetches the pinned, checksummed database and Java archives; sends nothing"}
missing = [n for n, ok in doors if not ok and n not in DOWNLOAD_ONLY]
check(not missing, "every module that calls out remotely goes through privacy",
      "★doesn't go through: %s★" % missing if missing else "")
for name in DOWNLOAD_ONLY:
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain", name),
               encoding="utf-8").read()
    calls = re.findall(r"urlopen\(([^,)]*)", src)
    check(calls == ["url"] and "data=" not in src.split("urlopen(")[1].split(")")[0]
          and "Request(" not in src,
          "%s only downloads (one urlopen of the archive URL, no body)" % name, str(calls))

head("⑤ the audit never returns a value")
rep = privacy.audit()
blob = json.dumps(rep, ensure_ascii=False)
check(not leaks(blob), "the audit result has no secret value (only names and pattern names)")
print("   %d memories scanned, %d had a pattern" % (rep["files"], len(rep["flagged"])))

head("⑥ document and source opt-outs reach actual remote build requests")
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
from brain import store, health, engines, stores

with tempfile.TemporaryDirectory() as td:
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
    samples=[('private_note','memory','false','PRIVATE_ORCHID_BODY'),
             ('source_note','excluded','true','EXCLUDED_SOURCE_BODY'),
             ('public_note','memory','true','PUBLIC_CONTROL_BODY')]
    for name,source,flag,body in samples:
        p=Path(td)/(name+'.md');p.write_text('---\nname: '+name+'\nembed: '+flag+'\n---\n'+body+'\n',encoding='utf-8')
        store._index_one(db,store.read_doc(str(p),source,td))
    for name,flag,body in [('long_private','false','LONG_PRIVATE_BODY'),('missing_private','false','MISSING_PRIVATE_BODY'),('changed_private','false','CHANGED_PRIVATE_BODY'),('quoted_private','"false"','QUOTED_PRIVATE_BODY'),('crlf_private','false','CRLF_PRIVATE_BODY')]:
        p=Path(td)/(name+'.md')
        content='---\nname: '+name+'\ndescription: '+('x'*5000)+'\nembed: '+flag+'\n---\n'+body+'\n'
        p.write_bytes((content.replace('\n','\r\n') if name=='crlf_private' else content).encode())
        store._index_one(db,store.read_doc(str(p),'memory',td))
        if name=='missing_private':p.unlink()
        if name=='changed_private':p.write_text(content.replace('embed: false','embed: true'),encoding='utf-8')
    db.commit()
    originals={p:p.read_bytes() for p in Path(td).glob('*.md')}
    config={'sources':[{'name':'memory'},{'name':'excluded','embed':False}]}
    for provider in ('gemini','openai'):
        sent=[]
        def intercept(req,*a,**kw):
            body=json.loads(req.data.decode());sent.append(body)
            n=len(body['requests'] if provider=='gemini' else body['input'])
            return _FakeResp({'embeddings':[{'values':[1.,0.]} for _ in range(n)],
                              'data':[{'index':i,'embedding':[1.,0.]} for i in range(n)]})
        with patch.object(vectors,'PROVIDER',provider),patch.object(vectors,'DIM',2),patch.object(vectors,'api_key',return_value='fixture'),patch.object(vectors,'_wait_for_quota',return_value=0),patch.object(vectors,'_mark_sent'),patch.object(store,'load_config',return_value=config),patch.object(stores,'sync_if_chosen',return_value={}),patch.object(urllib.request,'urlopen',side_effect=intercept),patch.object(engines,'choice',return_value={'provider':provider}):
            vectors.ensure_table(db);db.execute('DELETE FROM vectors');db.commit()
            cov=vectors.coverage(db)
            check(cov['docs']==1 and cov['stale']==1,'remote coverage excludes both opt-outs '+provider,str(cov))
            vectors.build(db,only_ids=[r[0] for r in db.execute('SELECT id FROM docs')])
            wire=json.dumps(sent)
            check(bool(sent) and 'PUBLIC_CONTROL_BODY' in wire and 'PRIVATE_ORCHID_BODY' not in wire and 'EXCLUDED_SOURCE_BODY' not in wire and all(marker not in wire for marker in ('LONG_PRIVATE_BODY','MISSING_PRIVATE_BODY','CHANGED_PRIVATE_BODY','QUOTED_PRIVATE_BODY','CRLF_PRIVATE_BODY')),'actual remote batch contains only eligible control '+provider)
            cov=vectors.coverage(db);state=health.embedding_state(db)
            check(cov['docs']==cov['embedded']==cov['chunks']==1 and cov['stale']==0 and state['eligible_docs']==state['current_docs']==1,'build and coverage share eligibility '+provider,str((cov,state)))
            # Old excluded vectors must not inflate the eligible coverage numerator.
            private=db.execute("SELECT id,sha FROM docs WHERE name='private_note'").fetchone()
            db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?)',(private['id'],0,private['sha'],vectors.model_tag(),2,vectors._unit([1,0]).tobytes()));db.commit()
            check(vectors.coverage(db)['embedded']==1,'old excluded vectors are not counted '+provider)
    with patch.object(vectors,'PROVIDER','embeddinggemma'),patch.object(store,'load_config',return_value=config):
        check({r['name'] for r in vectors.eligible_docs(db)}=={'private_note','public_note','long_private','missing_private','changed_private','quoted_private','crlf_private'},'local-only document remains eligible for local computation')
    check(all(p.read_bytes()==body for p,body in originals.items()) and db.execute('SELECT count(*) FROM docs').fetchone()[0]==8,'source bytes and all lexical documents preserved')
    db.close()

head("⑦ embedding counts use one eligibility snapshot during source transitions")
with tempfile.TemporaryDirectory() as td:
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;store._init(db)
    p=Path(td)/'public.md';p.write_text('---\nname: public\n---\nPUBLIC_CONTROL_BODY\n',encoding='utf-8')
    store._index_one(db,store.read_doc(str(p),'memory',td));vectors.ensure_table(db);db.commit()
    before='\n'.join(db.iterdump());db.execute('PRAGMA query_only=ON')
    for first in (True,False):
        for read in (health.embedding_state,vectors.coverage):
            calls=[]
            def changing_source(path,sha):
                calls.append(path)
                return first if len(calls)==1 else not first
            with patch.object(vectors,'PROVIDER','gemini'),patch.object(vectors,'no_embed_sources',return_value=[]),patch.object(privacy,'exportable_version',side_effect=changing_source),patch.object(engines,'choice',return_value={'provider':'gemini'}):
                out=read(db)
            if read==health.embedding_state:
                ok=out['eligible_docs']==int(first) and out['current_docs']==0 and out['pending_docs']==int(first)
            else:
                ok=out['docs']==int(first) and out['embedded']==0 and out['stale']==int(first)
            check(ok and len(calls)==1,'one source snapshot prevents false completion and negative counts '+str((read.__name__,first)),str(out))
    check(before=='\n'.join(db.iterdump()),'embedding transition diagnostics leave database unchanged')
    db.close()

print("\n" + "=" * 72)
if FAIL:
    print("❌ %d failure(s): %s" % (len(FAIL), " · ".join(FAIL)))
    sys.exit(1)
print("all passed ✅")
