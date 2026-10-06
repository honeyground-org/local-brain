#!/usr/bin/env python3
"""★Any engine can fill any role it supports★ — and only because someone chose it. (local · budget 0)

## What this holds down (2026-10-06)

brain sends notes to an outside model for two jobs — embed (meaning-based search) and judge (does
this note answer that question). The judge used to have one provider's URL and key written into the
code. Now each role names its engine (§engines). The ways that can quietly go wrong:

  ① a key that merely exists gets used — `ANTHROPIC_API_KEY` is usually the coding agent's own
  ② an adapter speaks the wrong dialect (path · auth header · body) and fails only on a real call
  ③ a threshold measured on one judge keeps being applied to another judge's scores
  ④ one judge's cached score is served as another's
  ⑤ a screen prints a key

Every adapter is driven against ★a local fake server★ that records what it was sent and answers in
that provider's own response shape — no network, no key, no cost. Each child process gets its own
temporary home and config, so the real ones are never touched.

How to run:  PYTHONPATH=. python3 tests/verify_engines.py
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(label)


# ── the fake provider ───────────────────────────────────────────────────────
SEEN = []
SCORES = '[{"i": 0, "s": 9}, {"i": 1, "s": 1}]'


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):                           # quiet
        pass

    def do_POST(self):                                   # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        SEEN.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                     "body": body})
        p = self.path
        if p.startswith("/limited/"):
            out = json.dumps({"error": {"message": "slow down", "type": "rate_limit_error"}})
            self.send_response(429)
            self.send_header("retry-after", "7")
            self.end_headers()
            self.wfile.write(out.encode())
            return
        if p.endswith(":generateContent"):
            res = {"candidates": [{"content": {"parts": [{"text": SCORES}]}}]}
        elif p.endswith(":batchEmbedContents"):
            res = {"embeddings": [{"values": [0.1] * 8} for _ in body.get("requests", [])]}
        elif p.endswith("/chat/completions"):
            res = {"choices": [{"message": {"content": SCORES}}]}
        elif p.endswith("/embeddings"):
            res = {"data": [{"index": i, "embedding": [0.2] * 8}
                            for i, _ in enumerate(body.get("input", []))]}
        elif p.startswith("/refuse/"):
            res = {"content": [], "stop_reason": "refusal", "stop_details": {"category": "cyber"}}
        elif p.endswith("/messages"):
            res = {"content": [{"type": "thinking", "thinking": ""},
                               {"type": "text", "text": SCORES}], "stop_reason": "end_turn"}
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(res).encode())


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]
TMP = tempfile.mkdtemp(prefix="brain-engines-")
# ⛔ Joined at run time, so the file that ships holds no key-shaped string — the release scan
#    (`verify_public_scrub`) treats `sk-…` as a secret, and so would anyone's scanner after a clone.
FAKE_KEY = "sk" + "-fake-SECRET-0123456789"


def child(code: str, env_extra: dict, config_engines=None) -> dict:
    """Run `code` in a fresh process with its own home and config; it prints one JSON line."""
    home = tempfile.mkdtemp(dir=TMP)
    cfg = os.path.join(home, "config.json")
    with open(cfg, "w") as fh:
        json.dump({"sources": [], **({"engines": config_engines} if config_engines else {})}, fh)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BRAIN_", "GEMINI_", "OPENAI_", "ANTHROPIC_"))}
    env.update({"HOME": home, "BRAIN_HOME": home, "BRAIN_CONFIG": cfg, "PYTHONPATH": ROOT,
                "BRAIN_LANG": "en"})
    env.update(env_extra)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                       cwd=ROOT, timeout=60)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"_err": (p.stderr or p.stdout)[-300:]}


JUDGE = """
import json
from brain import rerank
c = [{"name": "a", "description": "the answer", "body": "x"},
     {"name": "b", "description": "unrelated", "body": "y"}]
s = rerank.score("what was it?", c, cache=%s)
print(json.dumps({"scores": s, "fail": rerank.last_failure()}))
"""

print("=" * 72 + "\n① presence is not a choice\n" + "=" * 72)
r = child("""
import json
from brain import engines, rerank, vectors
print(json.dumps({"rows": [(x["role"], x["provider"], x["source"]) for x in engines.describe()],
                  "judge": rerank.available(), "embed": vectors.PROVIDER}))
""", {"ANTHROPIC_API_KEY": FAKE_KEY, "OPENAI_API_KEY": FAKE_KEY, "GEMINI_API_KEY": FAKE_KEY})
check("★three keys set, nothing chosen → both roles are none★",
      r.get("rows") == [["embed", "none", "unset"], ["judge", "none", "unset"]], r)
check("…and the judge is not available, the embedder is off",
      r.get("judge") is False and r.get("embed") == "none", r)
r = child(JUDGE % "False", {"ANTHROPIC_API_KEY": FAKE_KEY})
check("…and scoring sends nothing anywhere", r.get("scores") is None and not SEEN, r)

print("\n" + "=" * 72 + "\n② each judge adapter speaks its provider's dialect\n" + "=" * 72)
cases = [
    ("gemini", {"BRAIN_JUDGE_PROVIDER": "gemini", "BRAIN_JUDGE_BASE_URL": BASE + "/gemini",
                "GEMINI_API_KEY": FAKE_KEY},
     lambda q: q["path"] == "/gemini/models/gemini-flash-lite-latest:generateContent"
     and q["headers"].get("x-goog-api-key") == FAKE_KEY
     and "contents" in q["body"]),
    ("openai", {"BRAIN_JUDGE_PROVIDER": "openai", "BRAIN_JUDGE_BASE_URL": BASE + "/openai",
                "OPENAI_API_KEY": FAKE_KEY},
     lambda q: q["path"] == "/openai/chat/completions"
     and q["headers"].get("authorization") == "Bearer " + FAKE_KEY
     and q["body"].get("model") == "gpt-4.1-mini" and q["body"]["messages"][0]["role"] == "user"),
    ("anthropic", {"BRAIN_JUDGE_PROVIDER": "anthropic", "BRAIN_JUDGE_BASE_URL": BASE + "/anthropic",
                   "ANTHROPIC_API_KEY": FAKE_KEY},
     lambda q: q["path"] == "/anthropic/messages"
     and q["headers"].get("x-api-key") == FAKE_KEY
     and q["headers"].get("anthropic-version") == "2023-06-01"
     and q["body"].get("model") == "claude-opus-5-5" and q["body"].get("max_tokens")
     and "temperature" not in q["body"]),
]
for name, env, shape in cases:
    SEEN.clear()
    r = child(JUDGE % "False", env)
    check("%-9s → scores read back from its own response shape" % name,
          r.get("scores") == [9.0, 1.0], r)
    check("%-9s → path · auth header · body are that provider's" % name,
          len(SEEN) == 1 and shape(SEEN[0]), SEEN[0]["path"] if SEEN else "(nothing sent)")

SEEN.clear()
r = child(JUDGE % "False", {"BRAIN_JUDGE_PROVIDER": "openai",
                            "BRAIN_JUDGE_BASE_URL": BASE + "/openai", "BRAIN_JUDGE_MODEL": "llama3.1"})
check("★a local OpenAI-compatible server needs no key★ — and is sent none",
      r.get("scores") == [9.0, 1.0] and SEEN and "authorization" not in SEEN[0]["headers"]
      and SEEN[0]["body"]["model"] == "llama3.1", r)

print("\n" + "=" * 72 + "\n③ a failure says why — and is never a row of zeros\n" + "=" * 72)
SEEN.clear()
r = child(JUDGE % "False", {"BRAIN_JUDGE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": FAKE_KEY,
                            "BRAIN_JUDGE_BASE_URL": BASE + "/refuse"})
check("a refusal → None, and the reason names it", r.get("scores") is None
      and "refusal" in (r.get("fail") or ""), r)
r = child(JUDGE % "False", {"BRAIN_JUDGE_PROVIDER": "openai", "OPENAI_API_KEY": FAKE_KEY,
                            "BRAIN_JUDGE_BASE_URL": BASE + "/limited"})
check("a 429 → None, and the reason carries the status", r.get("scores") is None
      and "429" in (r.get("fail") or ""), r)
SEEN.clear()
r = child(JUDGE % "False", {"BRAIN_JUDGE_PROVIDER": "anthropic"})
check("the provider's own host with no key → None, nothing sent", r.get("scores") is None
      and "key" in (r.get("fail") or "") and not SEEN, r)

print("\n" + "=" * 72 + "\n④ the embedder too\n" + "=" * 72)
EMB = """
import json
from brain import vectors
v = vectors.embed(["one note", "another"], dim=8)
print(json.dumps({"n": len(v), "dim": len(v[0]) if v else 0}))
"""
for name, env, path in (
        ("gemini", {"BRAIN_EMBED_PROVIDER": "gemini", "BRAIN_EMBED_BASE_URL": BASE + "/gemini",
                    "GEMINI_API_KEY": FAKE_KEY}, "/gemini/models/gemini-embedding-2:batchEmbedContents"),
        ("openai", {"BRAIN_EMBED_PROVIDER": "openai", "BRAIN_EMBED_BASE_URL": BASE + "/openai"},
         "/openai/embeddings")):
    SEEN.clear()
    r = child(EMB, env)
    check("%-6s embeddings: two vectors back, sent to %s" % (name, path),
          r.get("n") == 2 and SEEN and SEEN[0]["path"] == path, r)
r = child(EMB, {})
check("no embedder chosen → it refuses (never a silent empty vector)", "_err" in r
      and "NoKey" in r["_err"], (r.get("_err") or "")[-80:])

print("\n" + "=" * 72 + "\n⑤ a threshold holds only on the judge it was measured on\n" + "=" * 72)
STAMP = """
import json
from brain import rerank, store
db = store.connect()
store.set_meta(db, "rerank_min_score", "7.0")
before = rerank.min_score(db)
store.set_meta(db, "rerank_calibrated_for", rerank.scale_id())
print(json.dumps({"unstamped": before, "stamped": rerank.min_score(db), "scale": rerank.scale_id()}))
"""
a = child(STAMP, {"BRAIN_JUDGE_PROVIDER": "gemini", "GEMINI_API_KEY": FAKE_KEY})
check("an unstamped threshold is not trusted (written before stamps — whose scale?)",
      a.get("unstamped") == 99.0, a)
check("(control) stamped for this judge, it is used", a.get("stamped") == 7.0, a)
b = child(STAMP, {"BRAIN_JUDGE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": FAKE_KEY})
c = child(STAMP, {"BRAIN_JUDGE_PROVIDER": "gemini", "GEMINI_API_KEY": FAKE_KEY,
                  "BRAIN_JUDGE_MODEL": "another-model"})
check("another provider is another scale", a.get("scale") and a.get("scale") != b.get("scale"), b)
check("another model on the same provider is another scale", a.get("scale") != c.get("scale"), c)
CROSS = """
import json
from brain import rerank, store, engines
db = store.connect()
store.set_meta(db, "rerank_min_score", "7.0")
store.set_meta(db, "rerank_calibrated_for", engines.fingerprint("gemini/gemini-flash-lite-latest", rerank._PROMPT))
print(json.dumps({"min": rerank.min_score(db)}))
"""
d = child(CROSS, {"BRAIN_JUDGE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": FAKE_KEY})
check("★a gemini-stamped threshold is not applied to an anthropic judge★", d.get("min") == 99.0, d)

print("\n" + "=" * 72 + "\n⑥ one judge's cached score is never served as another's\n" + "=" * 72)
CACHE = """
import json, os
from brain import rerank
c = [{"name": "a", "description": "the answer", "body": "x"},
     {"name": "b", "description": "unrelated", "body": "y"}]
print(json.dumps({"s": rerank.score("what was it?", c, cache=True)}))
"""
home = tempfile.mkdtemp(dir=TMP)


def shared(env):
    cfg = os.path.join(home, "config.json")
    json.dump({"sources": []}, open(cfg, "w"))
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("BRAIN_", "GEMINI_", "OPENAI_", "ANTHROPIC_"))}
    e.update({"HOME": home, "BRAIN_HOME": home, "BRAIN_CONFIG": cfg, "PYTHONPATH": ROOT}, **env)
    p = subprocess.run([sys.executable, "-c", CACHE], capture_output=True, text=True, env=e,
                       cwd=ROOT, timeout=60)
    return p.stdout.strip()


SEEN.clear()
shared({"BRAIN_JUDGE_PROVIDER": "openai", "BRAIN_JUDGE_BASE_URL": BASE + "/openai"})
shared({"BRAIN_JUDGE_PROVIDER": "openai", "BRAIN_JUDGE_BASE_URL": BASE + "/openai"})
n_same = len(SEEN)
shared({"BRAIN_JUDGE_PROVIDER": "anthropic", "BRAIN_JUDGE_BASE_URL": BASE + "/anthropic",
        "ANTHROPIC_API_KEY": FAKE_KEY})
check("(control) the same judge asked twice → one call, the second is cached", n_same == 1, n_same)
check("★another judge, same question → it is asked, not served the first one's score★",
      len(SEEN) == 2 and SEEN[-1]["path"] == "/anthropic/messages", [q["path"] for q in SEEN])

print("\n" + "=" * 72 + "\n⑦ the screen and the setter\n" + "=" * 72)
SCREEN = """
import json
from brain import cli, store
print(json.dumps({"t": cli.engines_text(store.connect())}))
"""
r = child(SCREEN, {"OPENAI_API_KEY": FAKE_KEY, "ANTHROPIC_API_KEY": FAKE_KEY},
          {"judge": {"provider": "anthropic"}})
t = r.get("t") or ""
check("★no key value is ever printed★", t and FAKE_KEY not in t and "SECRET" not in t, t[:60])
check("the chosen judge, its default model and where the choice came from are shown",
      "anthropic · claude-opus-5-5" in t and "chosen in config.json" in t, "")
check("what each role sends, and to which host", "api.anthropic.com" in t and "sends" in t, "")
check("a key that is present but not chosen is named as ★not used★",
      "never used unless chosen): openai" in t, "")
check("a judge with no threshold for it says it adds nothing yet", "not calibrated" in t, "")
r = child(SCREEN, {}, {"embed": {"provider": "openai", "base_url": "http://localhost:11434/v1",
                                 "model": "nomic-embed-text"}})
check("a local server is shown as ★nothing leaves★",
      "nothing leaves" in (r.get("t") or "") and "localhost:11434" in (r.get("t") or ""),
      "")
SET = """
import json
from brain import engines, store
out = {}
try:
    engines.set_choice("embed", "anthropic")
except ValueError as e:
    out["refused"] = str(e)[:60]
engines.set_choice("judge", "openai", model="gpt-4.1-mini")
out["cfg"] = store.load_config()
print(json.dumps(out))
"""
r = child(SET, {})
check("anthropic cannot be chosen as the embedder (it has no embedding API)", "refused" in r, r)
check("setting writes only the engines block", r.get("cfg") == {
    "sources": [], "engines": {"judge": {"provider": "openai", "model": "gpt-4.1-mini"}}}, r)

srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print("=" * 72)
print("❌ %d failure(s): %s" % (len(FAIL), "; ".join(FAIL)) if FAIL else "✅ all passed")
sys.exit(1 if FAIL else 0)
