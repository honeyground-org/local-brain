"""Behaviour layer — attaches the guidance tied to ★the action about to happen★, right before that action.

Why this layer is needed
-------------------
MEMORY.md's ⛔ directives are "things never asked about", so recall cannot stand in for them. The moment
a user asks "what should I check before pushing" is already too late, or the question is never asked at
all. The moment guidance is needed is **the moment I am about to call `git push`**.

That moment can be caught — measured on 2026-08-18: `PreToolUse`'s hook `additionalContext` reaches the
model's context (confirmed with a probe token), and the payload carries `tool_name` and `tool_input`.
That is why this layer holds.

★The directive's original lives in a memory file★
------------------------------
This module holds no directive sentences of its own. It reads a memory file's `description` and attaches
it. So shrinking that line in MEMORY.md does not erase the content, and editing the memory makes the hook
attach the new sentence immediately. Avoiding a rule written in two places is the core of this design.

Frequency (measured 2026-08-18 · 175 transcripts · 43,965 tool calls)
-----------------------------------------------------------
Bash is 65.7% of the total (28,898), but within it `git push` is **3.55%**, `gh pr create` 2.51%,
`terraform` 0.93%. So once the command itself is checked, firing is rare. The hook itself runs on every
single Bash call, so ★the shell filters first and does not call Python★ (`bin/brain-guard`).

⛔ This hook blocks nothing — it only attaches context. Blocking is a human's job; a hook that judges
   stops work whenever it judges wrong.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List, Optional

from . import store

# ⛔ The memory directory is not nailed in — it is derived from config (store.memory_dir).
STATE_DIR = os.path.join(store.brain_home(), "guard")   # ⛔ the home is set by §store.brain_home

# ---------------------------------------------------------------------------
# Built-in rules — ★none★, and that is the design (2026-10-06).
#
# A rule is three things: an action (`git push`), a reason, and ★the memories that hold the
# wording★. The memories are a person's own files, so a rule written here would point at files
# that exist on exactly one machine. That is what the table used to be: twelve rules naming 41 of
# the author's memory files and the author's stack (`.blade.php`, `artisan`, `ecs exec`). On anyone
# else's machine they resolved to nothing, and the hook still spoke — `terraform plan` on an empty
# brain said "apply is forbidden", a policy from one company's infrastructure.
#
# ⇒ Every rule lives in the person's own `rules.json` (§ruledisc): learned from their tool use, or
#   written by them. A new user starts with zero rules and the behaviour layer stays silent until
#   their own history gives it something true to say. The author's twelve moved there too
#   (`"origin": "former-builtin"`), so nothing changed for the one machine they were written on.
#
# The rule shape (in rules.json):
#   tools   : canonical action names a PreToolUse matcher selects (`run_shell` · `edit_file` …)
#   match   : a string to look for inside tool_input (any one match fires)
#   memories: the memory names whose `description` is the wording
#   once    : "session" limits it to once per session (repeating the same line becomes noise)
# ⛔ Keep this list empty — `tests/verify_guard.py` fails the moment anything is added back.
# ---------------------------------------------------------------------------
RULES: List[dict] = []


def active_rules() -> List[dict]:
    """★The rules the behaviour layer actually uses★ — built-in (minus disabled) + learned (only enabled).

    ⛔ Nothing below this reads `RULES` directly. Leave a direct reader and a learned rule quietly drops
       out, and nobody sees that silence. (§ruledisc — it learns while the rules are in use)
    ⛔ A lazy import — ruledisc reads this module's RULES, so a top-level import would cycle.
    """
    try:
        from . import ruledisc
        return ruledisc.all_rules()
    except Exception:                                    # noqa: BLE001
        return list(RULES)                               # (empty) a broken learning layer attaches nothing


def _description(name: str) -> Optional[str]:
    """One line: a memory file's description. ★The hook does not hold directive sentences of its own★."""
    base = store.memory_dir()
    if not base:
        return None
    path = os.path.join(base, name + ".md")
    try:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(4000)
    except OSError:
        return None
    m = re.search(r"^description:\s*(.+)$", head, re.M)
    if not m:
        return None
    return m.group(1).strip().strip('"')


# ── ★session budget★ — what is known stays; only ★how much is shown★ is capped (2026-08-31) ────
#
# ⛔ Why this is needed (measured): while rules went 12 → 33, ★the count shown per session went 8 → 15★.
#    This design's own written-down warning is that exact state — "if guidance floods every session,
#    a human ignores the whole layer, and then it goes unread even when it is truly needed."
#
# ⛔ ★Disabling a rule is not the answer.★ Off means the knowledge is gone forever (`dismissed` is never
#    revived). What must shrink is not ★what is known★ but ★how much shows in one session★.
#
# Past the budget, ★only rare rules★ pass through — the rarer a rule fires, the more information that
# moment carries (a `git fetch` at 5% is background noise, an `ads-batch-run.sh` at 0.3% is signal).
# The firing rate is measured by the cron and written to `guard-rates.json`. ⛔ Absent that file, no cap
# is applied — ★not knowing is not grounds for blocking★ (no value means neither "rare" nor "common").
SESSION_CAP = int(os.environ.get("BRAIN_GUARD_SESSION_CAP", "12") or 12)
RARE_RATE = float(os.environ.get("BRAIN_GUARD_RARE_RATE", "1.0") or 1.0)
RATES = os.path.join(os.path.dirname(STATE_DIR), "guard-rates.json")


def _rates() -> Dict[str, float]:
    try:
        with open(RATES, encoding="utf-8") as fh:
            return {k: float(v) for k, v in json.load(fh).items()}
    except (OSError, ValueError, TypeError):
        return {}


def _session_spent(session: str) -> int:
    """How many rules have already fired this session — counted from marker files (no separate state)."""
    if not session:
        return 0
    try:
        pre = session + "."
        return sum(1 for n in os.listdir(STATE_DIR) if n.startswith(pre))
    except OSError:
        return 0


def within_budget(session: str, rule_id: str, rates: Optional[Dict[str, float]] = None) -> bool:
    """Is it within budget. ⛔ Even past budget, ★a rare rule★ still passes through."""
    rates = _rates() if rates is None else rates
    if not rates:
        return True                            # never measured → do not block
    if _session_spent(session) < SESSION_CAP:
        return True
    r = rates.get(rule_id)
    return r is not None and r < RARE_RATE


def _already_said(session: str, rule_id: str) -> bool:
    if not session:
        return False
    mark = os.path.join(STATE_DIR, "%s.%s" % (session, rule_id))
    if os.path.exists(mark):
        return True
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(mark, "w") as fh:
            fh.write("1")
    except OSError:
        pass                                  # a failed suppression still attaches the guidance
    return False


def match_rules(tool_name: str, tool_input: Dict) -> List[dict]:
    """⛔ ★It is deliberately cast wide★ — it does not check whether this is really a command position.

    Observed 2026-08-18: two rules fired just from that word appearing inside a script's heredoc
    (nobody was actually about to rebase). A command-position regex (after a line start · ; · && · |)
    could narrow it, but ★that opens gaps★: `if ...; then git push; fi`, `xargs git push`, an alias, a
    variable substitution.

    Comparing the costs gives the answer:
      · one false fire = the guidance gets read once more (and once per session, so it never accumulates)
      · one miss       = an unapproved push · overwriting someone else's merge · an apply
    ★A miss is overwhelmingly more expensive, so it is cast wide.★ Written down here so the next session
    does not re-ask "why is this so loose" — narrow it only after counting what would be missed.
    """
    blob = json.dumps(tool_input, ensure_ascii=False)
    hit = []
    for r in active_rules():
        if not _tool_matches(tool_name, r["tools"]):
            continue
        if any(m in blob for m in r["match"]):
            hit.append(r)
    return hit


def _host_tools(canonical: str, host=None) -> List[str]:
    """A canonical action name → the tool names of ★the current host★ (or of `host`). An empty list if the adapter does not know it."""
    try:
        from brain import hosts
        return hosts.tool_names(canonical, host)
    except Exception:                                    # noqa: BLE001
        return []


def _tool_matches(tool_name: str, wanted: List[str], host=None) -> bool:
    """Is this tool call the action a rule is aiming at.

    ⛔ ★Write a host's own tool name straight into a rule and it goes entirely silent on another host★
    (2026-09-02). A rule is written with a ★canonical name★ like `run_shell`·`edit_file`, and the adapter
    translates it to the current host's name — Claude Code uses `Bash`, Codex uses `shell`.

    ⚠️ Anything that is not a canonical name (an old hand-written rule · a learned rule) is compared
    ★literally★. The worst outcome is a rule going silently dark mid-migration.
    `host` — whose tool names `tool_name` is in. A hook call is the current host's; a call read back from
    a log is the host that wrote that log (§ruledisc.measure_fire · measure_active measure with this).
    """
    from brain import hosts
    for w in wanted:
        if w in hosts.CANONICAL:
            if tool_name in _host_tools(w, host):
                return True
        elif tool_name == w:                             # the old way — literal comparison
            return True
    return False


def _resolved(rule: dict) -> List[tuple]:
    """(name, description) for each of a rule's memories that ★exists here★."""
    out = []
    for name in rule.get("memories") or ():
        d = _description(name)
        if d:
            out.append((name, d))
    return out


def render(rules: List[dict]) -> str:
    """The text a user sees at the moment they call the tool.

    ⛔ ★A rule speaks only through memories that exist here★ (2026-10-06).
    The wording of a rule is its memories' descriptions; the `why` is only a header over them. A rule
    whose memories resolve to nothing has nothing true to say on this machine, so it says nothing —
    not even its `why`. (2026-09-10 stopped the dangling "call recall with this name" pointer; the
    `why` alone kept being printed, which on a stranger's machine meant an empty brain answering
    `terraform plan` with another person's policy.)
    """
    from brain import i18n                        # ⛔ local import — this runs on every tool call
    lines = []
    for r in rules:
        found = _resolved(r)
        if not found:
            continue
        lines.append(i18n.t("guard.rule.header", why=r["why"]))
        lines.extend("   · %s" % d for _, d in found)
        # ⛔ Do not fix the hint to one rule — as rules grow, a fixed word starts to mislead.
        lines.append(i18n.t("guard.rule.recall_hint",
                            names=", ".join(n for n, _ in found[:3])))
    return "\n".join(lines)


def _rehome() -> None:
    """Re-read the home after `--home` was taken from argv (the paths above were fixed at import)."""
    global STATE_DIR, RATES
    STATE_DIR = os.path.join(store.brain_home(), "guard")
    RATES = os.path.join(os.path.dirname(STATE_DIR), "guard-rates.json")


def main() -> int:
    # ⛔ ★before the payload★ — the tool name in it is resolved through the host's own names
    #    (measured 2026-09-21: Codex sends `Bash` for shell but ★`apply_patch`★ for edits, so
    #    without this every edit-side rule goes silent there while the shell ones still fire —
    #    the worst shape of failure: half-working, and quiet about it).
    from brain import hosts
    hosts.pin_from_argv()
    # ⛔ ★the same home the shell half read its signal file from★ (§store.adopt_home_from_argv)
    if store.adopt_home_from_argv():
        _rehome()
    try:
        payload = json.load(sys.stdin)
    except Exception:                                        # noqa: BLE001
        return 0
    tool = payload.get("tool_name") or ""
    rules = match_rules(tool, payload.get("tool_input") or {})
    # ⛔ ★Drop the rules with nothing to say here before any bookkeeping★ — otherwise a silent rule
    #    would spend the session budget and write its 'already said' marker without saying anything.
    rules = [r for r in rules if _resolved(r)]
    # ⛔ ★Check budget before 'already said'★ — order matters. `_already_said` has the side effect of
    #    ★writing★ the marker, so letting a budget-blocked rule through first would "record it as said
    #    without ever saying it" (it never fires again after that). A silent loss of knowledge.
    sid = payload.get("session_id") or ""
    _r = _rates()
    rules = [r for r in rules if within_budget(sid, r["id"], _r)]
    rules = [r for r in rules
             if not (r.get("once") == "session" and _already_said(sid, r["id"]))]
    if not rules:
        return 0
    text = render(rules)
    if not text.strip():
        return 0
    # ⛔ permissionDecision is never set — overriding the permission decision defeats other gates.
    #    This hook's job is to speak, not to permit.
    sys.stdout.write(hosts.active().hook_output("pre_tool_use", text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
