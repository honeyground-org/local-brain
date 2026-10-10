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
missing = [n for n, ok in doors if not ok]
check(not missing, "every module that calls out remotely goes through privacy",
      "★doesn't go through: %s★" % missing if missing else "")

head("⑤ the audit never returns a value")
rep = privacy.audit()
blob = json.dumps(rep, ensure_ascii=False)
check(not leaks(blob), "the audit result has no secret value (only names and pattern names)")
print("   %d memories scanned, %d had a pattern" % (rep["files"], len(rep["flagged"])))

print("\n" + "=" * 72)
if FAIL:
    print("❌ %d failure(s): %s" % (len(FAIL), " · ".join(FAIL)))
    sys.exit(1)
print("all passed ✅")
