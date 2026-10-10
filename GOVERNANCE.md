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

**A role lives in two places, and both are needed.** The roster says whose approval the rules count;
the repository's permissions on GitHub say whose approval GitHub accepts at all.

| Role | GitHub repository role | Why that one |
|---|---|---|
| Area owner | Write | a code owner's approval only counts from someone with write access |
| Maintainer | Maintain | approve, queue merges, manage labels and releases — without changing settings or bypassing rules |
| Repository admin | Admin | settings, rulesets and the bypass; kept to the repository's owners |

### Adding a maintainer or an area owner — how

1. **Propose it in a pull request.** Edit `.github/governance.json` — add the login to `maintainers`, or
   to an area's `owners` — and run `python3 .github/scripts/pr_report.py --codeowners --write`. Title:
   `governance: add @login as maintainer` (or `… as owner of storage`). It is a high-impact change.
2. **Give them the GitHub role** from the table: Settings → Collaborators and teams → Add people, or
   `gh api -X PUT repos/honeyground-org/local-brain/collaborators/LOGIN -f permission=maintain`
   (`push` for an area owner).
3. **They accept the invitation.** Until they do, GitHub ignores them as a code owner — and the pull
   request does not say so.
4. **Merge it** with the existing maintainers' approval (while the roster is short, with a
   `Bypass: roster` record — see below).
5. **The merge audit confirms it**: after the merge it asks GitHub whether it accepts every owner in
   CODEOWNERS, and fails when someone has no write access or has not accepted yet.

From then on GitHub asks them to review their areas automatically and the review gate counts their
approvals. The rules themselves do not change as the roster grows. Removing someone is the same pull
request in reverse, then lowering or removing their GitHub role.

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
pass (clean room on Linux, macOS and Windows, live databases, databases in Docker, quality, release gates, PR rules,
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
- **Admin bypass** is for two cases only, recorded in a fixed form — see the next section.

## Admin bypass — how

A repository admin can merge a pull request whose approvals are incomplete. That power is used in two
cases only, each recorded on the pull request in a fixed form that the merge audit checks:

| Record | Valid when |
|---|---|
| `Bypass: roster` | everyone on the roster who could review (everyone but the author) has approved, and that is still fewer than the rule needs. It covers the gap — never a reviewer who has not looked yet. |
| `Bypass: security` | an urgent security fix that cannot wait. A normal review is still owed after the merge. |

Never to get past a red check other than the review gate, and never because reviewers are slow.

1. Every required check is green except `review gate`, and the PR report shows no ❌.
2. Review the change as you would anyone's — for a high-impact change, the five questions above.
3. Comment on the pull request with a line `Bypass: roster` or `Bypass: security` and one sentence on
   why (for example: *the only maintainer is the author; every required check is green*).
4. Merge: in the merge box tick *Merge without waiting for requirements to be met (bypass rules)* and
   squash-merge, or run `gh pr merge <number> --squash --admin`.
5. The **merge audit** (`.github/workflows/merge-audit.yml`) runs on every push to `main`. It passes when
   the pull request met the review rule or carries a valid record; otherwise it fails and comments on
   the pull request. GitHub also logs every bypass (Settings → Rules → Insights).

**It retires itself.** Because `roster` is valid only when everyone eligible has approved, it narrows as
people join: with a second maintainer, a high-impact change needs their approval and the bypass covers
only the missing one; with three, it is no longer possible and every change is reviewed in full. Nothing
has to be edited when that happens.

## Releases

Maintainers tag releases from `main` (`v0.x.y` while the interfaces settle). Release notes are generated
from the area labels the PR report sets (`.github/release.yml`).

## Changing this document

Edits to GOVERNANCE.md, `.github/governance.json` or anything under `.github/` are high impact and follow
the process above.
