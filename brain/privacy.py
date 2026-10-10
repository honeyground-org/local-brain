"""The outgoing door — ★masks a secret before it ever leaves remotely★ (2026-08-26).

## Why this layer exists — measurement spoke first

This brain sends documents out to two places: **embeddings** (`vectors.embed`) and **the judge**
(`rerank.score`). Both are remote APIs. Counted on 2026-08-26: of 619 memories, **21** had a
credential pattern (mongodb URI · `password=` · `secret`/`token`), and a 420-character excerpt from
one of them (a reference note holding database credentials) ★actually went out to the judge★.

The only defence was `no_embed`, and that is a **source-level** switch — the only choices were "turn
off the whole memory or send it all," with no way to exclude just one memory. So nobody turned it off
and everything went out. That same day, the axis Anthropic added to memory (sensitive topics excluded
by default + an absolute deny list) turned out to be exactly the axis we didn't have.

## Three layers — what belongs to which

| layer | what | nature |
|---|---|---|
| ⛔ absolute | masks **only the value** of a credential pattern (`scrub`) | independent of config. Cannot be turned off |
| optional | document frontmatter `embed: false` (`is_local_only`) | a human excludes one document by declaring it |
| optional | config source `"embed": false` (§vectors.no_embed_sources) | excludes the whole corpus |

★the key name stays, only the value is masked★ — `password: ‹masked›` still gets found by searching
for it. Search quality survives while only the secret is dropped. Nobody ever searches by the value itself.

## ⛔ What this ★does not★ mask — written down honestly

- **an internal IP·host:port** (`198.51.100.7:9200`) — not a credential, and it's indistinguishable
  from a version number or a timestamp, so false positives run high. Declare `embed: false` on that document if you want it excluded.
- **a URI with no credential** (`mongodb://localhost:27117`) — no `@`, no secret.
  Measured: several of the 21 were exactly this (a test Mongo address).
- ⛔ **What already went out can't be pulled back.** This layer only blocks ★going forward★. Deleting
  from the vector DB is only our own cleanup — the fact that it was sent to the provider stays a fact.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

MASK_FMT = "‹masked:%s›"

# ★placeholders★, not real values — masking them gains nothing and only wrecks search
_PLACEHOLDER = re.compile(
    r"^(?:str|string|bool|true|false|null|none|nil|int|number|\.\.\.|x{3,}|\*{3,}"
    r"|<[^>]*>|\$\{[^}]*\}|\$[A-Z_]+|your[_-].*|changeme|todo|xxx|dummy|example"
    r"|env\(.*\)|process\.env\..*|os\.environ.*)$",
    re.I)

# ⛔ order matters — mask the long ones (URI·PEM) first, or a fragment inside survives
PATTERNS: List[Tuple[str, "re.Pattern"]] = [
    # a PEM block — the whole multi-line thing
    ("private-key",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.S)),
    # ★only a URI that carries credentials★ — needs an `@` to be a user:secret shape
    ("db-uri",
     re.compile(r"\b(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis|amqp|ftp|ssh)"
                r"://[^\s`\"'<>]*@[^\s`\"'<>]+")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("gh-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    # ★the AI providers' own keys★ (2026-10-10) — the engines brain itself talks to; a note holding one
    # went out whole, because only `api_key = …` was caught. `sk-…`: OpenAI (sk-proj-, sk-svcacct-) and
    # Anthropic (sk-ant-api03-); a digit is required so a long hyphenated word is not mistaken for one.
    ("ai-key", re.compile(r"\bsk-(?:ant-[a-z]+\d{2}-|proj-|svcacct-|admin-)?(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])")),
    ("gh-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("hf-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    # last — key=value. Masks ★only the value★ (the key name stays for search)
    ("assignment",
     re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|"
                r"secret[_-]?key|auth[_-]?token|token)\b(\s*[:=]\s*)"
                r"(['\"]?)([^\s'\"`,;)]{8,})\3")),
]


def _mask_assignment(m: "re.Match") -> str:
    value = m.group(4)
    if _PLACEHOLDER.match(value):
        return m.group(0)                       # a placeholder is left as-is
    return "%s%s%s" % (m.group(1), m.group(2), MASK_FMT % "secret")


def scrub(text: str) -> Tuple[str, List[str]]:
    """Text with a secret masked and ★what got masked★.

    ⛔ Never masks silently — the caller must be able to count and report it. Not knowing what was
       masked means neither "it went out" nor "it didn't" can be proven.
    """
    if not text:
        return text, []
    hits: List[str] = []
    out = text
    for label, pat in PATTERNS:
        if label == "assignment":
            new, n = pat.subn(_mask_assignment, out)
        else:
            new, n = pat.subn(MASK_FMT % label, out)
        if new != out:
            hits.extend([label] * n)
            out = new
    return out, hits


def scrub_all(texts: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Multiple texts at once — embeddings go out in a batch."""
    outs, hits = [], []
    for t in texts:
        o, h = scrub(t or "")
        outs.append(o)
        hits.extend(h)
    return outs, hits


# ── document-level opt-out ───────────────────────────────────────────────────
_FM_EMBED = re.compile(r"^embed:\s*(false|no|off|0)\s*$", re.I | re.M)


def is_local_only(path_or_text: str, is_text: bool = False) -> bool:
    """Did this document declare in frontmatter ★never export me★ (`embed: false`).

    A finer-grained layer than the source-level switch (§vectors.no_embed_sources) — the corpus stays
    on and only one document is excluded. Not having this is why nobody turned anything off at measurement time and everything went out.
    """
    if is_text:
        head = (path_or_text or "")[:4000]
    else:
        try:
            with open(path_or_text, encoding="utf-8", errors="replace") as fh:
                head = fh.read(4000)
        except OSError:
            return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    front = head[:end if end > 0 else len(head)]
    return bool(_FM_EMBED.search(front))


def audit(mem_dir: str = "") -> Dict[str, object]:
    """Counts what remains in the corpus — ★never returns a value★.

    `brain privacy` calls this. It's the list a human uses to decide "what should be excluded,"
    so it carries only file names and ★pattern names★.
    """
    import glob
    from . import store
    base = mem_dir or store.memory_dir()
    rows, n = [], 0
    if not base:
        return {"base": "", "files": 0, "flagged": []}
    for p in sorted(glob.glob(os.path.join(base, "*.md"))):
        n += 1
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        _, hits = scrub(txt)
        if hits:
            rows.append({"name": os.path.basename(p)[:-3],
                         "patterns": sorted(set(hits)),
                         "local_only": is_local_only(txt, is_text=True)})
    return {"base": base, "files": n, "flagged": rows}
