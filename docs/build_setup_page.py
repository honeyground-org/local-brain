"""Rebuild docs/brain-setup.html from docs/brain-setup-prompt.md.

⛔ Why this script exists: the prompt was ★copied★ into the HTML page. The same
   text in two places is the failure this repository keeps meeting — one copy gets
   fixed and the other quietly drifts. The page is now generated: the Markdown is
   the source, the HTML is the artefact.

Only the block between <pre id="body"> and </pre> is replaced; the styling and the
surrounding sections stay as they are.

    python3 docs/build_setup_page.py [--check]

--check exits non-zero if the page is out of date instead of rewriting it, so it
can be used as a test.
"""
from __future__ import annotations

import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "brain-setup-prompt.md")
PAGE = os.path.join(HERE, "brain-setup.html")
OPEN, CLOSE = '<pre id="body">', "</pre>"


def rendered() -> str:
    md = open(SRC, encoding="utf-8").read().rstrip("\n")
    page = open(PAGE, encoding="utf-8").read()
    i = page.index(OPEN) + len(OPEN)
    j = page.index(CLOSE, i)
    return page[:i] + html.escape(md, quote=False) + "\n" + page[j:]


def main() -> int:
    out = rendered()
    current = open(PAGE, encoding="utf-8").read()
    if out == current:
        print("brain-setup.html is up to date")
        return 0
    if "--check" in sys.argv:
        print("⛔ brain-setup.html is stale — run: python3 docs/build_setup_page.py")
        return 1
    open(PAGE, "w", encoding="utf-8").write(out)
    print(f"brain-setup.html rebuilt from {os.path.basename(SRC)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
