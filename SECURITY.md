# Security

## Reporting a problem

**Do not open a public issue.** Report it privately through GitHub:
[Security → Report a vulnerability](https://github.com/honeyground-org/local-brain/security/advisories/new).
Only the maintainers see it.

Please include what an attacker could do, the steps to reproduce it, and the version or commit. You will
get an acknowledgement within three working days and an assessment within ten. We fix, publish an
advisory, and credit you unless you prefer otherwise.

## Supported versions

The latest release and `main`.

## What matters most here

brain reads people's private notes and runs inside their coding tools, so these are in scope above all:

- **Anything that sends data off the machine** without the user having chosen an engine or a database
  for it (`brain/engines.py`, `brain/proxy.py`, `brain/stores.py`), or that bypasses `brain/privacy.py`.
- **Secrets**: keys written where others can read them, printed, logged, passed on a command line, or
  committed.
- **Code execution** through the hooks, the MCP server, the installer, or the files brain indexes.
- **Local services**: the Docker-run databases must listen on `127.0.0.1` only.
- **This repository's automation**: a pull request that could make a workflow running with write
  permissions (`pull_request_target`) execute its code or read a secret.
