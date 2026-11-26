# GitHub Issue → Pull Requests workflow (`issue-flow`) — Design

Date: 2026-11-26 · Status: draft for approval

## 1. Intent

A product manager writes a feature as a **GitHub issue** labelled `coding-agent` (the "Epic"). Today,
turning that into reviewed, tested code is manual and approvals happen ad hoc. We want one repeatable
Spec Kit workflow — the same shape as Spec Kit's built-in `speckit` workflow (`specify → review → plan →
review → tasks → implement`) — that takes such an issue and drives it to **one pull request per Story**,
with a named human role approving **on GitHub** between every stage, and an automatic test-and-fix loop
before QA sees anything.

It must be reusable across projects, so it ships through our internal catalog
(`https://github.com/integrationfirst/speckit-catalog`).

## 2. Goals, non-goals, anti-goals

**Goals**
- Input: a GitHub issue number. Only issues labelled `coding-agent` are accepted.
- Output: one PR per Story; each Story is a sub-issue of the Epic issue and is closed by its PR.
- Every stage transition waits for an approval made **on GitHub** by someone holding the right role.
- After implementation, run the project's tests (UI, UX, API, …); on any failure, loop back to
  implementation to fix, until all pass (bounded), before QA approval.
- Resumable: a run can sit paused for days and continue with `specify workflow resume`.
- Long review documents are published to **Confluence** so non-developers can read them.
- Generic: per-project settings (repo, approvers, test commands) live in a config file.

**Non-goals (v1)**
- Jira. Removed from this flow entirely.
- Running unattended on a server/CI (planned later; design must not block it — see §9).
- Closing the Epic issue. A person closes it after the Story PRs merge.
- Merging PRs. Choosing test frameworks.

**Anti-goals (would be a failure even if it "works")**
- Any stage advancing without an approval from an authorised person, or on a stale approval given
  for an earlier version of the artifact.
- Acting on an issue that does not carry the `coding-agent` label.
- An endless fix loop silently burning time/tokens.
- Sub-issues created before the Team Lead approved the breakdown.
- Secrets committed to a project repo.

## 3. Constraints (verified 2026-11-26)

- Spec Kit CLI 1.4.4 workflow step types: `command`, `gate`, `shell`, `if`, `switch`, `while`,
  `do-while` (default `max_iterations` 10), `fan-out`/`fan-in`, `prompt`, `workflow`, `github`
  (only `add-label`).
- `command` steps dispatch `claude -p /<command>` headless. Headless runs cannot answer permission
  prompts, so the project's `.claude/settings.json` must allow `gh`, `git`, the Atlassian MCP
  Confluence tools and the configured test commands.
- `gate` prompts in a TTY, otherwise **pauses**; `specify workflow resume` continues. Gates do not know
  who approved — hence approvals by GitHub comment / review.
- GitHub access is via the **`gh` CLI** (logged in as the developer). The GitHub MCP server is not
  usable at the moment (auth error), so the design does not depend on it.
- GitHub supports **sub-issues** (`parent`, `subIssues` on Issue via GraphQL / `gh api`).
- Confluence: `strongtie.atlassian.net`, via the Atlassian MCP already configured in Claude Code.
- Catalog URLs must be HTTPS; extensions install with `specify extension add <id>` from the internal
  catalog; workflows have their own catalog (`specify workflow catalog`, `specify workflow add`).
- The vetted `jira` extension is unrelated to this flow; projects using `issue-flow` should not have its
  `after_tasks` hook enabled.

## 4. Approaches considered

| | Approach | Trade-off | Decision |
|---|---|---|---|
| **A** | Spec Kit **workflow** (`issue-to-pr`) + internal **extension** (`issue-flow`) providing the GitHub/Confluence/test/PR commands | Native pause/resume, visible run status, automation later = schedule `resume`. Bound to a young workflow engine. | **Chosen** (user) |
| B | One Claude Code command `/issue-to-pr` with its own state file | Faster to start; no native resume or run status; harder to automate. | Rejected |
| C | Extend community `jira-lifecycle` (skj) | Jira-only, own token, one-Story model. Moot after the move to GitHub Issues. | Rejected |

## 5. Decisions made with the user

- **Source of work: GitHub Issues** (not Jira). Only issues labelled **`coding-agent`**.
- **Approvals happen on GitHub**, by role:

  | Stage | Approver role(s) |
  |---|---|
  | Spec | Product Owner **and** Business Analyst **and** Solution Architect (all required) |
  | Plan | Team Lead |
  | Tasks / Story breakdown | Team Lead |
  | Tests passed (per Story, before PR) | QA Lead |
  | Pull request (per Story) | Team Lead |

- **One PR per Story.** Only the "User Story" phases of `tasks.md` become Stories; Setup and
  Foundational fold into the first Story, Polish into the last. The Team Lead approves that breakdown.
- **Rejections at QA or PR**: back into the fix loop on the same branch with the rejection / review
  comments as input, all tests re-run, then **QA approves again**; a PR is updated in place. The next
  Story does not start until the current PR is approved.
- **Test loop**: failures loop back to implementation until all pass, then QA, then PR.
- **Confluence**: review documents are published in the user's **personal Confluence space**.
- **Runs on a developer machine first**; automation later.
- **Approach A.**

## 6. Design

### 6.1 Flow

```
input: issue=#42  (must carry label coding-agent)
 ├─ pull-issue ........... read issue (title, body, AC, comments) → specs/<NNN-slug>/issue.md
 ├─ STAGE spec  [PO+BA+SA] speckit.specify(issue.md) → publish → wait-approval ─┐ reject → regenerate with feedback
 ├─ STAGE plan  [TL]       speckit.plan              → publish → wait-approval ─┤
 ├─ STAGE tasks [TL]       speckit.tasks + Story breakdown → publish → wait ────┘
 ├─ create-stories ....... sub-issues under #42 (label coding-agent) → issue-mapping.json
 └─ for each Story, in order:
      ├─ branch  <story#>-<slug>   (Story 1 from the spec feature branch; Story N from Story N-1)
      ├─ do-while (tests failing AND round ≤ max_fix_rounds):
      │     implement (round 1: Story tasks; later: fix using test report / rejection feedback)
      │     run-tests → test-report.md
      ├─ if still failing → gate pauses for a human; resume re-enters the loop
      ├─ STAGE qa [QA Lead]    publish test report on the Story issue → wait-approval
      │     reject → back to the fix loop (feedback = QA comment), then QA again
      ├─ open-pr ............. push, gh pr create ("Closes #story"), Story labelled in-review
      └─ STAGE pr [TL]         wait for an approving PR review from a Team Lead login
            changes requested → fix loop (feedback = review comments) → tests → QA again → push
```

### 6.2 The approval stage (reusable sub-workflow)

Each STAGE is the same pattern, implemented once as a nested `workflow` step (`issue-flow-stage`),
parameterised by stage, artifact(s), issue number and role(s):

1. **publish** (`speckit.issue-flow.publish`): posts a review-request comment on the issue (Epic issue
   for spec/plan/tasks; Story issue for qa). The comment contains the stage, a **request id**
   (`IF-<stage>-r<N>`), a short summary, the artifact link, `@mentions` of the approvers, and the
   instruction `/approve IF-<stage>-r<N>` or `/reject IF-<stage>-r<N> <reason>`.
   Artifacts (spec/plan/tasks breakdown) are published as **Confluence pages** in the configured space
   (the user's personal space by default), one page per artifact, updated in place on each revision,
   with a link in the comment. The test report (qa) is posted inline in the comment.
   The request also records a **content hash** of the artifact.
2. **check** (`speckit.issue-flow.check-approval`): reads issue comments newer than the request; keeps
   only `/approve|/reject <request-id>` whose author's GitHub login holds the stage's role; writes
   `specs/<feature>/.issue-flow/<stage>.json` = `{status: pending|approved|rejected, by, at, reason,
   hash}`. Multi-role stages need one approval per role; any authorised reject wins. If the artifact's
   current hash differs from the request's, status is `stale` → republish as `r<N+1>`.
3. **decide**:
   - `approved` → continue.
   - `pending` → a `gate` pauses the run with "waiting for @login (role) on #42 — link". The developer
     runs `specify workflow resume <run>`; the loop returns to **check**.
   - `rejected` → the stage's generator re-runs with the reason as feedback, then republishes `r<N+1>`.
     An earlier approval never satisfies a later request id.

The **pr** stage uses the PR's reviews instead of comments: approved when the latest review from a
login in `team_lead` is `APPROVED`; `CHANGES_REQUESTED` from such a login is a rejection whose
feedback is the review body and its inline comments.

Engine mapping: `do-while (status != approved)` around [check → `switch` on status → gate | regenerate
+ publish]. How the switch reads `status` (shell step output vs command `capture`) is left to the
builder; a spike must confirm it before anything else is built.

### 6.3 Roles and identity

Per-project config lists approvers per role as **GitHub logins** (optionally a GitHub team slug).
A comment or review counts only if its author is in the role. The developer running the workflow does
not count unless listed for that role. `setup` verifies every login exists and can see the repo.

### 6.4 Story breakdown and GitHub writes

- Stories = `## Phase N: User Story …` phases of `tasks.md`; Setup/Foundational tasks join Story 1,
  Polish tasks join the last Story. Each Story issue body holds its task checklist (`- [ ] T0xx …`).
- The tasks-stage publication shows the proposed Stories and their tasks; nothing is created until the
  Team Lead approves that exact request id.
- `create-stories` creates each Story as an issue with label `coding-agent`, links it as a
  **sub-issue** of the Epic issue, and writes `specs/<feature>/issue-mapping.json`
  (`{epic, stories:[{number, title, phase, tasks:[ids]}]}`). Idempotent: re-runs skip existing ones.
- Progress labels (configurable): `in-progress` when its branch starts, `in-review` when its PR opens.
  Task checkboxes in the Story body are ticked as tasks complete. Story issues close via the PR's
  `Closes #n` on merge. The Epic is never closed by the workflow.

### 6.5 Per-Story implementation, tests, PR

- **Branches** (my call): the spec/plan/tasks live on the feature branch `speckit.specify` creates.
  Story 1 branches from it (so Story 1's PR also carries the spec documents); Story N branches from
  Story N-1's branch and its PR targets that branch (stacked PRs; GitHub retargets when the lower PR
  merges). Branch name `<story#>-<slug>`.
- **Implement**: `speckit.implement` scoped to the Story's task ids; marks tasks `[x]` in `tasks.md`.
- **Tests** (`speckit.issue-flow.run-tests`): runs every configured suite, captures exit code and output
  tail, writes `specs/<feature>/stories/<story#>/test-report.md`. All `required` suites must pass.
- **Fix loop**: up to `max_fix_rounds` (default 5); on exhaustion a gate pauses ("fix manually, or
  approve another round of N"); resume continues.
- **QA**: QA Lead approves the report on the Story issue; a reject feeds back into the fix loop.
- **PR** (`speckit.issue-flow.open-pr`): push, `gh pr create` with body from the Story, tasks, AC
  traceability and test report, `Closes #story`. Later fixes push to the same PR.
- **PR review**: Team Lead approval as in §6.2; changes requested → fix loop → tests → QA → push.
  Only after approval does the next Story start. The workflow never merges.

### 6.6 Deliverables

In `integrationfirst/speckit-catalog`:
- `extensions/issue-flow/` — `extension.yml`, `issue-flow-config.template.yml`, commands:
  `setup`, `pull-issue`, `publish`, `check-approval`, `create-stories`, `run-tests`, `open-pr`,
  `status` (read-only: where is this issue, who are we waiting for). Released as a tagged zip
  (`issue-flow-v<version>`).
- `workflows/issue-to-pr/workflow.yml` + `workflows/issue-flow-stage/workflow.yml`.
- `catalog.json` entry for `issue-flow`; `workflows/catalog.json` for the workflows; README updates
  (setup in a new project, permissions snippet).

Per project (generated by `setup`): `.specify/extensions/issue-flow/issue-flow-config.yml`:

```yaml
github:
  repo: owner/name            # issues + PRs
  trigger_label: coding-agent
  progress_labels: {in_progress: in-progress, in_review: in-review}
confluence:
  site: strongtie.atlassian.net
  space_key: "~<personal space key>"   # setup resolves the user's personal space
  parent_page_id: ""                    # optional
approvers:                              # GitHub logins (or org/team slugs)
  product_owner: []
  business_analyst: []
  solution_architect: []
  team_lead: []
  qa_lead: []
stages:
  spec:  {roles: [product_owner, business_analyst, solution_architect], require: all}
  plan:  {roles: [team_lead]}
  tasks: {roles: [team_lead]}
  qa:    {roles: [qa_lead]}
  pr:    {roles: [team_lead], via: github_review}
tests:
  max_fix_rounds: 5
  suites:
    - {name: unit, type: unit, command: "npm test", required: true}
    - {name: api,  type: api,  command: "newman run postman/collection.json", required: true}
    - {name: ui,   type: ui,   command: "npx playwright test", required: true}
git: {default_branch: main, stacked: true}
```

## 7. Error handling

- Any `gh`, git or Confluence failure fails the step; the run stops and `resume` retries it. Commands
  are idempotent (mapping file, request ids, "comment for rN already posted" checks), so retries do not
  duplicate GitHub or Confluence content.
- Missing label, an issue without acceptance criteria, an unknown approver login: stop with a clear
  message rather than guess.
- Headless permission denial is reported with the exact tool to allow in `.claude/settings.json`.

## 8. Testing the workflow itself

- Dry-run mode (`dry_run: true` input): every GitHub/Confluence write is printed instead of performed.
- End-to-end run against `integrationfirst/speckit-sample` (public; test issues, comments and PRs are
  publicly visible) and the user's personal Confluence space, with a small
  `coding-agent` issue, exercising: reject-then-approve on spec, stale approval after an edit, an
  unauthorised `/approve` ignored, an issue without the label refused, a forced test failure fixed in
  the loop, loop exhaustion gate, QA reject, PR changes-requested, two stacked Story PRs.

## 9. Later: automation

Only the caller of `specify workflow resume` changes: a scheduled job (or a GitHub Action triggered by
`issue_comment` / `pull_request_review`) instead of the developer. That needs a bot token and a headless
agent; state already lives in files and on GitHub, so no redesign.

## 10. Left to the builder

How `switch`/`do-while` read the check result (spike first, §6.2); command prompt wording; PR body
template; slugging; how `speckit.implement` is scoped to one Story's task ids.
