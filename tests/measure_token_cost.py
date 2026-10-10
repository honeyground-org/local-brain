#!/usr/bin/env python3
"""★What each way costs in context★ — characters that must enter the model to get one answer. (local · budget 0 · a measurement)

## Why this exists

"The brain saves tokens" is a claim, and a claim without an instrument rots. This measures the
thing that actually costs money: ★how much text has to be put into the model's context★ to answer
one question, and what a month of real use adds up to.

## ⛔ What is measured exactly, and what is only converted

  ★measured★    characters and bytes. Every number below is counted, not estimated.
  ★converted★   tokens. There is no offline tokenizer for this provider, so tokens are
                characters ÷ a ratio that is ★stated, not hidden★ (see CHARS_PER_TOKEN).

⛔ An earlier attempt tried to derive the ratio empirically from this machine's own transcripts
   (visible assistant text ÷ `usage.output_tokens`) and got 0.63 characters per token — impossible
   for plain text. The cause: a single assistant turn is split across several JSONL lines and the
   `usage` block is not per-line, so the division is meaningless. ★It is reported as a stated
   assumption rather than a fake measurement.★ If an exact figure is ever needed, count it with
   the provider's own token-counting endpoint.

## The three ways being compared

  brain        the hook's ★actual injected string★ (`hook._format`), plus the documents an agent
               opens afterwards if it wants the full text
  grep OR      every document whose text contains any content word of the question — an agent has
               to ★read them★ to find the answer, so the cost is the sum of their sizes
  index file   one always-loaded index (`MEMORY.md`) charged ★every session★, whether or not the
               session needed a memory

⛔ The grep rule is imported from `verify_vs_grep`, not reimplemented — the same rule in two places
   gets fixed in one.

How to run:  PYTHONPATH=. python3 tests/measure_token_cost.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics as stats
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from brain import calibrate, evalinit as ei, hook, search, store  # noqa: E402
import verify_vs_grep as vg  # noqa: E402  ★the grep rule lives there, not here★

# ★Stated assumption, not a measurement★ — characters per token.
#   Korean text is token-expensive (roughly one token per 1–1.5 Hangul characters); English prose
#   runs nearer 4 characters per token. This corpus is mixed, so the composition is measured below
#   and the conversion is reported as a ★range★ using these two ends.
CHARS_PER_TOKEN_KO = float(os.environ.get("BRAIN_CPT_KO", "1.2") or 1.2)
CHARS_PER_TOKEN_EN = float(os.environ.get("BRAIN_CPT_EN", "3.8") or 3.8)

DAYS = int(os.environ.get("BRAIN_COST_DAYS", "30") or 30)


def _tok_range(chars: float) -> str:
    """chars → 'lo–hi tokens' under the two stated ends."""
    hi = chars / CHARS_PER_TOKEN_KO          # most expensive (all Hangul)
    lo = chars / CHARS_PER_TOKEN_EN          # cheapest (all English)
    return "%s–%s" % (f"{int(lo):,}", f"{int(hi):,}")


def _mix_tokens(chars: float, ko_share: float) -> float:
    """chars → tokens using this corpus's ★measured★ script composition."""
    return chars * (ko_share / CHARS_PER_TOKEN_KO + (1 - ko_share) / CHARS_PER_TOKEN_EN)


def corpus_shape(db):
    rows = db.execute("SELECT name, title, description, body FROM docs").fetchall()
    sizes, hangul, latin, total = [], 0, 0, 0
    for r in rows:
        text = " ".join([r["name"], r["title"] or "", r["description"] or "", r["body"] or ""])
        sizes.append(len(text))
        hangul += len(re.findall(r"[가-힣]", text))
        latin += len(re.findall(r"[0-9A-Za-z]", text))
        total += len(text)
    sizes.sort()
    return {
        "docs": len(rows), "chars": total,
        "median": sizes[len(sizes) // 2] if sizes else 0,
        "mean": int(stats.mean(sizes)) if sizes else 0,
        "ko_share": hangul / float(hangul + latin or 1),
    }


def usage_volume():
    """★How much this person actually uses it★ — prompts and sessions in the window."""
    root = os.path.expanduser("~/.claude/projects")
    cutoff = time.time() - DAYS * 86400
    sessions = prompts = 0
    for fp in glob.glob(os.path.join(root, "*", "*.jsonl")):
        try:
            if os.path.getmtime(fp) < cutoff:
                continue
        except OSError:
            continue
        sessions += 1
        try:
            with open(fp, errors="replace", encoding="utf-8") as fh:
                for line in fh:
                    if '"type":"user"' not in line and '"type": "user"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") != "user":
                        continue
                    c = (d.get("message") or {}).get("content")
                    if isinstance(c, str) and c.strip():
                        prompts += 1
        except OSError:
            continue
    return {"sessions": sessions, "prompts": prompts, "days": DAYS}


def _index_names() -> set:
    """The memory names the always-loaded index file actually carries — its whole reach."""
    try:
        with open(os.path.join(store.memory_dir(), "MEMORY.md"), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return set()
    return (set(re.findall(r"\(([a-z0-9_]+)\.md\)", text))
            | set(re.findall(r"\[\[([a-z0-9_]+)\]\]", text)))


def main() -> int:
    db = store.connect()
    shape = corpus_shape(db)
    vol = usage_volume()
    thr = calibrate.threshold(db, 0)

    print("=" * 78)
    print("what one answer costs in context — measured %s" % time.strftime("%Y-%m-%d"))
    print("=" * 78)
    print("  corpus      %s documents · %s characters · median %s · mean %s"
          % (f"{shape['docs']:,}", f"{shape['chars']:,}", f"{shape['median']:,}", f"{shape['mean']:,}"))
    print("  composition Hangul %.0f%% / Latin %.0f%%  → conversion uses this mix"
          % (shape["ko_share"] * 100, (1 - shape["ko_share"]) * 100))
    print("  assumption  %.1f chars/token (Hangul) · %.1f (Latin) — ⛔ stated, not measured"
          % (CHARS_PER_TOKEN_KO, CHARS_PER_TOKEN_EN))
    print("  real use    %s sessions · %s prompts in the last %d days"
          % (f"{vol['sessions']:,}", f"{vol['prompts']:,}", vol["days"]))

    # ── ① the standing cost: one always-loaded index, charged every session ──
    print("\n" + "-" * 78)
    print("① standing cost — what is loaded ★whether or not it is needed★")
    print("-" * 78)
    idx_path = os.path.join(store.memory_dir(), "MEMORY.md")
    idx_chars = 0
    try:
        with open(idx_path, encoding="utf-8") as fh:
            idx_chars = len(fh.read())
    except OSError:
        pass
    print("  index file  %s chars/session  (%s tokens)   · %s"
          % (f"{idx_chars:,}", _tok_range(idx_chars), os.path.basename(idx_path)))
    print("  brain       ★0 chars/session★ — nothing is loaded unless the hook fires")
    print("              and a new memory adds ★0 chars★ to any standing file (it declares its own trigger)")

    # ── ② the per-answer cost ────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("② per-answer cost — text that must enter context to find one answer")
    print("-" * 78)
    A = ei.load_A()
    if not A:
        print("  ⏭  skipped — no labelled sample on this machine (`bin/brain eval-init`)")
        return 0

    docs_text = vg.load_files()
    body = {r["name"]: len(" ".join([r["name"], r["title"] or "", r["description"] or "",
                                     r["body"] or ""]))
            for r in db.execute("SELECT name,title,description,body FROM docs")}

    hook_payload, brain_read, grep_read, grep_n, fired = [], [], [], [], 0
    for q, gold in A:
        rows = search.recall(db, q, k=hook.MAX_ITEMS + 2, log=False)
        keep = hook.select(rows, thr)
        if keep:
            fired += 1
            hook_payload.append(len(hook._format(keep)))
        top3 = rows[:3]
        brain_read.append(sum(body.get(r["name"], 0) for r in top3))
        names = vg.grep(docs_text, vg.tokens(q))
        grep_n.append(len(names))
        grep_read.append(sum(len(docs_text[n]) for n in names))

    def line(label, vals, note=""):
        if not vals:
            print("  %-26s (no data)" % label)
            return 0
        med = int(stats.median(vals))
        print("  %-26s median %9s chars  (%14s tokens) %s"
              % (label, f"{med:,}", _tok_range(med), note))
        return med

    m_hook = line("brain · hook injects", hook_payload,
                  "← fired on %d of %d" % (fired, len(A)))
    m_brain = line("brain · opens top 3", brain_read, "← if the agent wants full text")
    m_grep = line("grep OR · must read", grep_read,
                  "← median %d documents matched" % int(stats.median(grep_n)))
    if m_grep and m_hook:
        print("\n  ★the hook's injection is %.0f× smaller than the grep-OR read★"
              % (m_grep / float(m_hook)))
    if m_grep and m_brain:
        print("  ★opening the top 3 is %.0f× smaller than the grep-OR read★"
              % (m_grep / float(m_brain)))

    # ── ③ a month of real use ────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("③ a month of ★this person's actual use★ (%d sessions · %d prompts / %d days)"
          % (vol["sessions"], vol["prompts"], vol["days"]))
    print("-" * 78)
    # ⛔ ★The firing rate for a monthly projection has to come from ★real prompts★, not from the
    #    memory-needed sample.★ The first version used the A-set rate (49%) against every prompt —
    #    measuring the benefit on one population and the cost on another, which is the exact trap
    #    this repository keeps writing down. Real prompts fire much more often, so the honest number
    #    is worse; it is reported as it comes out.
    real = calibrate._real_prompts()
    real_fired, real_payload = 0, []
    for q in real:
        rows = search.recall(db, q[:hook.MAX_PROMPT_CHARS], k=hook.MAX_ITEMS + 2, log=False)
        keep = hook.select(rows, thr)
        if keep:
            real_fired += 1
            real_payload.append(len(hook._format(keep)))
    ks = shape["ko_share"]
    fire_rate = real_fired / float(len(real) or 1)
    pay = int(stats.median(real_payload)) if real_payload else (m_hook or 0)
    print("  firing rate  ★%.0f%%★ of %d real prompts (not the %.0f%% of the memory-needed sample)"
          % (fire_rate * 100, len(real), 100.0 * fired / len(A)))
    print("  payload      median %s chars per firing on real prompts" % f"{pay:,}")
    # ★How often the old way would have to go looking★ — measured, not assumed.
    #   The index file names only a handful of memories. Whenever the memory that was actually
    #   needed is not one of them, an agent has to search and read files, and ★that is the cost the
    #   first version of this instrument charged at zero★ — which is the whole reason it concluded
    #   the brain was more expensive.
    named = _index_names()
    surfaced = miss = 0
    for q in real:
        rows = search.recall(db, q[:hook.MAX_PROMPT_CHARS], k=hook.MAX_ITEMS + 2, log=False)
        for r in hook.select(rows, thr):
            surfaced += 1
            miss += 0 if r["name"] in named else 1
    miss_rate = miss / float(surfaced or 1)
    print("  index reach  it names %d of %s memories → ★%.1f%% of what gets surfaced is out of its reach★"
          % (len(named), f"{shape['docs']:,}", miss_rate * 100))

    brain_month = vol["prompts"] * fire_rate * pay
    index_standing = vol["sessions"] * idx_chars
    searches = vol["prompts"] * fire_rate * miss_rate
    index_search = searches * (m_brain or 0)
    index_month = index_standing + index_search
    print("  brain        %s chars  = %s tokens"
          % (f"{int(brain_month):,}", f"{int(_mix_tokens(brain_month, ks)):,}"))
    print("               (prompts × firing rate %.0f%% × %s chars per firing · opens ★0 files★)"
          % (fire_rate * 100, f"{pay:,}"))
    print("  index file   %s chars  = %s tokens"
          % (f"{int(index_month):,}", f"{int(_mix_tokens(index_month, ks)):,}"))
    print("               standing %s + searching %s"
          % (f"{int(index_standing):,}", f"{int(index_search):,}"))
    print("               (%s searches × %s chars — ★a floor★: that is what reading just 3 documents"
          % (f"{int(searches):,}", f"{m_brain or 0:,}"))
    print("                costs, and without ranking an agent reads more, not fewer)")
    if brain_month and index_month:
        print("\n  ★standing cost alone★        brain %s vs index file %s tokens  → brain %+.0f%%"
              % (f"{int(_mix_tokens(brain_month, ks)):,}",
                 f"{int(_mix_tokens(index_standing, ks)):,}",
                 100.0 * (brain_month / index_standing - 1)))
        print("     ⛔ This is the number the first version reported, and it is ★the wrong question★:")
        print("        it charges the old way nothing for the searching it must actually do.")
        print("  ★with the searching counted★  brain %s vs index file %s tokens  → ★%.1f× cheaper★"
              % (f"{int(_mix_tokens(brain_month, ks)):,}",
                 f"{int(_mix_tokens(index_month, ks)):,}",
                 index_month / max(1.0, brain_month)))
        print("     files opened to answer: brain ★0★ (the excerpt is already in the prompt)")
        print("                             index file ★3 or more★, %.0f%% of the time" % (miss_rate * 100))
    print("\n  ⛔ Even this understates it: the search floor above assumes the agent reads exactly 3")
    print("     documents. Reading every grep match costs %s characters (§②)." % f"{m_grep:,}")
    print("  ⛔ Still not counted: what a ★wrong or missing★ answer costs (a re-asked question,")
    print("     a repeated mistake). That is the larger economic term and it is ★not measured★.")

    print("\n" + "=" * 78)
    print("⛔ characters are counted; tokens are converted under a stated ratio (see the docstring)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
