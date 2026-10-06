"""i18n — pulls screen text from a per-language catalog. ★English is the default★.

## Why this shape

One of this repository's points of pride is ★0 dependencies (standard library only)★. So instead of
building `gettext`'s `.po/.mo` compile step, it uses ★a JSON catalog★. A translator needs only a text
editor, and the settings UI reads the same JSON directly.

## The order that decides the language (first wins)

    1. config.json's "language"      ← the settings UI writes here
    2. the BRAIN_LANG env var         ← to see a different one just once
    3. ★en★                           ← the default

⛔ The OS locale (LANG/LC_ALL) is ★not★ a slot (removed 2026-10-06, for the public release). It used to
   be slot 3, so a machine set to Korean opened every screen in Korean even though nobody chose that —
   and English, the canonical catalog, was unreachable without knowing BRAIN_LANG existed. Another
   language is now always a choice: `brain install --lang ko` writes it, BRAIN_LANG shows it once.

## ⛔ Three things this file guarantees

- **It never dies on screen.** Missing a key or a broken translation, it returns ★English, and absent
  even that, the key itself★. A recall tool must never throw because of a translation.
- **A placeholder mismatch is never passed to runtime.** Missing `{count}` in a translation,
  `str.format` throws — this catches it and falls back to English, and `brain i18n --check` catches it earlier.
- ⛔ **A missing translation is never quiet.** This applies to translation the "probe that cannot fail"
  this repository has been burnt by repeatedly — `--check` counts ★missing keys · unused keys ·
  placeholder mismatches★. Uncounted, a translation is guaranteed to rot (the catalog never follows the code).
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional

# ★Supported languages★ — English is canonical, the rest may be partial (a missing key falls back to en).
LANGS = ("en", "de", "es", "fr", "ja", "ko")
DEFAULT = "en"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⛔ The catalog lives ★inside the package★ (2026-09-02). Put it in the repo root's `i18n/` instead and
#    pip unpacks it at ★the top of site-packages★ — that namespace is shared by everyone, so it collides
#    with another package's `i18n` or gets overwritten by someone else's.
#    ★Breaking someone else's environment is the worst failure★ (the same rule behind why the installer
#    merges settings.json instead of overwriting it).
CATALOG_DIR = os.environ.get("BRAIN_I18N_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "locales")

_cache: Dict[str, Dict[str, str]] = {}
_lang: Optional[str] = None
_missing: set = set()          # keys not found this process — for diagnosis, not for `--check`


def _normalize(raw: str) -> str:
    """`ko_KR.UTF-8` · `de-DE` · `JA` → `ko` · `de` · `ja`. An unknown value is the empty string."""
    if not raw:
        return ""
    code = re.split(r"[._-]", raw.strip())[0].lower()
    return code if code in LANGS else ""


def _from_config() -> str:
    """config.json's language. ⛔ store is not called here — that would create a cycle."""
    # ⛔ An installed copy has no repository — the path rule's canonical source is `store.default_config_path`.
    #    (Importing store here would cycle, so on failure it quietly steps back.)
    try:
        from brain import store as _s
        path = _s.default_config_path()
    except Exception:                                    # noqa: BLE001
        path = os.environ.get("BRAIN_CONFIG") or os.path.join(_ROOT, "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return _normalize(str(json.load(fh).get("language") or ""))
    except (OSError, ValueError, AttributeError):
        return ""


def lang(refresh: bool = False) -> str:
    """The current language. ★Decided once per process★ (a file is not read on every sentence)."""
    global _lang
    if _lang is not None and not refresh:
        return _lang
    # ⛔ An unknown value (`kl_KL`, a typo) ★skips that slot★ — stopping there would let one typo turn
    #    the whole screen English with no way to tell why. The next slot answers instead.
    # ⛔ No LANG/LC_ALL here — see the module docstring: a language other than English is chosen, never inferred.
    _lang = (_from_config()
             or _normalize(os.environ.get("BRAIN_LANG", ""))
             or DEFAULT)
    return _lang


def catalog(code: str) -> Dict[str, str]:
    """One language's catalog. ★Empty★ if the file is absent (not an error — it falls back to en)."""
    if code in _cache:
        return _cache[code]
    for ck in [k for k in _ph_cache if k[0] == code]:
        _ph_cache.pop(ck, None)          # re-reading the catalog re-judges too
    path = os.path.join(CATALOG_DIR, "%s.json" % code)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        out = {k: v for k, v in data.items() if isinstance(v, str) and not k.startswith("_")}
    except (OSError, ValueError):
        out = {}
    _cache[code] = out
    return out


_ph_cache: Dict[tuple, bool] = {}


def _placeholders_ok(code: str, key: str, raw: str) -> bool:
    """Does this translation's placeholders ★match English★.

    ⛔ Why an exception from `str.format` cannot be relied on (a check caught this on 2026-09-02):
    `"({a})".format(a=1, c=2)` ★succeeds★ — an extra argument is simply ignored. So if a translator
    ★drops★ `{c}`, no exception is thrown and ★a sentence missing information reaches the screen quietly.★
    An exception is thrown only ★when a name that does not exist is used★. Blocking both cases needs ★a comparison★.
    """
    if code == DEFAULT:
        return True
    ck = (code, key)
    if ck in _ph_cache:
        return _ph_cache[ck]
    en_raw = catalog(DEFAULT).get(key)
    ok = (en_raw is None
          or set(_PLACEHOLDER.findall(raw)) == set(_PLACEHOLDER.findall(en_raw)))
    _ph_cache[ck] = ok
    return ok


def t(key: str, **kw) -> str:
    """A translated line. ⛔ ★Never throws an exception.★

    Fallback order: the current language → English → ★the key itself★.
    If a placeholder differs from English, that translation ★is not used★ and it falls one step further —
    so that a translator dropping `{count}` never lets ★a sentence missing information★ out.
    """
    for code in (lang(), DEFAULT):
        raw = catalog(code).get(key)
        if raw is None:
            continue
        if not _placeholders_ok(code, key, raw):
            continue                     # ★a broken translation is never used★ (§_placeholders_ok)
        if not kw:
            return raw
        try:
            return raw.format(**kw)
        except (KeyError, IndexError, ValueError):
            continue
    _missing.add(key)
    return key


def missing_seen() -> list:
    """Keys not found this process — for diagnosis (the check uses `audit()`)."""
    return sorted(_missing)


# ── ⛔ the check that keeps a translation from rotting ────────────────────────────
# ⛔ ★The rule for finding a key was wrong, twice★ (2026-09-02, the check exposed its own defect)
#   ① something with only dots, like `t("...")`, was counted as a key → ★it must hold at least one letter★.
#   ② the ★second★ half of `t("a.b" if x else "c.d")` was missed — only right after the opening parenthesis was looked at.
#      So a key actually in use was reported as "unused" (believing that report and deleting it would have broken the screen).
#   ⇒ scans from `t(` until that call closes and counts ★every key-shaped string inside it★.
# ⛔ ★There is exactly one way to call it — `t(` or `i18n.t(`.★ An alias
#    (`from .i18n import t as _tr`) is invisible to this scanner, and a key actually in use gets
#    reported as "unused". That happened exactly on 2026-09-02 (one key called through `_i18n_t(` went undetected). ★The check caught it.★
_CALL = re.compile(r"\bt\(")
_STR = re.compile(r"""["']([^"'\n]{2,120})["']""")
_KEYISH = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)+$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _code_only(text: str, path: str) -> str:
    """Code with comments and docstrings stripped. ⛔ ★So an example inside a description is never counted as a key.★

    On 2026-09-02 this scanner reported ★its own doc's example★ (`t("a.b" if x else "c.d")`) as a real
    key. A counting tool that reads a description as code calls an absent key "missing".
    """
    if not path.endswith(".py"):
        return text
    import io
    import tokenize as _tk
    # ★Only a string standing alone at the start of a statement★ is a docstring.
    #
    # ⛔ Wrong twice (2026-09-02, both exposed by this very check):
    #   ① `NL` (a line break inside parentheses) was also treated as "a statement starting" →
    #      ★every string inside a multi-line list got erased as a docstring★. Inside parentheses
    #      Python emits `NL`, not `NEWLINE` (the logical line end) — ★the two are different.★
    #   ② column position was not kept, so tokens ran together (`from__future__importannotations`).
    #      Mostly harmless for key extraction, but a run-together spot can create a call that never existed.
    _OPENS = (_tk.NEWLINE, _tk.INDENT, _tk.DEDENT, _tk.ENCODING)
    try:
        out, row, col, at_stmt_start = [], 1, 0, True
        for tok in _tk.generate_tokens(io.StringIO(text).readline):
            drop = (tok.type == _tk.COMMENT
                    or (tok.type == _tk.STRING and at_stmt_start))
            if tok.type in _OPENS:
                at_stmt_start = True
            elif tok.type not in (_tk.COMMENT, _tk.NL):
                at_stmt_start = False
            if drop:
                continue
            sr, sc = tok.start
            if sr > row:                       # crossing a line, catch up with newlines
                out.append("\n" * (sr - row))
                col = 0
            if sc > col:
                out.append(" " * (sc - col))   # ★keeps the column★ — so tokens do not run together
            out.append(tok.string)
            row, col = tok.end
        return "".join(out)
    except (_tk.TokenError, IndentationError, SyntaxError):
        return text                          # unreadable → return the original (counting never dies)


def _keys_in(text: str):
    """Every ★key-shaped★ string inside a `t(` call. Counts parentheses to find where the call ends."""
    # ⛔ ★Splitting at a newline misses a multi-line call whole★ (measured 2026-09-02: 4 keys actually
    #    in use were reported as "unused" — believing that and deleting them would break the screen).
    #    Only parentheses are counted to find the call's end, with a capped window in case a file is broken.
    for m in _CALL.finditer(text):
        i, depth, stop = m.end(), 1, min(len(text), m.end() + 2000)
        while i < stop and depth:
            c = text[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        for sm in _STR.finditer(text[m.end():i]):
            if _KEYISH.match(sm.group(1)):
                yield sm.group(1)


def used_keys(root: str = "") -> Dict[str, str]:
    """The `t("...")` keys actually called from the source → the first file each appeared in."""
    root = root or _ROOT
    found: Dict[str, str] = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "__pycache__", "node_modules", "i18n", "tests")]
        for fn in files:
            if not fn.endswith((".py", ".html", ".js")):
                continue
            path = os.path.join(base, fn)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for key in _keys_in(_code_only(text, path)):
                found.setdefault(key, os.path.relpath(path, root))
    return found


# `UI_PREFIX` (a page loads a whole prefix) and `I18N_PREFIX` (code builds keys at runtime — `calib.noise.%d`)
# are the same declaration to the scanner: "I use every key of this prefix".
_PREFIX_DECL = re.compile(r"(?:UI|I18N)_PREFIX\s*=\s*\(([^)]*)\)", re.S)
_ATTR = re.compile(r'data-t="([A-Za-z0-9_.\-]+)"')


def claimed_prefixes(root: str = "") -> list:
    """★What the code declares — "I use every one of this prefix."★

    ⛔ Why this is needed (2026-09-02): the dashboard never calls a key through `t("...")` — it ★loads
    six languages into the page and lets the browser switch★, so a key goes out as a `data-t="..."`
    attribute and the dictionary is loaded whole, by prefix. So to a scanner that only looks for `t(`,
    those keys looked ★unused★, and `verify_i18n` actually reported them that way.
    ★Believing that and deleting them would have erased the screen's text whole.★

    ⇒ Reads the `UI_PREFIX = (...)` declaration and counts that prefix's keys as ★used★.
      `I18N_PREFIX = (...)` is the same declaration for code that builds a key at runtime
      (`calibrate.neutral_probes` — `"calib.noise.%d" % i` — is invisible to a scanner that reads `t("...")`).
    """
    root = root or _ROOT
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "__pycache__", "node_modules", "locales", "tests")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            try:
                with open(os.path.join(base, fn), encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for m in _PREFIX_DECL.finditer(_code_only(text, fn)):
                out += [x for x in re.findall(r'"([A-Za-z0-9_.\-]+)"', m.group(1))]
    return sorted(set(out))


def audit(root: str = "") -> dict:
    """Counts ★missing keys · unused keys · placeholder mismatches★.

    ⛔ Uncounted, a translation is guaranteed to rot — the catalog never follows the code as it changes.
    A placeholder mismatch is especially dangerous: `t()` falls back to English quietly, producing
    ★a translation that was added but never shows★.
    """
    used = used_keys(root)
    # ⛔ Something declared by prefix counts as ★used★ too (§claimed_prefixes) — otherwise every key
    #    of the dashboard is reported "unused", and believing that empties the screen.
    prefixes = tuple(claimed_prefixes(root))
    en = catalog(DEFAULT)
    if prefixes:
        for k in en:
            if k.startswith(prefixes):
                used.setdefault(k, "(prefix)")
    out = {"used": len(used), "prefixes": list(prefixes), "langs": {},
           "en_missing": sorted(k for k in used if k not in en
                                and not k.startswith(prefixes)),
           "en_unused": sorted(k for k in en if k not in used)}
    for code in LANGS:
        if code == DEFAULT:
            continue
        cat = catalog(code)
        bad_ph = []
        for k, v in cat.items():
            if k not in en:
                continue
            if set(_PLACEHOLDER.findall(v)) != set(_PLACEHOLDER.findall(en[k])):
                bad_ph.append(k)
        out["langs"][code] = {
            "have": len(cat),
            "missing": sorted(k for k in en if k not in cat),
            "extra": sorted(k for k in cat if k not in en),
            "placeholder_mismatch": sorted(bad_ph),
        }
    return out
