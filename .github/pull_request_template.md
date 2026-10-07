<!--
Title: `area: what changed` — for example `stores: retry a lost connection once`.
Every commit signed off: `git commit -s`.
The PR report (a bot comment) shows this change's impact and which sections it needs:
  low (docs, translations): Purpose · Checks
  medium: Purpose · Effect · Risks and rollback · Checks
  high (measurement code, storage, privacy, behaviour, install, CI): all five, with numbers in Effect
Preview it before you push: python3 .github/scripts/pr_report.py --local --body <this text in a file>
-->

## Purpose
<!-- What problem does this solve, and for whom? Link the issue (Fixes #123). -->

## Effect
<!-- What is different for a user, measured. For recall, speed or the threshold: before → after,
     measured the same way on the same corpus, and how you measured it. -->

## Scalability and extensibility
<!-- Does it extend an existing seam (one class per storage backend, locales, hosts, the ruler) or add
     a new one? New state, files, tables, settings or network calls? What would the next person change
     to extend it? Still standard library only, still macOS · Linux · Windows? -->

## Risks and rollback
<!-- What could break, for whom, and how would we notice? How is it undone — revert, a setting, a
     migration back? Does it write to the user's data? -->

## Checks
<!-- What you ran and what it said. Tick what applies. -->
- [ ] `python3 tests/clean_room.py` passes
- [ ] New behaviour has a check that can fail (a control case that must be red)
- [ ] User-facing text goes through `brain/locales/`, in all six languages
- [ ] Measurement code (`RULER_MODULES`) changed → `CODE_GENERATION` bumped
