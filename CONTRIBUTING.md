# Contributing to local-brain

Thank you for helping. This page is the whole path from an idea to a merged change: how to set up,
what every change must hold to, how to open a pull request, and what the automation and the reviewers
do with it. How decisions are made and who makes them is in [GOVERNANCE.md](GOVERNANCE.md).

## In one screen

```bash
git clone https://github.com/honeyground-org/local-brain && cd local-brain
git switch -c stores-retry-once                    # a branch per change
# … edit …
python3 tests/clean_room.py                        # every check, tracked files only, empty home
git commit -s -m "stores: retry a lost connection once"
python3 .github/scripts/pr_report.py --local --body my-pr.md    # the report a reviewer will see
git push -u origin stores-retry-once               # then open the pull request
```

Nothing to install: brain runs on the system `python3` (3.8 or newer) and its standard library.

## Before you start

- **Small fixes** (a bug, a typo, a clearer message): open the pull request directly.
- **Anything that changes behaviour** — retrieval, the threshold, storage, what leaves the machine, the
  behaviour layer, installing — open a [proposal](../../issues/new?template=feature_request.yml) first.
  It asks how we would *measure* that the change works; agreeing on that before the code saves
  everyone a round trip.
- Look for issues labelled `good first issue` if you want somewhere to start.

## What every change holds to

The principles are in [docs/ROADMAP.md](docs/ROADMAP.md#principles-every-change-follows). In practice:

| Rule | What it means for your change |
|---|---|
| **Measured, not assumed** | A change is done when it wins under the same conditions against what was there before. Put the numbers in the description. |
| **Checks that can fail** | A new behaviour comes with a check in `tests/verify_*.py` that includes a control case which *must* be red. A check that cannot fail proves nothing. |
| **Standard library only** | No third-party imports. Each storage backend speaks its service's HTTP API with `urllib`. The PR rules reject a new import outside the standard library. |
| **English in the repository** | Code, comments, docs, commit messages. Text a user reads goes through `brain/locales/` — all six languages, literal keys (`i18n.t("cli.x.y")`). |
| **Measurement code is versioned** | The modules in `brain/calibrate.py` → `RULER_MODULES` decide the threshold. Change one and bump `CODE_GENERATION` in the same pull request, or CI fails. |
| **Nothing tuned to one person** | Every number is measured on the user's own data or chosen by them — never copied from someone's notes. |
| **Test against a copy** | Point `BRAIN_HOME` and `BRAIN_CONFIG` at a temporary folder. A command run against your real config changes the brain you are using. |

## Running the checks

```bash
python3 tests/clean_room.py                    # all of them, the way CI runs them
python3 tests/clean_room.py verify_stores      # only some
PYTHONPATH=. python3 tests/verify_stores.py    # one, in your checkout
```

Exit code 77 means *skipped*: the check needs something a fresh machine does not have (your notes, a
database, Docker) and says what. CI supplies the databases and Docker, so those run there:

```bash
docker run -d -p 127.0.0.1:6333:6333 qdrant/qdrant:v1.19.2
NEO4J_PASSWORD=… python3 tests/clean_room.py --strict verify_stores_live
BRAIN_TEST_DOCKER=1 python3 tests/clean_room.py --strict verify_docker_stores
```

## Commits

- **Title:** `area: what changed`, imperative, lower case after the colon — `stores: retry a lost
  connection once`, `README: explain the clean room`. Pull requests are squash-merged, so the pull
  request title becomes the commit on `main`; it follows the same rule.
- **Sign-off:** every commit carries `Signed-off-by: Your Name <your email>` — `git commit -s` adds it.
  It certifies the [Developer Certificate of Origin](https://developercertificate.org/): you wrote the
  change or have the right to submit it under the project's licence (Apache-2.0). Forgot? `git rebase
  --signoff main` and force-push your branch.
- Your commits' author address is published with them. If you prefer not to publish your email, use
  your GitHub noreply address (GitHub → Settings → Emails).

## Opening a pull request

The template asks five questions. Which ones are required depends on the change's **impact**, which the
PR report works out from the files you touched:

| Impact | Typical change | Description needs | Approvals |
|---|---|---|---|
| low | docs, translations | Purpose · Checks | 1 |
| medium | the command line, pages, retrieval code outside the measurement modules, checks | Purpose · Effect · Risks and rollback · Checks | 1 + the code owners |
| high | measurement code, storage, privacy, the behaviour layer, installing, CI and governance, removing a check | all five, with numbers in Effect | 2, at least one maintainer + the code owners; a summary for reviewers in the PR report |

Write for a reviewer who has five minutes:

- **Purpose** — the problem, for whom, and the issue it fixes.
- **Effect** — what is different for a user, measured: before → after, on the same corpus, the same way.
- **Scalability and extensibility** — the seam it extends (or why it needs a new one), new state or
  settings, platforms.
- **Risks and rollback** — what could break, how we would notice, how to undo it.
- **Checks** — what you ran and what it said.

Open it as a **draft** while you are still working; the checks run, nobody is asked to review yet.

## What happens automatically

| Check | Runs on | What it does | When it is red |
|---|---|---|---|
| **clean room** (Linux, macOS; Windows shown) | every push | every `tests/verify_*.py` on the tracked files with an empty home, Python 3.8 – 3.13 | the log names the check; reproduce with `python3 tests/clean_room.py <name>` |
| **live databases** | every push | the same answers from Qdrant and Neo4j as from the local copy | see `tests/verify_stores_live.py` |
| **databases in Docker** | every push | data survives removing and recreating the containers | see `tests/verify_docker_stores.py` |
| **release gates** | every push | measurement code bumped, no secrets (gitleaks), no personal data, history included | the step says which file and line |
| **PR rules** | title, description, push | title format, description sections, sign-off, standard-library imports | the summary lists each problem |
| **review gate** | push, every review | enough of the right approvals for the impact | waits until they arrive — not something you fix |
| **merge audit** | every merge into `main` | it was reviewed per the rule, or carries a valid `Bypass:` record; GitHub accepts every code owner | comments on the pull request — for maintainers ([GOVERNANCE.md](GOVERNANCE.md#admin-bypass--how)) |
| **PR report** | title, description, push | one comment: impact and why, areas, what is missing, reviewers requested; labels. For high impact, a summary for reviewers: each of your five answers beside what the diff shows | — |

All of it runs on GitHub's free runners and calls no paid service — CI costs nothing, and
`tests/verify_pr_report.py` fails if a workflow ever adds a paid API, action or runner.

## Review and merge

1. Code owners of every area you touched are asked for a review automatically; for a high-impact change
   the whole roster is asked.
2. Reviewers answer the five questions in [GOVERNANCE.md](GOVERNANCE.md#important-changes). A request
   for changes blocks until it is resolved; push the fix and reply to the thread.
3. Pushing after an approval dismisses it — reviewers approve what will merge.
4. When everything is green, a maintainer adds the pull request to the **merge queue**. It re-runs the
   checks on top of the latest `main` and squash-merges. You do not need to keep your branch up to date
   by hand.

## Reporting bugs and security problems

- Bugs: the [bug report form](../../issues/new?template=bug_report.yml), with `brain status` output
  (remove anything private).
- Security problems: **never in a public issue** — see [SECURITY.md](SECURITY.md).

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
