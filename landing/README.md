# Landing page (base)

`index.html` is the self-contained introduction page for local-brain: one file, no external scripts, fonts or
trackers. It is the starting point for the project landing site.

- Languages: English and Korean in the same file. The visitor can switch with the EN / 한국어 toggle, and the
  choice is remembered in `localStorage`. The default is the `data-lang` / `data-pin` attribute on `<html>`
  (`en` here; set both to `ko` for a Korean-first page).
- Open it directly in a browser, or serve the folder as a static site (for example GitHub Pages with `landing/`).
