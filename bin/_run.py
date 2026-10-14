"""Run one of brain's modules ★from where brain is installed★ — never from the folder it was started in.

`python3 -m brain.hook` puts the current directory ahead of everything else on `sys.path`, PYTHONPATH
included. The hook, the guard and the MCP server are started by the host in the person's project folder, so
a folder holding any `brain/` package ran ★that folder's code★ instead (found 2026-10-14: a working copy's
code answered the live hook and wrote records against the live index). Any repository opened in a host with
brain installed could have done the same on every prompt.

A script's own folder (bin/), not the current one, starts `sys.path`; this puts the repository in front,
drops the current directory, and runs the module as `__main__`.

    python3 bin/_run.py brain.hook --host claude-code
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_cwd = os.getcwd()
sys.path[:] = [ROOT] + [p for p in sys.path if p not in ("", ".", _cwd, HERE)]
if len(sys.argv) < 2 or not sys.argv[1].startswith("brain."):
    sys.stderr.write("usage: python3 bin/_run.py brain.<module> [args…]\n")
    sys.exit(2)
module = sys.argv.pop(1)
runpy.run_module(module, run_name="__main__", alter_sys=True)
