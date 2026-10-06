# Memory Index

> This file is a **seed** planted by `local-brain`. It is deliberately empty — the
> memories come from your own work. Knowing what the three sections below are for
> is what makes the brain pull its weight.
>
> The `<!-- brain: … -->` after a heading tells `brain index-audit` what that section is
> for (`directives` · `signals` · `progress`). Rename or translate the heading freely —
> keep the marker. It does not show when the file is rendered.

## 🧠 How to find a memory — the `brain` MCP

```
recall(query="the question in natural language", terms=[synonyms, other spellings, related identifiers, 5-10 of them])
```

⛔ **Always pass `terms`.** Memories are written in **different words** than your
question ("stopped" in the question, "discontinued" in the memory), and a question
that shares no word with its memory finds nothing — give other spellings in every
language your notes use.

- `recall` — past decisions, lessons, measurements, ⛔rules. Any time "last time" or
  "what was that again" comes up
- `timeline` — for questions with **no keyword at all**: "check what we did before".
  It is a chronological lookup, not a search
- `remember` — only what stays true later (never momentary numbers or one-off state)
- `reindex` — after editing a memory file by hand
- `brain_status` — distortion diagnostics (orphans, broken links, stale evidence,
  duplicates) · `brain_suggest` — where to start when tidying memories

## ⛔ Standing instructions (applied without being asked) <!-- brain: directives -->

> Only put things here that **nobody asks permission for**. These are the rules that
> must surface at the moment you are about to act on your own. Recall cannot cover
> this slot, because there is no question to trigger it.
>
> ⚠️ Do not put progress reports, measurements or incident histories here. Those come
> back through recall when asked. If this section grows, the file reaches the read
> limit and everything past it is **silently** not read.
> `brain index-audit` judges, per line, whether recall would still reach it if deleted.

- (accumulate your team's ⛔rules here — pre-push checks, auto-deploy branches,
  commands that are never run)

## 🔎 Touch these topics and **recall first, always** <!-- brain: signals -->

> Do not copy the content here — keep only the **signal that you must ask**. Write
> down the places that have already burnt you.

- (e.g. before touching deploys, migrations or billing code → `recall(terms=[...])`)

## 🔴 Currently working on <!-- brain: progress -->

> Each line is a **one-line index**. Keep measurements, evidence and traps inside the
> linked topic file, not here. When something finishes, drop it from this list and
> leave it in the topic file — recall will reach it.

- (one line per thread in progress)
