# Governance

How local-brain is run: who decides, how a change is reviewed, and what an important change has to
show before it merges. How to *make* a change is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Roles

| Role | Who | Can | Is expected to |
|---|---|---|---|
| **Contributor** | anyone who opens an issue or a pull request | propose and discuss anything | follow CONTRIBUTING.md and the Code of Conduct |
| **Area owner** | named per area in `.github/governance.json` | approve as code owner for their area; be asked automatically | review changes in their area within a few working days; keep its checks honest |
| **Maintainer** | named in `.github/governance.json` → `maintainers` | approve anywhere; queue merges; manage releases, labels and settings | review high-impact changes; decide when there is no consensus; keep this document true |

**The roster is one file.** `.github/governance.json` lists the maintainers, the areas (their paths,
impact level and owners) and how many approvals each impact needs. `.github/CODEOWNERS` is generated
from it (`python3 .github/scripts/pr_report.py --codeowners --write`), and `tests/verify_pr_report.py`
fails when the two disagree or when a tracked file belongs to no area.

**Becoming an area owner.** Sustained, careful work in an area — changes that came with measurements and
checks that can fail, and useful reviews of other people's changes. A maintainer proposes it in a pull
request that edits the roster; it merges with the usual high-impact review.

**Becoming a maintainer.** Area owners who have reviewed across areas for some months. Proposed in a
pull request editing the roster; merges when every maintainer has approved or none has objected within
seven days.

**Stepping back.** Anyone can step back at any time. After six months without reviews, a maintainer or
area owner is moved to an *emeritus* list in the roster — thanked, no longer asked for reviews — and
can return by asking.

## Impact decides the review

The PR report classifies every pull request from the files it touches (`.github/governance.json` →
`areas`), and raises it to **high** when it changes measurement code (`RULER_MODULES`), removes a check,
or raises the English-only ratchet.

| Impact | Approvals | Also required |
|---|---|---|
| low | 1 | code owners of the areas touched |
| medium | 1 | code owners; Purpose, Effect, Risks and rollback, Checks in the description |
| high | 2, at least 1 maintainer | code owners; all five sections with measured numbers; the summary for reviewers |

What the branch ruleset on `main` enforces: changes arrive by pull request only; the required checks
pass (clean room on Linux and macOS, live databases, databases in Docker, release gates, PR rules,
review gate); one approval and the code owners; approvals are dismissed by a new push; review threads
are resolved; history is linear (squash merges); no force pushes or deletion; merges go through the
merge queue. The **review gate** adds what the ruleset cannot express — the second approval and the
maintainer for high-impact changes — and is re-evaluated on every review.

## Important changes

A change is important when it is high impact, or when it changes a principle in
[docs/ROADMAP.md](docs/ROADMAP.md), a public interface (command-line flags, the MCP tools, the hook's
output, config keys) or a stored format (the index schema, the ledger, `config.json`).

**1. Propose first.** Open a proposal issue: the problem, the change, and *how we will know it works* —
the number that should move and the check that would fail without the change. Agree on the measurement
before the code.

**2. Describe it so it can be judged quickly.** The pull request answers five questions, with evidence:

| Question | A good answer |
|---|---|
| **Purpose** — what problem, for whom? | names the user and the situation; links the proposal |
| **Effect** — what is different, measured? | before → after on the same corpus, measured the same way, and how to reproduce it |
| **Scalability and extensibility** — does it fit? | extends an existing seam (one class per backend, locales, hosts, the ruler) or argues for a new one; lists new state, settings and network calls; standard library only; macOS, Linux, Windows |
| **Risks and rollback** — what if it is wrong? | what could break and for whom, how we would notice, how it is undone |
| **Checks** — how do we know? | each new behaviour has a check with a control case that must be red; measurement code → `CODE_GENERATION` bumped |

**3. The automation summarises it for the reviewers.** The PR report states the impact and the reasons,
the areas and what is still missing. For a high-impact change it adds a **summary for reviewers**: each
of the five answers from the description, side by side with what the diff itself shows — the areas
touched, whether Effect gives numbers, new and removed files, imports outside the standard library,
measurement code and its generation, which tests changed. Several reviewers can see in one table where
the description and the change disagree. It is computed from the change alone and calls no paid
service: CI costs nothing (see below).

**4. Reviewers decide.** Each reviewer checks the five answers against the diff — *purpose matches the
diff · the effect is measured, not asserted · it extends an existing seam · the risk has a rollback ·
new checks can fail* — and approves, or requests changes with the reason. Two approvals including a
maintainer, and no open request for changes, merge it.

**5. The record stays.** The proposal, the description, the PR report and the reviews remain on the
pull request: why it was done, what it changed, and who agreed.

## CI costs nothing

Every check runs on GitHub's standard runners, which are free for public repositories, and no step calls
a paid API (an AI service, for example) or a larger, paid runner. `tests/verify_pr_report.py` reads the
workflows and fails when one would cost money, and the clean room passes no paid key to any check. Changing
this rule is a governance change.

## Decisions

- **Lazy consensus.** A proposal or pull request that has the approvals it needs and no objection
  proceeds. Silence is not a veto.
- **Objections carry reasons.** A request for changes names what would resolve it. It blocks until it is
  resolved or withdrawn.
- **No consensus.** If maintainers disagree after discussion, they vote in the pull request; a simple
  majority of maintainers decides, and the reasons are written down there.
- **Admin bypass** is for two cases only: the roster cannot yet supply the reviewers a rule asks for, or
  an urgent security fix. The pull request says which, and the bypass is recorded by GitHub.

## Releases

Maintainers tag releases from `main` (`v0.x.y` while the interfaces settle). Release notes are generated
from the area labels the PR report sets (`.github/release.yml`).

## Changing this document

Edits to GOVERNANCE.md, `.github/governance.json` or anything under `.github/` are high impact and follow
the process above.
