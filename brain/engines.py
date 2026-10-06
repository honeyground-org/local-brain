"""AI engines — ★which outside model plays which role★, chosen by the person and shown plainly.

## Why this exists (2026-10-06)

brain calls an outside model for exactly two jobs, and until now both were wired to one company:

  embed  — turns notes and questions into vectors, so a question can find a note written in other
           words (meaning-based search). Sends: note chunks and each searched question.
  judge  — reads a question and up to 20 candidate notes and scores 0~10 whether each one actually
           answers it. Used by two-stage recall, `brain score --full`, and behaviour-rule discovery.
           Sends: the question and short excerpts of the candidates.

The embedder could already be switched; the judge had one provider's URL and key written into the
code. A tool meant for anyone cannot decide whose model reads their notes. So each role names its
engine here, any engine can fill it, and `brain engines` says which one does and what it sends.

## ⛔ An engine is only ever used because someone chose it

Order: `BRAIN_<ROLE>_PROVIDER` → `config.json` `"engines"` → ★none★. There is no built-in favourite.
A key lying around is not a choice — `ANTHROPIC_API_KEY` is usually the coding agent's own key, and
`OPENAI_API_KEY` / `GEMINI_API_KEY` may belong to another tool; sending notes through them because
they happen to be set is the one path where content leaves a machine whose owner never decided it
should. The installer records a choice out loud (§install), and `brain engines --set` changes it.

## ★An OpenAI-compatible server is any server★

`openai` with a `base_url` reaches anything that speaks that API — a local Ollama or LM Studio
(`http://localhost:11434/v1`), vLLM, or a hosted router. With a local server nothing leaves the
machine and no key is needed; the key is required only for the provider's own host.

⛔ Standard library only, like the rest of brain — the official SDKs would add dependencies and
   drop the Python 3.9 this runs on. Each adapter speaks the provider's own documented HTTP API.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

ROLES = ("embed", "judge")
_ROLE_ENV = {"embed": "EMBED", "judge": "JUDGE"}

# provider → what it can do, its own host, its key, and a default model per role.
# ⛔ A default model is a convenience, not a recommendation — every one is overridable, and the
#    display says when a model came from here rather than from the person.
PROVIDERS: Dict[str, dict] = {
    "gemini": {
        "roles": ("embed", "judge"),
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "key_env": "GEMINI_API_KEY", "key_field": "gemini_api_key",
        "model": {"embed": "gemini-embedding-2", "judge": "gemini-flash-lite-latest"},
    },
    "openai": {
        "roles": ("embed", "judge"),
        "base": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY", "key_field": "openai_api_key",
        "model": {"embed": "text-embedding-3-small", "judge": "gpt-4.1-mini"},
    },
    "anthropic": {
        "roles": ("judge",),
        "base": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY", "key_field": "anthropic_api_key",
        "model": {"judge": "claude-opus-5-5"},
    },
    # ⛔ a deterministic hash that knows no meaning — wiring and contract checks only, never quality
    "stub": {"roles": ("embed",), "base": "", "key_env": "", "key_field": "",
             "model": {"embed": "stub"}},
    "none": {"roles": ("embed", "judge"), "base": "", "key_env": "", "key_field": "",
             "model": {}},
}

# ★Limits are the provider's, measured — never ours★ (2026-10-06). Gemini's free tier refuses past 15 judge
# calls a minute and 500 a day (measured 2026-08-25/26, the provider's own quota ids) and counts embedding
# items at 100 a minute (2026-08-21). A provider not listed has ★no limit we know of★: it is not paced,
# and the first refusal teaches the real number (§rerank.budget learns the daily one from the 429).
# ⛔ They used to apply to ★every★ provider — a person on another judge was capped at Gemini's 500 a day
#    by our own counter, a throttle on volume nobody measured.
JUDGE_RPM = {"gemini": 15}
JUDGE_RPD = {"gemini": 500}
EMBED_RPM_ITEMS = {"gemini": 100}


def secrets_path() -> str:
    """Keys kept for brain, outside any repository (0600). ⛔ The old location wins while it exists."""
    from brain import store
    old = os.path.expanduser("~/.claude/brain/secrets.json")
    if os.path.exists(old):
        return old
    return os.path.join(store.brain_home(), "secrets.json")


def _config_engines() -> dict:
    try:
        from brain import store
        return store.load_config(tolerant=True).get("engines") or {}
    except Exception:                                    # noqa: BLE001
        return {}


def choice(role: str) -> dict:
    """The engine filling `role` right now: {provider, model, base_url, source, model_source, effort}.

    source = "env" | "config" | "unset" — shown, so nobody has to guess why an engine is in use.
    """
    env = _ROLE_ENV[role]
    cfg = _config_engines().get(role) or {}
    provider = (os.environ.get("BRAIN_%s_PROVIDER" % env) or "").strip().lower()
    source = "env" if provider else ""
    if not provider and cfg.get("provider"):
        provider, source = str(cfg["provider"]).strip().lower(), "config"
    if not provider:
        provider, source = "none", "unset"
    if provider not in PROVIDERS or role not in PROVIDERS[provider]["roles"]:
        return {"provider": provider, "model": "", "base_url": "", "source": source,
                "model_source": "", "effort": "", "error": "unknown"}
    # the judge's model also answers to the older variable name
    legacy_model = os.environ.get("BRAIN_RERANK_MODEL", "") if role == "judge" else ""
    model = (os.environ.get("BRAIN_%s_MODEL" % env) or legacy_model or cfg.get("model") or "")
    model_source = "chosen" if model else "default"
    model = model or PROVIDERS[provider]["model"].get(role, "")
    base = (os.environ.get("BRAIN_%s_BASE_URL" % env) or cfg.get("base_url")
            or PROVIDERS[provider]["base"]).rstrip("/")
    effort = os.environ.get("BRAIN_%s_EFFORT" % env) or cfg.get("effort") or ""
    if not effort and provider == "anthropic" and model_source == "default":
        effort = "low"                                   # the default model is used as a quick judge
    return {"provider": provider, "model": model, "base_url": base, "source": source,
            "model_source": model_source, "effort": effort, "error": ""}


def is_local(c: dict) -> bool:
    """Does this engine stay on this machine (a local server) — then nothing leaves."""
    host = urllib.parse.urlsplit(c.get("base_url") or "").hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


def _official(c: dict) -> bool:
    return (c.get("base_url") or "") == PROVIDERS.get(c.get("provider", ""), {}).get("base")


def api_key(c: dict) -> str:
    """The key for this engine: the provider's variable → the secrets file. "" when none is needed.

    ⛔ Raises only when the engine needs one — a local OpenAI-compatible server does not.
    """
    p = PROVIDERS.get(c.get("provider", ""), {})
    if c.get("provider") in ("stub", "none"):
        return ""
    k = os.environ.get(p.get("key_env") or "", "").strip()
    if not k:
        try:
            with open(secrets_path(), encoding="utf-8") as fh:
                k = (json.load(fh).get(p.get("key_field") or "") or "").strip()
        except (OSError, ValueError):
            k = ""
    if not k and _official(c):
        from brain.vectors import NoKey
        raise NoKey("no %s key — set %s, or put {\"%s\": \"...\"} in %s"
                    % (c.get("provider"), p.get("key_env"), p.get("key_field"), secrets_path()))
    return k


def available(role: str) -> bool:
    c = choice(role)
    if c["provider"] == "none" or c.get("error") or not c["model"]:
        return False
    if c["provider"] == "stub":
        return True
    try:
        api_key(c)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def endpoint_host(role: str) -> str:
    """The host this role actually talks to — "" when nothing goes out (none · stub)."""
    c = choice(role)
    if c["provider"] in ("none", "stub") or c.get("error"):
        return ""
    return urllib.parse.urlsplit(c["base_url"]).hostname or ""


def judge_id(model: str = "") -> str:
    """Which judge is speaking — provider/model[@base]. Part of every cache key and calibration stamp:
    a score from one judge is not a score on another's scale."""
    c = choice("judge")
    if model:
        c = dict(c, model=model)
    tail = "" if _official(c) else "@" + (urllib.parse.urlsplit(c["base_url"]).netloc or "")
    return "%s/%s%s" % (c["provider"], c["model"], tail)


def fingerprint(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:16]


# ── the judge call ──────────────────────────────────────────────────────────
class JudgeError(RuntimeError):
    """A failed judge call that can still say ★why★ — a limit, a refusal and a network fault differ."""

    def __init__(self, status: int, code: str, message: str, daily: bool = False,
                 limit: float = 0.0, retry_after: float = 0.0):
        super().__init__("%s %s %s" % (status, code, message))
        self.status, self.code, self.message = status, code, message
        self.daily, self.limit, self.retry_after = daily, limit, retry_after


def _http(url: str, headers: Dict[str, str], body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=dict(headers, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:                                # noqa: BLE001
            payload = {}
        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        try:
            retry = float(exc.headers.get("retry-after") or 0)
        except (TypeError, ValueError):
            retry = 0.0
        daily, limit = False, 0.0
        if err and "details" in err:                     # Gemini names its walls (§vectors.parse_quota)
            from brain.vectors import parse_quota
            q = parse_quota(err)
            daily, limit = bool(q["daily"]), float(q["limit"] or 0)
            retry = retry or float(q["retry_after"] or 0)
        raise JudgeError(exc.code, str(err.get("type") or err.get("status") or err.get("code") or ""),
                         str(err.get("message") or "")[:200], daily, limit, retry) from None


def judge_text(prompt_text: str, timeout: float = 25.0, model: str = "") -> str:
    """Send one judging prompt to the chosen judge and return its text answer.

    `model` overrides the chosen model on the same provider (a caller measuring one model against another).
    ⛔ Raises JudgeError / OSError / NoKey on failure — the caller turns that into ★None★, never zeros.
    """
    c = choice("judge")
    if model:
        c = dict(c, model=model)
    # ⛔ ★the door masks, whoever opens it★ (§privacy) — rerank already masked its excerpts, but a
    #    second caller must not have to remember to. Masking is idempotent, so the cache key holds.
    from brain import privacy
    prompt_text, _hidden = privacy.scrub(prompt_text)
    p = c["provider"]
    if p == "gemini":
        data = _http("%s/models/%s:generateContent" % (c["base_url"], c["model"]),
                     {"x-goog-api-key": api_key(c)},
                     {"contents": [{"parts": [{"text": prompt_text}]}],
                      "generationConfig": {"temperature": 0,
                                           "responseMimeType": "application/json"}},
                     timeout)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise JudgeError(0, "shape", "no candidate text") from None
    if p == "openai":
        key = api_key(c)
        data = _http("%s/chat/completions" % c["base_url"],
                     {"Authorization": "Bearer %s" % key} if key else {},
                     {"model": c["model"], "temperature": 0,
                      "messages": [{"role": "user", "content": prompt_text}]},
                     timeout)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise JudgeError(0, "shape", "no choice text") from None
    if p == "anthropic":
        body = {"model": c["model"], "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt_text}]}
        if c.get("effort"):
            body["output_config"] = {"effort": c["effort"]}
        data = _http("%s/messages" % c["base_url"],
                     {"x-api-key": api_key(c), "anthropic-version": "2023-06-01"},
                     body, timeout)
        if data.get("stop_reason") == "refusal":
            raise JudgeError(200, "refusal", str((data.get("stop_details") or {}).get("category")))
        text = "".join(b.get("text", "") for b in (data.get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "text")
        if not text:
            raise JudgeError(0, "shape", "no text block")
        return text
    raise JudgeError(0, "no-engine", "no judge engine is chosen (brain engines)")


# ── showing it ──────────────────────────────────────────────────────────────
def describe() -> List[dict]:
    """One row per role — what the screens print. Never prints a key, only whether one is there."""
    rows = []
    for role in ROLES:
        c = choice(role)
        try:
            has_key = bool(api_key(c)) or c["provider"] in ("none", "stub") or not _official(c)
        except Exception:                                # noqa: BLE001
            has_key = False
        rows.append(dict(c, role=role, ready=available(role), has_key=has_key,
                         local=is_local(c), host=endpoint_host(role)))
    return rows


def keys_present() -> List[str]:
    """Providers whose key can be found here — to ★suggest★ a choice, never to make one."""
    out = []
    for name, p in PROVIDERS.items():
        if not p["key_env"]:
            continue
        try:
            if api_key({"provider": name, "base_url": p["base"]}):
                out.append(name)
        except Exception:                                # noqa: BLE001
            pass
    return out


def set_choice(role: str, provider: str, model: str = "", base_url: str = "",
               effort: str = "") -> dict:
    """Write one role's engine into config.json. ⛔ Only a person (CLI · installer) calls this."""
    from brain import store
    provider = provider.strip().lower()
    if role not in ROLES:
        raise ValueError("role must be one of %s" % ", ".join(ROLES))
    if provider not in PROVIDERS or role not in PROVIDERS[provider]["roles"]:
        ok = [n for n, p in PROVIDERS.items() if role in p["roles"]]
        raise ValueError("%s cannot be the %s engine — choose one of %s"
                         % (provider, role, ", ".join(ok)))
    cfg = store.load_config()             # ⛔ not tolerant — no config means not installed yet
    eng = cfg.setdefault("engines", {})
    entry = {"provider": provider}
    if model:
        entry["model"] = model
    if base_url:
        entry["base_url"] = base_url.rstrip("/")
    if effort:
        entry["effort"] = effort
    eng[role] = entry
    store.save_config(cfg)
    return entry


_ASSIGN = re.compile(r"^(embed|judge)=([a-z]+)$")


def parse_assignments(items: List[str]) -> Dict[str, str]:
    """`embed=openai judge=anthropic` → {role: provider}."""
    out = {}
    for it in items:
        m = _ASSIGN.match(it.strip().lower())
        if not m:
            raise ValueError("expected role=provider, e.g. judge=anthropic — got %r" % it)
        out[m.group(1)] = m.group(2)
    return out
