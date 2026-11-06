"""Build the release archive a host app pins — `local-brain-<version>.tar` — and print its sha256.

    python .github/scripts/release_archive.py            # from the current commit, into dist/
    python .github/scripts/release_archive.py v0.1.1     # from a tag

The archive is `git archive` of a commit: the same commit always gives the same bytes, so a host
(Cosmos `bundle/manifest.py`) can pin one sha256 and check it at build time. The version inside the
archive (`brain.__version__`) must equal the one in pyproject.toml — `tests/verify_host_contract.py`
holds that.
"""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def version_at(ref: str) -> str:
    text = subprocess.run(["git", "show", "%s:pyproject.toml" % ref], cwd=ROOT, capture_output=True, encoding="utf-8",
                          text=True, check=True).stdout
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def main(argv) -> int:
    ref = argv[1] if len(argv) > 1 else "HEAD"
    version = version_at(ref)
    commit = subprocess.run(["git", "rev-parse", ref + "^{commit}"], cwd=ROOT, capture_output=True, encoding="utf-8",
                            text=True, check=True).stdout.strip()
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "local-brain-%s.tar" % version)
    subprocess.run(["git", "archive", "--format=tar", "-o", out, commit], cwd=ROOT, check=True)
    h = hashlib.sha256()
    with open(out, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    print("version %s\ncommit %s\nfile %s\nsha256 %s" % (version, commit, out, h.hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
