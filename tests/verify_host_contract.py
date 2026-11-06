"""Host contract — ★what Cosmos calls in-process still exists, in the shape it calls it★ (2026-11-06).

Cosmos attaches to whatever local-brain is on the computer: the copy it ships, or one the person installed
first and keeps updating. It reads `brain.hostcontract.HOST_API` and `brain.__version__` before calling
anything. This check keeps those two honest:

  · every entry of `SURFACE` resolves and accepts the keyword arguments the host passes
  · `brain.__version__` equals pyproject.toml (the host shows and pins this number)
  · `HOST_API` is a positive integer

⛔ If you change one of the SURFACE functions incompatibly, this fails. Either keep the old shape, or bump
   `HOST_API` ★and★ update the host in the same release train — never silently.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain                                               # noqa: E402
from brain import hostcontract                             # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("✅" if cond else "❌", label, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    print("=" * 72 + "\nhost contract (what Cosmos calls)\n" + "=" * 72)
    gone = hostcontract.missing()
    check("every SURFACE entry exists with the parameters a host passes", not gone, "; ".join(gone))
    with open(os.path.join(ROOT, "brain", "deferred.py"), encoding="utf-8") as fh:
        check("brain.deferred runs as a module (a host starts it with the runtime's Python)",
              'if __name__ == "__main__":' in fh.read())
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        declared = re.search(r'^version = "([^"]+)"', fh.read(), re.M).group(1)
    check("brain.__version__ equals pyproject.toml", getattr(brain, "__version__", None) == declared,
          "%r vs %r" % (getattr(brain, "__version__", None), declared))
    check("HOST_API is a positive integer", isinstance(hostcontract.HOST_API, int) and hostcontract.HOST_API > 0)
    check("SURFACE is not empty", len(hostcontract.SURFACE) >= 5)
    print("\n%s" % ("FAIL: " + ", ".join(FAIL) if FAIL else "all passed"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
