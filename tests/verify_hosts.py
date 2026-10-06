"""Host-adapter check — ★is the brain glued to just one tool★. (local · budget 0)

## Why (user requirement, 2026-09-02)

> *"It has to be able to plug into any LLM that comes along. Right now it works with claude code,
>  but later it also has to connect with codex."*

The brain's substance was never host-specific to begin with. What was tangled up was ★four edges★
(home · prompt history · hooks · tool names), and those were pulled into one place, `brain/hosts.py`.

⛔ What this check protects is ★that seam never leaking again★. Hardcode a path and it goes
   ★silently empty★ on a different host, and calibration's positive control group disappears.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import hosts                                   # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\nhost adapters — is it glued to just one tool\n" + "=" * 72)

    names = [h.name for h in hosts.all_hosts()]
    check("three or more hosts are declared (claude-code · codex · generic)",
          {"claude-code", "codex", "generic"} <= set(names), str(names))

    # ── ★the brain still runs with no host at all★ ──────────────────────────────────
    g = [h for h in hosts.all_hosts() if h.name == "generic"][0]
    check("generic ★always holds★ (used via CLI/MCP even with no host)", g.detect())
    check("generic never ★invents what isn't there★ (0 prompts · no hooks)",
          g.prompts() == [] and g.hook_events() == {})

    # ── does it answer the whole contract ──────────────────────────────────────────────
    for h in hosts.all_hosts():
        i = h.info()
        ok = (isinstance(i["root"], str) and i["root"]
              and isinstance(h.prompts(limit=3), list)
              and isinstance(h.memory_dirs(), list)
              and isinstance(h.hook_events(), dict))
        check("%s answers the whole contract" % h.name, ok)
        for c in hosts.CANONICAL:
            if not isinstance(h.tool_names(c), list):
                check("%s's tool_names(%s) is a list" % (h.name, c), False)

    # ── hosts actually found on this machine ────────────────────────────────────────
    found = hosts.detected()
    print("\n  found on this machine: %s" % ([h.name for h in found] or "none"))
    for h in found:
        i = h.info()
        print("    %-12s home %-22s hook %s" % (h.name, i["root"], os.path.basename(i["hooks_file"])))
        # ⛔ ★automatic recall and the behaviour layer are half of the brain's value★ — measure whether both are possible
        check("%s: automatic recall is possible (user_prompt_submit hook)" % h.name, i["can_auto_recall"])
        check("%s: the behaviour layer is possible (pre_tool_use hook)" % h.name, i["can_guard"])
        check("%s: knows its hook config file path" % h.name, bool(i["hooks_file"]))

    # ── ★can BRAIN_HOST pin it down★ ────────────────────────────────
    saved = os.environ.get("BRAIN_HOST")
    try:
        for want in [h.name for h in found] or ["generic"]:
            os.environ["BRAIN_HOST"] = want
            check("BRAIN_HOST=%s actually picks that one" % want,
                  hosts.active(refresh=True).name == want, hosts.active().name)
        os.environ["BRAIN_HOST"] = "no-such-host"
        check("★an unknown host name falls back to whatever was found★ (a typo shouldn't stop the brain)",
              hosts.active(refresh=True).name in [h.name for h in found] + ["generic"],
              hosts.active().name)
    finally:
        os.environ.pop("BRAIN_HOST", None)
        if saved:
            os.environ["BRAIN_HOST"] = saved
        hosts.active(refresh=True)

    # ── ★does calibration actually use the adapter★ (a hardcode still lurking gets caught here) ──
    from brain import calibrate as cal
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "brain", "calibrate.py"), encoding="utf-8").read()
    check("calibration ★never hardcodes a path★ (`~/.claude/projects` must not remain)",
          "~/.claude/projects" not in src)
    check("calibration calls the host adapter", "hosts.active()" in src)
    if found:
        check("calibration actually gets prompts (0 means the positive control group is gone)",
              len(cal._real_prompts(limit=50)) > 0, "%d found" % len(cal._real_prompts(limit=50)))

    # ── ★are the behaviour-layer rules tied to a host★ (2026-09-02) ──────────
    #    write `Bash` directly into a rule and ★nothing fires at all on Codex★ — and it's silent about it.
    #    ⛔ brain ships no rules (2026-10-06), so what is checked is ★the code that writes rules★
    #       (`_tools_for` for new ones, `_migrate_tools` for old ones) and then whatever is active here.
    from brain import guard, ruledisc
    import brain.hosts as H
    made = {sig: ruledisc._tools_for(sig) for sig in
            ("git push", ".tsx", "database/migrations", "Seeder", "make deploy")}
    bad = ["%s:%s" % (k, w) for k, v in made.items() for w in v if w not in H.CANONICAL]
    check("★a newly learned rule gets canonical action names★ (never a host's own tool name)",
          not bad, ", ".join(bad[:4]) or "%d signal shapes" % len(made))
    old = {"learned": [{"id": "x", "tools": ["Bash", "Edit", "MultiEdit", "Write"]}]}
    ruledisc._migrate_tools(old)
    check("★an old rule's host tool names are migrated★ (Bash/Edit/Write → canonical)",
          all(w in H.CANONICAL for w in old["learned"][0]["tools"]), str(old["learned"][0]["tools"]))
    host_specific = []
    for r in guard.active_rules():
        for w in r.get("tools", []):
            if w not in H.CANONICAL:
                host_specific.append("%s:%s" % (r.get("id", "?"), w))
    check("every rule active here uses canonical action names",
          not host_specific, ", ".join(host_specific[:4]) or "all %d rules" % len(guard.active_rules()))

    # does the same rule ★fire on both hosts★ — this is the whole point of the work
    # ⛔ against ★fixture rules★ — the question is the adapter seam, not which rules this machine has
    _fixture = [{"id": "x-shell", "tools": ["run_shell"], "match": ["git push"], "memories": []},
                {"id": "x-edit", "tools": ["edit_file"], "match": ["database/migrations"],
                 "memories": []}]
    _real_active = guard.active_rules
    guard.active_rules = lambda: list(_fixture)
    saved2 = os.environ.get("BRAIN_HOST")
    probes = {"run_shell": ("git push origin main", "command"),
              "edit_file": ("a/database/migrations/x.php", "file_path")}
    try:
        for hname in [h.name for h in found]:
            os.environ["BRAIN_HOST"] = hname
            hosts.active(refresh=True)
            h = hosts.active()
            for canon, (payload, field) in probes.items():
                names = h.tool_names(canon)
                if not names:
                    continue
                got = guard.match_rules(names[0], {field: payload})
                check("%s: `%s`(%s) fires a rule" % (hname, canon, names[0]),
                      len(got) > 0, "%d hit(s)" % len(got))
    finally:
        guard.active_rules = _real_active
        os.environ.pop("BRAIN_HOST", None)
        if saved2:
            os.environ["BRAIN_HOST"] = saved2
        hosts.active(refresh=True)

    # ── ⛔⛔ ★does the data home move without saying anything★ (the most dangerous spot) ──────
    #    if home follows the host, on a day you use Codex the index · learned rules · score history
    #    ★look like they vanished★ (the files are still there, nobody is reading them).
    from brain import store
    import shutil as _sh
    import tempfile as _tf
    here = store.brain_home()
    saved3 = os.environ.get("BRAIN_HOST")
    try:
        for hname in [h.name for h in found]:
            os.environ["BRAIN_HOST"] = hname
            hosts.active(refresh=True)
            check("★home does not move with the host★ (stays the same even switched to %s)" % hname,
                  store.brain_home() == here, store.brain_home())
    finally:
        os.environ.pop("BRAIN_HOST", None)
        if saved3:
            os.environ["BRAIN_HOST"] = saved3
        hosts.active(refresh=True)

    # for someone installing fresh — ★it follows the host★ — only when there is no legacy spot
    real_legacy = store._LEGACY_HOME
    tmp = _tf.mkdtemp()
    try:
        store._LEGACY_HOME = os.path.join(tmp, "nope")
        os.environ["BRAIN_HOST"] = "generic"
        hosts.active(refresh=True)
        check("★there is a home even with no host★ (the brain runs fine without one)",
              store.brain_home().endswith(".brain"), store.brain_home())
        os.environ["BRAIN_HOME"] = os.path.join(tmp, "forced")
        check("BRAIN_HOME ★always wins★", store.brain_home().endswith("forced"),
              store.brain_home())
    finally:
        os.environ.pop("BRAIN_HOME", None)
        os.environ.pop("BRAIN_HOST", None)
        store._LEGACY_HOME = real_legacy
        hosts.active(refresh=True)
        _sh.rmtree(tmp, ignore_errors=True)
    check("the check put the home back the way it was", store.brain_home() == here, store.brain_home())

    # ⛔ ★did already-learned rules★ move too — fix only the code and the data is left behind
    from brain import ruledisc
    legacy = []
    for r in ruledisc.load().get("learned", ()):
        for w in r.get("tools", ()):
            if w in ruledisc._LEGACY_TOOLS:
                legacy.append("%s:%s" % (r.get("id", "?"), w))
    check("★learned rules moved to canonical names too★ (fix only the code and the data is left behind)",
          not legacy, ", ".join(legacy[:4]) or "0 old names")

    # does the migration ★leave an unknown name alone★ — a human may have written it by hand
    probe = {"learned": [{"id": "x", "tools": ["Bash", "SomeCustomTool"]}]}
    ruledisc._migrate_tools(probe)
    check("migration ★leaves an unknown name untouched★ (keeping is safer than deleting)",
          probe["learned"][0]["tools"] == ["run_shell", "SomeCustomTool"],
          str(probe["learned"][0]["tools"]))

    # ── ★who is calling★ — the pin, and what happens without it ────────────────────
    #    ⛔ 2026-09-21: on a machine with both hosts, `active()` answers "the first one found",
    #       which is a ★property of the machine★, not of the process that called us. Codex sends
    #       `Bash` for shell but `apply_patch` for edits, so unpinned the shell rules keep firing
    #       while every edit rule goes silent — the failure shape that hides best.
    import sys as _sys
    saved_argv, saved_env = list(_sys.argv), os.environ.get("BRAIN_HOST")
    try:
        _sys.argv = ["brain-guard", "--host", "codex", "--keep-me"]
        got = hosts.pin_from_argv()
        check("`--host codex` pins the host", got == "codex" and hosts.active().name == "codex", got)
        check("the flag is ★taken out of argv★ (whatever parses next would choke on it)",
              _sys.argv == ["brain-guard", "--keep-me"], str(_sys.argv))
        _sys.argv = ["brain-guard", "--host=claude-code"]
        check("`--host=x` form works too", hosts.pin_from_argv() == "claude-code")
        # ⛔ control — an unknown name must ★change nothing★ and never raise (a hook may not block)
        before = hosts.active().name
        _sys.argv = ["brain-guard", "--host", "nosuchhost"]
        check("an unknown host name is dropped, silently and safely",
              hosts.pin_from_argv() == "" and hosts.active().name == before)
        # ★the pin is what makes an edit rule reachable on Codex★ — measured names, not guessed
        hosts.pin("codex")
        cx = hosts.active()
        hosts.pin("claude-code")
        cc = hosts.active()
        check("Codex's ★measured★ names are in the adapter (`Bash` for shell · `apply_patch` for edits)",
              "Bash" in cx.tool_names("run_shell") and "apply_patch" in cx.tool_names("edit_file"))
        check("★claude-code alone does not cover Codex's edit name★ — so the pin has to exist",
              "apply_patch" not in cc.tool_names("edit_file") + cc.tool_names("write_file"))
    finally:
        _sys.argv = saved_argv
        os.environ.pop("BRAIN_HOST", None)
        if saved_env:
            os.environ["BRAIN_HOST"] = saved_env
        hosts.active(refresh=True)

    # ── ★what it reads★ — one log line, per host shape ─────────────────────────────
    CLAUDE_LINE = ('{"timestamp":"2026-09-21T00:00:00Z","message":{"content":'
                   '[{"type":"tool_use","name":"Bash","input":{"command":"git status"}}]}}')
    CLAUDE_EDIT = ('{"timestamp":"2026-09-21T00:00:00Z","message":{"content":'
                   '[{"type":"tool_use","name":"Edit","input":{"file_path":"/a/b/card.tsx"}}]}}')
    CODEX_LINE = ('{"timestamp":"2026-09-21T00:00:00Z","payload":{"type":"custom_tool_call",'
                  '"name":"exec","input":"text( await tools.exec_command({cmd:\\"git status\\"}) )"}}')
    CODEX_EDIT = ('{"timestamp":"2026-09-21T00:00:00Z","payload":{"type":"custom_tool_call",'
                  '"name":"exec","input":"const patch = \\"*** Begin Patch\\\\n'
                  '*** Update File: /a/b/card.tsx\\\\n\\""}}')
    by = {h.name: h for h in hosts.all_hosts()}
    cc, cx = by["claude-code"], by["codex"]
    check("claude-code reads its own shell line", cc.tool_calls(CLAUDE_LINE) == [("Bash", "shell", "git status")],
          str(cc.tool_calls(CLAUDE_LINE)))
    check("claude-code reads its own edit line",
          cc.tool_calls(CLAUDE_EDIT) == [("Edit", "path", "/a/b/card.tsx")], str(cc.tool_calls(CLAUDE_EDIT)))
    check("codex digs the command ★out of the JS wrapper★",
          cx.tool_calls(CODEX_LINE) == [("exec", "shell", "git status")], str(cx.tool_calls(CODEX_LINE)))
    check("codex digs the path ★out of the patch text★",
          ("exec", "path", "/a/b/card.tsx") in cx.tool_calls(CODEX_EDIT), str(cx.tool_calls(CODEX_EDIT)))
    # ⛔ control — ★each reader must be blind to the other's shape★. Without this row, a parser that
    #    matched anything would look perfect here and would quietly double-count in real use.
    check("★control★: claude-code's reader finds nothing in a Codex line", cc.tool_calls(CODEX_LINE) == [])
    check("★control★: codex's reader finds nothing in a Claude Code line", cx.tool_calls(CLAUDE_LINE) == [])
    for h in (cc, cx):
        pats = h.transcripts()
        check("%s's transcript glob lives under its own home" % h.name,
              bool(pats) and all(p.startswith(h.root()) for p in pats), str(pats))

    # ── ★installed is not running★ — Codex trusts hooks by hash (2026-09-28) ───────
    #    Hooks were wired into Codex on 09-21 and, of the 13 sessions that followed, the behaviour
    #    layer fired in ★2★ — both of them test runs made with `--dangerously-bypass-hook-trust`.
    #    Eleven real sessions got nothing, and every screen said "wired". The installer now asks the
    #    adapter which entries are still unapproved, so this row protects that question.
    import json as _json                                  # noqa: E402
    import tempfile as _tmp                               # noqa: E402
    cx = by["codex"]
    hooks_doc = {"hooks": {"UserPromptSubmit": [{"matcher": "", "hooks": [
        {"type": "command", "command": "/x/bin/brain-hook --host codex"}]}]}}
    saved_home = os.environ.get("CODEX_HOME")
    with _tmp.TemporaryDirectory() as home:
        os.environ["CODEX_HOME"] = home
        with open(os.path.join(home, "hooks.json"), "w", encoding="utf-8") as fh:
            _json.dump(hooks_doc, fh)
        open(os.path.join(home, "config.toml"), "w", encoding="utf-8").write("")
        check("★an unapproved hook is reported★ (written ≠ running)",
              cx.untrusted_hooks() == ["UserPromptSubmit[0][0]"], str(cx.untrusted_hooks()))
        # ⛔ control — with the trust record present it must go quiet, or the warning is noise
        #    that people learn to scroll past.
        key = '%s:user_prompt_submit:0:0' % os.path.join(home, "hooks.json")
        open(os.path.join(home, "config.toml"), "w", encoding="utf-8").write(
            '[hooks.state."%s"]\ntrusted_hash = "sha256:abc"\n' % key)
        check("★control★: once it is trusted, nothing is reported",
              cx.untrusted_hooks() == [], str(cx.untrusted_hooks()))
        # a hook that is not ours is never counted — other tools live in that file too
        hooks_doc["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] = "/opt/other-tool hook"
        with open(os.path.join(home, "hooks.json"), "w", encoding="utf-8") as fh:
            _json.dump(hooks_doc, fh)
        open(os.path.join(home, "config.toml"), "w", encoding="utf-8").write("")
        check("★someone else's hook is never counted as ours★", cx.untrusted_hooks() == [])
    os.environ.pop("CODEX_HOME", None)
    if saved_home:
        os.environ["CODEX_HOME"] = saved_home
    inst = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "brain", "install.py"), encoding="utf-8").read()
    check("the installer ★says so★ instead of reporting the hooks as done",
          "untrusted_hooks" in inst and "install.hooks_untrusted" in inst)

    # ⛔ rule discovery must ★not★ be hardcoded to one host's folder any more (2026-09-21)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rd = open(os.path.join(root, "brain", "ruledisc.py"), encoding="utf-8").read()
    check("rule discovery asks ★every detected host★ for its transcripts",
          "hosts.detected()" in rd and "host.tool_calls(" in rd)

    # ⛔ did the installer bake in a tool name
    #    (2026-09-02: the installer moved from `install.sh` to `brain/install.py`. This check was
    #     still looking at the old spot and went red — ★the check has to follow the source of truth too★.)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    inst = open(os.path.join(root, "brain", "install.py"), encoding="utf-8").read()
    check("the installer gets its matcher ★from the adapter★ (never bakes in a tool name)",
          'matcher="Bash"' not in inst and 'tool_names("run_shell")' in inst)
    wrapper = open(os.path.join(root, "install.sh"), encoding="utf-8").read()
    check("`install.sh` is ★a thin wrapper★ (so a second copy for Windows never gets built)",
          "brain.install" in wrapper and len(wrapper.splitlines()) < 30,
          "%d lines" % len(wrapper.splitlines()))

    print("=" * 72)
    print("❌ %d failure(s): %s" % (len(FAIL), ", ".join(FAIL)) if FAIL else "✅ all passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
