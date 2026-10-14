"""★How to start one of brain's modules so that it runs the installed brain★ (2026-10-14).

`python -m brain.<module>` puts the current directory first on `sys.path`. Hosts start the hook, the guard and
the MCP server in the person's project folder, so a project with a `brain/` package of its own — or a working
copy of this repository — had ★its★ code run in their place, on every prompt (§bin/_run.py has the incident).
Every place that writes or runs such a command asks here:

- a repository checkout → `<python> <root>/bin/_run.py brain.<module>` (puts the repository first, drops the
  current directory);
- anything else (an installed package, a copied package) → `<python> -c <bootstrap> <root> brain.<module>`,
  which does the same in one line. (`-I -m` was considered: it drops the current directory but ignores
  PYTHONPATH too, so a copied package is not found — `verify_server_refresh` runs exactly that layout.)
"""
from __future__ import annotations

import os
import sys
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "bin", "_run.py")
BOOT = ("import runpy,sys;r=sys.argv.pop(1);"
        "sys.path[:]=[r]+[p for p in sys.path if p not in ('','.')];"
        "runpy.run_module(sys.argv.pop(1),run_name='__main__',alter_sys=True)")


def argv(module: str, python: str = "") -> List[str]:
    py = python or sys.executable
    if os.path.isfile(RUNNER):
        return [py, RUNNER, module]
    return [py, "-c", BOOT, ROOT, module]


def command(module: str, quote=lambda s: s) -> str:
    """The same as one line, each part passed through `quote` (the installer's quoting for spaces)."""
    return " ".join(quote(p) for p in argv(module))
