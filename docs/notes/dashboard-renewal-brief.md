# The pages this project renders — a brief for whoever renews them

**Written:** 2026-09-22 · every number below was measured that day on the live output, not estimated.
**Audience:** a session picking up a visual renewal of these screens. Read this before opening the files.

---

## 1. What exists, and how to see it right now

| page | produced by | size | how to make it |
|---|---|---|---|
| **Dashboard** (default) | `brain/dashview.py` (699 lines) | 304.7 KB | `brain dashboard` → `~/.claude/brain/dashboard.html`, opens in a browser |
| **Dashboard (classic)** | `brain/dashboard.py` `render()` (737 lines) | 273.2 KB | `brain dashboard --classic` |
| **Connection graph** | `brain/graphview.py` (353 lines) | 239 KB of SVG | embedded inside both dashboards |
| **Setup page** | `docs/build_setup_page.py` → `docs/brain-setup.html` | 17 KB | `python3 docs/build_setup_page.py` |

`--no-open` writes the file without launching a browser; `--out <path>` puts it somewhere else.
(`docs/worktree-hook-rev-b.html` belongs to a different project and is excluded from the public
snapshot — ignore it.)

---

## 2. How the HTML is made

**One Python function returns one string.** No template engine, no build step, no bundler, no
framework, no CDN. `render(payload) -> str`, written to a file, opened with `file://`.

- **CSS** is a single `CSS = """…"""` constant inlined into `<style>`. Custom properties on `:root`
  (`--ink --dim --line --bg --card --g --a --r --grey --blue`).
- **Charts are hand-written SVG functions**, one per mark: `radar` · `ring` · `logbars` · `donut` ·
  `treemap` · `growth` · `lights`. Each takes numbers and returns an SVG string. There is no chart
  library anywhere in the project.
- **Escaping is explicit** — `esc()` on every value that reaches the page. There is no auto-escaping
  layer to fall back on, so a renewal that introduces a new interpolation must call it.
- **The graph's layout is computed in Python at generation time**, never by physics in the browser:
  it paints instantly, never jitters, and looks identical every run.

Measured on the live page: **298,127 characters · 6 `<svg>` · 0 `<table>` · 1 `<script>` ·
0 external requests.**

---

## 3. What each screen was built to do

### Dashboard — *"is there anything to fix right now?"*
Not a screen that shows numbers. The verdict is at the top, the grounds under it, and **every item
ends with "so what should I do"**. A box with a number and no next action has no reason to be there.
Diagnostics carry one of four states — good · warning · serious · critical.

### The 2026-09-02 rewrite — *"graphs, not text"*
The user's instruction was blunt: *"The dashboard is bad. Show it as graphs, not text. Add real
animation. And let me switch language."* The old renderer put **51,988 visible characters, 4 tables
and 49 rows** on screen — built to be read, and the reasoning ate the screen. The new one keeps the
same numbers and draws them.

⛔ **Read the current number correctly before you judge it.** The live page has 46,384 visible
characters, which looks like the rewrite barely helped. It is not: **98% of that text (45,341 chars)
is inside the one graph SVG** — they are the node labels, i.e. data. The actual UI chrome is
**1,043 characters**. Any "reduce the text" work should start from 1,043, not 46,384.

### Connection graph — the brain's shape, and one specific defect
It answers two questions in one picture: is the memory graph clustered or scattered and where are
the hubs; and **which memories are linked to nothing** (set apart in an outer band).

---

## 4. ⛔ Invariants — a renewal may not break these

Each one exists because something broke. Keep the rule; the visual expression of it is yours.

1. **Zero dependencies, and the page never phones out.** Every chart is hand-written SVG precisely so
   the page works offline. A CDN link, a web font, a chart library, an analytics pixel — none of
   these may appear. *(Measured today: 0 external requests. Keep it at 0.)*
2. **It is a local file and the text never leaves.** The screen shows memory names and summaries,
   which here include production credentials, client incidents and internal revenue structure. "Text
   does not leave this machine" is a principle of the whole system; a dashboard that breaks it makes
   it not a principle.
3. **`not measured` is never drawn as zero.** Zero is a claim, absence is not. Unmeasured axes are
   hatched, not filled; `None` is grey, which is not a bad score.
4. **Colour never carries meaning alone.** An icon and a word travel with it — colour blindness,
   monochrome print, forced-colour mode. Colour means state (green met · amber short · red broken ·
   grey not measured), never decoration.
5. **Entrance animation only.** Values grow once, on first paint. ⛔ Nothing loops forever: a
   dashboard left open on a laptop must not burn the battery. `prefers-reduced-motion` is honoured
   *and* there is a switch in the header whose choice persists in `localStorage`.
6. **The graph uses three kinds + "other".** A node-link graph can put any two nodes side by side, so
   the palette was verified all-pairs; a fourth colour drops dark-mode CVD ΔE to 1.9 (measured). When
   colour runs short the rule is to fold into "other", never to invent one.
7. **Layout is computed at generation time.** No browser-side simulation.
8. **A memory's name and kind are data, not UI.** They are never translated. The i18n check enforces
   this by counting Hangul syllables only — an ASCII memory name can never be caught, a Korean UI
   string always is.

### The i18n mechanism — the part most likely to be broken by accident

The page ships **all six languages inside the single static file** and switches client-side.

- A translated node is `<span data-t="key" data-v='{…}'>` — **the values ride along as JSON**,
  because an earlier version let the switcher write `textContent = catalogue[key]` and the header
  literally read `"{at} created"` after the first switch.
- When the placeholder *value* is itself translatable, ship the key: `data-keys` + `data-slot`.
  Otherwise that slot freezes at the server's language.
- ⛔ **`UI_PREFIX` in `dashview.py` must list every key prefix the client needs.** Dropping `bench.`
  and `health.` once left row names in English on the Japanese screen — the catalogue had the
  translation, the client never received it. **Add a key prefix, add it to that tuple.**
- The check is `tests/verify_dashboard_i18n.py`, and it scans the **rendered page**, not the label
  list — because "translating a screen is not translating labels": an audit once found 761 untranslated
  pieces coming from five different producers, including the graph carrying its own legend text.

---

## 5. Free to change — treat nothing here as sacred

Layout, spacing, type scale, card shapes, the chart *forms* (a radar could become something better),
section order, the header, the language switcher's look, the colour ramp — provided §4 survives. The
current visual language (slate/`#0f172a` ink, 16px radius cards, 1120px max width, uppercase 12px
section headers, system font stack) is a default, not a decision anyone defended.

---

## 6. Where a renewal is most wanted — known weak spots, measured

| # | problem | evidence |
|---|---|---|
| 1 | **The new screen has no dark mode.** The classic one does. | `prefers-color-scheme` appears **1×** in the classic page and **0×** in the default page; `graphview.css_vars(dark=True)` exists and only the classic renderer calls it. This is a regression the rewrite introduced. |
| 2 | **Two renderers to keep in sync.** `dashview.render` (default) and `dashboard.render` (`--classic`) draw the same payload twice. | The classic path was deliberately kept as a fallback "for when the new screen breaks", and has been diverging since 2026-09-02. Decide whether it lives; if it does, it needs the same rules applied. |
| 3 | **One SVG is 239 KB of the 300 KB page** — ~750 node labels. | If the page ever feels heavy, this is the whole of it. Options nobody has tested: label only hubs, cull leaves at low zoom, or split the graph onto its own page. |
| 4 | **`details_html` was emptied, not redesigned.** | The old comparison table was removed because it was Korean prose on a six-language screen (600 untranslated chars). The numbers still exist; the screen simply lost that view. A renewal could bring it back properly. |
| 5 | **`validate_palette.js` is referenced but is not in this repository.** | `graphview.py` cites `validate_palette.js --pairs all` as the authority for its colour choices. The rule survives as a constant; the tool that proved it does not ship here. Re-deriving a palette means re-establishing that check. |

---

## 7. How to verify a change

```bash
python3 tests/verify_dashboard_i18n.py   # ⛔ the one that matters — scans the RENDERED page in all 6 languages
python3 tests/verify_i18n.py             # catalogue integrity: missing keys, placeholder mismatches
python3 tests/verify_english_only.py     # shipping files carry no Korean (baseline: 127 intentional lines)
brain dashboard --no-open && open ~/.claude/brain/dashboard.html
```

House rules that apply to any change here: **zero runtime dependencies**, Python **3.8+**, shipping
files are **English-only**, and every check states its own pass criteria and runs a control group
before trusting its result. A probe that cannot fail is not a probe.

---

## 8. Reading order

1. `brain/dashview.py` — the docstring is the design brief for the current screen; then `CSS`, then
   the chart functions, then `render()`.
2. `brain/graphview.py` — the docstring's "three design decisions" explains the graph's constraints.
3. `brain/dashboard.py` — `payload()` is the data contract both renderers consume; `write()` is the
   entry point and says why the classic screen still exists.
