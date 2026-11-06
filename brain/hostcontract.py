"""The surface a host app embeds — ★one number, checked on both sides★ (2026-11-06).

Cosmos ships a pinned local-brain and also attaches to one that is already installed (the person may have
installed local-brain first, or updated it since). Either way it calls a handful of functions in-process.
If one of them changes shape, the host breaks quietly: recall returns nothing, notes stop syncing.

So the functions a host may call are listed ★here, once★, with the keyword arguments it passes:

  · `HOST_API` — bump it ★only★ when an entry below changes incompatibly (removed, renamed, a parameter
    dropped or its meaning changed). Adding an entry or an optional parameter is not a bump.
  · `tests/verify_host_contract.py` fails if the code stops matching `SURFACE` — so a change that would
    break a host cannot merge without either keeping the surface or bumping `HOST_API` on purpose.
  · A host reads `HOST_API` (and `brain.__version__`) ★before★ calling anything and refuses an API it
    does not know, instead of guessing.
"""
import importlib
import inspect
from typing import List

HOST_API = 1

# (module, attribute, keyword parameters the host passes)
SURFACE = [
    ("brain.store", "brain_home", ()),
    ("brain.store", "connect", ("readonly",)),
    ("brain.store", "load_config", ("tolerant",)),
    ("brain.store", "add_source", ()),
    ("brain.server", "call_tool", ("_measure",)),          # recall with format=json, uncounted
    ("brain.embedruntime", "ready", ("timeout",)),
    ("brain.embedruntime", "step_env", ()),
    ("brain.install", "main", ()),                         # `python -m brain.install --embed embeddinggemma`
    # After writing notes into a source, a host with no hooks wakes the worker so they are indexed and
    # embedded. It starts `brain.deferred` with the runtime's own Python: inside a bundled host
    # `sys.executable` is the host, not Python, so `deferred.kick()` cannot be used there.
    ("brain.embedruntime", "venv_python", ()),
    ("brain.launch", "argv", ("python",)),
    ("brain.deferred", "main", ()),                        # run as `brain.deferred`; holds its own lease
]


def missing() -> List[str]:
    """What in `SURFACE` the code no longer offers — empty when a host can rely on it."""
    out = []
    for mod, attr, params in SURFACE:
        try:
            fn = getattr(importlib.import_module(mod), attr)
        except Exception as exc:                            # noqa: BLE001 — reported, not raised
            out.append("%s.%s: %s" % (mod, attr, type(exc).__name__))
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        accepts_any = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
        for name in params:
            if name not in sig.parameters and not accepts_any:
                out.append("%s.%s: no parameter %r" % (mod, attr, name))
    return out
