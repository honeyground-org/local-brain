"""The ★frozen★ real-prompt sample the checks use.

## Why it has to be frozen (measured 2026-08-20)

The checks called `calibrate._real_prompts(limit=N)` to draw a sample. That function gathers prompts
from every `~/.claude/projects/*/*.jsonl` and builds a sample with `random.seed(7)`. A fixed seed looks
like it should reproduce, but ★the population grows with every prompt★ — even this session's own
words go straight into that pool. Because the sample's membership shifts,

    the same code gives a different verdict.

That same day, with no code change, `verify_threshold`'s fire rate moved **68% → 73% → 71%**, and
the newly-built `verify_related` reported 1 mismatch in ① — but ★that query could no longer be
found★ (it wasn't in the next run's sample). A failure that can't be reproduced can't be fixed either.

## How

Drawn once and written to `tests/eval/prompts.json`, and read from there ever after. Refreshing it is
an explicit human action — deleting that file (an automatic refresh would just drift again).

⛔ It's personal data — `tests/eval/` is gitignored (a person's own words end up in it verbatim).
⛔ Never put a made-up query in it · lesson_an_evaluation_set_i_invented_measured_my_imagination
"""
from __future__ import annotations

import json
import os
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tests", "eval", "prompts.json")
DEFAULT_N = 300


def frozen_prompts(n: int = DEFAULT_N) -> List[str]:
    """The frozen sample. Draws and writes it once if absent, otherwise uses it as-is."""
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return list(data.get("prompts") or [])[:n] if n else list(data.get("prompts") or [])

    from brain import calibrate
    prompts = calibrate._real_prompts(limit=n)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "_": ["★a frozen sample★ — keeps the check from measuring something different every run.",
                  "to refresh it, delete this file and run the check once (it gets drawn again then).",
                  "⛔ personal data — do not commit it (tests/eval is gitignored).",
                  "⛔ never ★hand-write a query★ into this — that measures your own imagination instead."],
            "created_for": "verify_threshold · verify_related",
            "n": len(prompts),
            "prompts": prompts,
        }, fh, ensure_ascii=False, indent=1)
    return prompts
