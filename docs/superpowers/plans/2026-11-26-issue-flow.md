# issue-flow (GitHub issue → pull requests) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Spec Kit extension `issue-flow` plus workflows `issue-to-pr` / `issue-flow-stage` that take a `coding-agent` GitHub issue through spec → plan → tasks → per-Story implement/test/QA/PR, pausing for role-based GitHub approvals, published via the internal catalog.

**Architecture:** Deterministic work (GitHub I/O via `gh`, approval evaluation, story breakdown, test running, branches/PRs) lives in a small stdlib-only Python CLI `issue-flow` shipped inside the extension and called from workflow `shell` steps with `output_format: json`. Agent work (generating spec/plan/tasks, implementing/fixing, publishing to Confluence via the Atlassian MCP) is Spec Kit `command` steps (headless `claude -p`). The workflow engine provides loops, pauses (`gate`) and `specify workflow resume`.

**Tech Stack:** Spec Kit CLI 1.4.4 workflows/extensions · Python 3.11+ (stdlib + PyYAML via `uv run --with`) · `gh` CLI · Claude Code headless + Atlassian MCP (Confluence) · pytest (via `uv run --with pytest`).

**Spec:** `docs/superpowers/specs/2026-11-26-issue-to-pr-design.md`

## Global Constraints

- Repo for all code: `integrationfirst/speckit-catalog`; extension root `extensions/issue-flow/`; workflows `workflows/issue-to-pr/`, `workflows/issue-flow-stage/`.
- Trigger label: `coding-agent`. Issues without it are refused (exit code 2).
- Request ids: `IF-<stage>-r<N>`; stages: `spec`, `plan`, `tasks`, `qa`, `pr`.
- Approval comment grammar (whole first line of a comment, case-insensitive command): `/approve <request-id>` · `/reject <request-id> <reason…>`.
- Stage roles: spec = `product_owner`+`business_analyst`+`solution_architect` (all); plan = `team_lead`; tasks = `team_lead`; qa = `qa_lead`; pr = `team_lead` via GitHub review.
- `max_fix_rounds` default `5`. Progress labels default `in-progress`, `in-review`.
- Branch name `<story#>-<slug>`; slug = lowercase title, non-alphanumerics → `-`, max 40 chars.
- State dir: `.specify/issue-flow/<epic#>/` (`state.json`, `requests.json`, `issue.md`, `breakdown.md`, `issue-mapping.json`, `stories/<story#>/test-report.md`). *Deviation from spec paths (`specs/<feature>/…`): state must exist before `speckit.specify` creates the feature dir; `state.json` records `feature_dir`.*
- Config file: `.specify/extensions/issue-flow/issue-flow-config.yml` (schema = spec §6.6).
- CLI entry: `.specify/extensions/issue-flow/bin/issue-flow <subcommand> …` → `uv run --no-project --quiet --with pyyaml python3 <ext>/scripts/issue_flow/cli.py "$@"`. Every subcommand prints exactly one JSON object on stdout; diagnostics go to stderr; exit 0 success, 2 refusal/validation, 1 unexpected error.
- `ISSUE_FLOW_DRY_RUN=1` (and workflow input `dry_run: true`) → every GitHub write is logged to stderr as `DRY-RUN gh …` and returns a fake id; reads still happen.
- No secrets in any file. GitHub via the developer's `gh` login; Confluence via the Atlassian MCP.
- Only test-suite commands from the committed config run with `shell=True`. Nothing taken from issue/comment/review text is ever interpolated into a shell command or `gh` argument list as anything but a single argv element (or stdin/`--body-file`).
- Never merges PRs, never closes the Epic issue.

## Review Focus

1. **Approval comment edited after posting** (an `/approve` edited from a `/reject`, or text appended) → evaluate the comment's *current* body and its `updatedAt` must be after the request; test in Task 4.
2. **Same approver both approves and later rejects the same request** → latest comment per login wins; test in Task 4.
3. **One person listed in two roles of a multi-role stage (spec)** → one `/approve` satisfies every role they hold; test in Task 4.
4. **`tasks.md` with no "User Story" phase, or tasks outside any phase** → `breakdown` exits 2 with a message naming the problem rather than creating zero/odd Stories; test in Task 5.
5. **Test command that hangs** → per-suite `timeout_seconds` (default 1800) marks the suite failed with "timed out"; test in Task 6.

---

### Task 1: Spike — confirm workflow-engine semantics (throwaway)

**Files:**
- Create (scratch, not committed): `spike/loop.yml`, `spike/stage.yml`, `spike/check.sh`
- Create: `docs/superpowers/plans/2026-11-26-issue-flow-spike-notes.md`

**Interfaces:**
- Produces: the notes file answering Q1–Q5 below with the exact YAML that worked. Tasks 9 follows these answers; if an answer contradicts this plan, update Task 9's YAML shape accordingly before starting it.

- [ ] **Step 1: Write `spike/check.sh`** that prints `{"status":"<contents of spike/status.txt>"}`.
- [ ] **Step 2: Write `spike/loop.yml`**: `do-while` (condition `{{ steps.check.output.data.status != 'approved' }}`, `max_iterations: 50`) whose body is a `shell` step `check` (`output_format: json`) then an `if` (`status == 'pending'`) whose `then` is a `gate` (`options: [continue]`).
- [ ] **Step 3: Run it in a temp Spec Kit project** (`specify init --here --ai claude` in a scratch dir): `echo pending > spike/status.txt; specify workflow run spike/loop.yml < /dev/null`
  Expected: run PAUSED at the gate. Then `echo approved > spike/status.txt; specify workflow resume <run-id> < /dev/null` → run COMPLETED.
  Record **Q1**: does resume re-enter the loop and re-run `check`? **Q2**: are `steps.<id>` inside a loop body addressable from the `do-while` condition?
- [ ] **Step 4: Test a nested call**: `spike/stage.yml` with `inputs: {stage, generator}` and a `command` step whose `command: "{{ inputs.generator }}"`; call it from a parent via a `workflow` step after `specify workflow add spike/stage.yml`. Record **Q3**: is a templated `command` accepted? **Q4**: can a called workflow pause at a gate and be resumed from the parent run?
- [ ] **Step 5: Record Q5**: how a `shell` step's non-zero exit is surfaced (run FAILED vs. output) and whether `resume` retries the failed step.
- [ ] **Step 6: Commit the notes**

```bash
git add docs/superpowers/plans/2026-11-26-issue-flow-spike-notes.md
git commit -m "docs: workflow engine spike notes for issue-flow"
```

---

### Task 2: Extension skeleton, config loader, CLI dispatcher

**Files:**
- Create: `extensions/issue-flow/extension.yml`, `extensions/issue-flow/issue-flow-config.template.yml`, `extensions/issue-flow/bin/issue-flow`, `extensions/issue-flow/scripts/issue_flow/{__init__,cli,config,state}.py`
- Test: `extensions/issue-flow/tests/test_config.py`, `tests/test_state.py`, `tests/conftest.py`

**Interfaces:**
- Produces:
  - `config.load(root: Path) -> Config` (dataclass: `repo: str`, `trigger_label: str`, `progress_labels: dict[str,str]`, `confluence: dict`, `approvers: dict[str, list[str]]`, `stages: dict[str, StageCfg]`, `tests: TestsCfg`, `git: GitCfg`). `StageCfg(roles: list[str], require: "all"|"any" = "all", via: "comment"|"github_review" = "comment")`. `TestsCfg(max_fix_rounds: int = 5, suites: list[Suite])`, `Suite(name, type, command, required=True, timeout_seconds=1800)`. `GitCfg(default_branch="main", stacked=True)`.
  - `config.ConfigError(Exception)` — CLI maps to exit 2.
  - `state.StateDir(root: Path, epic: int)` with `.path`, `.read(name: str) -> dict` (missing → `{}`), `.write(name: str, data: dict)` (atomic write, pretty JSON).
  - `cli.main(argv: list[str]) -> int`; subcommands registered by later tasks via `cli.register(name, func)` where `func(args, ctx) -> dict`; `ctx` has `.root`, `.config`, `.gh` (Task 3), `.dry_run`.
- `extension.yml`: id `issue-flow`, version `0.1.0`, `requires.speckit_version: ">=1.4.0"`, tools `gh` (required), `git` (required), `uv` (required); config `issue-flow-config.yml` from template; commands added in Task 8. No hooks.

- [ ] **Step 1: Write failing tests**: `test_load_valid_config` (template with filled values → `cfg.stages["spec"].roles == ["product_owner","business_analyst","solution_architect"]`, `cfg.tests.max_fix_rounds == 5`); `test_unknown_role_in_stage_raises` (stage refers to `architect` not in approvers → `ConfigError` mentioning `architect`); `test_missing_repo_raises`; `test_state_roundtrip` (`write("state", {...})` then `read` equal; file under `.specify/issue-flow/42/state.json`).
- [ ] **Step 2: Run** `cd extensions/issue-flow && uv run --no-project --with pytest --with pyyaml pytest -q` → FAIL (modules missing).
- [ ] **Step 3: Implement** `config.py`, `state.py`, `cli.py` (argparse subparsers; prints `json.dumps(result)`; `ConfigError`/`Refusal` → exit 2 with `{"error": msg}`), `bin/issue-flow` wrapper, template (spec §6.6 values, `confluence.space_key: ""`).
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(issue-flow): extension skeleton, config and state`.

---

### Task 3: `gh` adapter with dry-run

**Files:**
- Create: `extensions/issue-flow/scripts/issue_flow/gh.py`
- Test: `extensions/issue-flow/tests/test_gh.py`, `tests/fakes.py` (`FakeRunner` recording argv and returning canned JSON)

**Interfaces:**
- Produces `GitHub(repo: str, runner: Callable[[list[str]], str] = run_gh, dry_run: bool = False)` with:
  - reads: `issue(n) -> dict` (`number,title,body,labels[name],state,url`), `comments(n) -> list[dict]` (`id,author,body,createdAt,updatedAt`), `pr_reviews(pr) -> list[dict]` (`author,state,body,submittedAt`), `pr_review_comments(pr) -> list[dict]`, `sub_issues(n) -> list[int]`.
  - writes (dry-run aware): `comment(n, body) -> int`, `create_issue(title, body, labels) -> int`, `add_sub_issue(parent, child) -> None` (GraphQL `addSubIssue`), `add_labels(n, labels)`, `remove_labels(n, labels)`, `edit_issue_body(n, body)`, `pr_create(head, base, title, body) -> dict(number,url)`, `pr_comment(pr, body)`.
- `Refusal(Exception)` defined in `cli.py` is raised by `issue()` callers, not here.

- [ ] **Step 1: Write failing tests**: `test_comments_parses_gh_json` (FakeRunner returns `gh api` paginated JSON → normalized dicts with `author` = login); `test_dry_run_write_does_not_call_runner` (dry_run=True, `comment(5,"x")` → runner not called, stderr contains `DRY-RUN gh`); `test_add_sub_issue_uses_node_ids` (asserts GraphQL mutation contains both node ids fetched via `issue` node lookup).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** using `gh api` (REST for issues/comments/reviews with `--paginate`, GraphQL for sub-issues); `run_gh` = `subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout`.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(issue-flow): gh adapter with dry-run`.

---

### Task 4: Approval requests and evaluation (`pull-issue`, `request`, `check-approval`)

**Files:**
- Create: `extensions/issue-flow/scripts/issue_flow/approvals.py`, `scripts/issue_flow/commands_review.py`
- Test: `extensions/issue-flow/tests/test_approvals.py`, `tests/test_commands_review.py`

**Interfaces:**
- Consumes: `Config`, `StateDir`, `GitHub` (Tasks 2–3).
- Produces (pure, in `approvals.py`):
  - `next_request_id(requests: dict, stage: str) -> str` → `IF-spec-r1`, then `r2`…
  - `content_hash(paths: list[Path]) -> str` (sha256 over sorted relative path + bytes).
  - `parse_command(body: str) -> tuple[str, str, str] | None` → `("approve"|"reject", request_id, reason)`.
  - `evaluate(request: dict, comments: list[dict], stage: StageCfg, approvers: dict[str,list[str]], current_hash: str) -> dict` → `{"status": "pending"|"approved"|"rejected"|"stale", "request_id", "approved_by": {role: login}, "waiting_for": [role], "reason": str, "feedback": str}`.
- Produces (CLI subcommands, all with `--epic N`):
  - `pull-issue` → refuses (exit 2) unless label `coding-agent`; writes `issue.md` (title, body, comments) ; returns `{"epic", "title", "path"}`.
  - `request --stage S --target N --artifact P [--artifact P…] [--page-url URL]` → records `{id, stage, target, hash, posted_at, artifacts, comment_id}` in `requests.json`, posts the review comment (stage, id, summary line, page URL or inline artifact (≤ 60 000 chars, else truncated + repo path), `@` mentions of the stage's logins, the two command lines); idempotent if the latest request for the stage has the same hash → returns it without posting.
  - `check-approval --stage S` → `evaluate(...)` on the target's comments, writes `<stage>.json`, returns the result.

- [ ] **Step 1: Write failing tests** (`test_approvals.py`):
  - `test_request_ids_increment`: `{}`→`IF-plan-r1`; with r1,r2 recorded → `IF-plan-r3`.
  - `test_parse_command`: `"/approve IF-spec-r2"` → `("approve","IF-spec-r2","")`; `"/REJECT IF-qa-r1 missing API tests"` → `("reject","IF-qa-r1","missing API tests")`; `"LGTM /approve IF-spec-r2"` → `None`.
  - `test_spec_needs_all_three_roles`: approvals from PO and BA only → `pending`, `waiting_for == ["solution_architect"]`; add SA → `approved`.
  - `test_unlisted_login_ignored`: `/approve` by `random-dev` → `pending`.
  - `test_reject_wins_with_reason`: PO approves, BA `/reject … needs offline mode` → `rejected`, `reason == "needs offline mode"`, `feedback` contains it.
  - `test_old_request_id_does_not_count`: `/approve IF-spec-r1` while current is r2 → `pending`.
  - `test_comment_before_request_ignored` (createdAt < posted_at) → `pending`.
  - `test_hash_change_is_stale`: current_hash ≠ request hash → `stale`.
  - Review Focus 1 `test_edited_comment_uses_current_body`: comment edited from `/reject` to `/approve` (updatedAt after posted_at) → counts as approve.
  - Review Focus 2 `test_latest_command_per_login_wins`: same TL `/approve` then `/reject` → `rejected`.
  - Review Focus 3 `test_one_login_in_two_roles`: login in PO and BA, plus SA → two approvals suffice → `approved`.
- [ ] **Step 2: Write failing tests** (`test_commands_review.py`, FakeRunner): `test_pull_issue_refuses_without_label` (exit 2, error mentions `coding-agent`); `test_request_posts_comment_with_mentions_and_commands` (body contains `@po-login`, `/approve IF-spec-r1`); `test_request_same_hash_is_idempotent` (second call posts nothing).
- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** `approvals.py` and `commands_review.py`; register subcommands.
- [ ] **Step 5: Run tests** → PASS.
- [ ] **Step 6: Commit** `feat(issue-flow): approval requests and evaluation`.

---

### Task 5: Story breakdown and creation (`breakdown`, `create-stories`, `next-story`)

**Files:**
- Create: `extensions/issue-flow/scripts/issue_flow/stories.py`
- Test: `extensions/issue-flow/tests/test_stories.py`, `tests/fixtures/tasks_*.md` (copy Spec Kit's `tasks-template.md` phase layout)

**Interfaces:**
- Produces:
  - `parse_tasks(md: str) -> list[Phase]`, `Phase(title: str, kind: "setup"|"foundational"|"user_story"|"polish"|"other", tasks: list[Task])`, `Task(id: str, text: str, done: bool)`; phase line regex `^## Phase \d+: (.+)$`; kind by title prefix (`Setup`, `Foundational`, `User Story`, `Polish`); task line `^- \[( |x|X)\] (T\d{3,})\b(.*)$`.
  - `plan_stories(phases) -> list[StoryPlan]`, `StoryPlan(title: str, tasks: list[Task])`: one per `user_story` phase; `setup`+`foundational`+`other` before the first user story are prepended to Story 1; `polish` and anything after the last user story appended to the last Story.
  - `breakdown --tasks PATH` → writes `breakdown.md` (per Story: title + task checklist), returns `{"stories":[{"title","task_ids"}]}`.
  - `create-stories` → for each planned Story not yet in `issue-mapping.json`: `create_issue(title, body=checklist + "Part of #<epic>", labels=[trigger_label])`, `add_sub_issue(epic, n)`; writes mapping `{"epic": N, "stories":[{"number","title","task_ids","status":"todo"}]}`; returns the mapping. Refuses (exit 2) if the `tasks` stage state is not `approved` for the current breakdown hash.
  - `next-story` → `{"story": <number of first story whose status != "done">|null, "index", "title", "task_ids"}`.
  - `set-story-status --story M --status todo|in_progress|in_review|done`.

- [ ] **Step 1: Write failing tests**: `test_parse_template_phases` (fixture with Setup, Foundational, US1, US2, Polish → 5 phases, kinds correct, `T001` parsed, `[x]` → done); `test_plan_folds_setup_and_polish` (→ 2 stories; Story 1 starts with T001; last story ends with the Polish task ids); Review Focus 4 `test_no_user_story_phase_refused` (exit 2, message `no "User Story" phase`) and `test_task_outside_phase_refused`; `test_create_stories_idempotent` (second run creates 0 issues); `test_create_stories_requires_tasks_approval` (exit 2); `test_next_story_skips_done`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `stories.py`; register subcommands.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit** `feat(issue-flow): story breakdown and sub-issue creation`.

---

### Task 6: Test runner (`run-tests`)

**Files:**
- Create: `extensions/issue-flow/scripts/issue_flow/testrun.py`
- Test: `extensions/issue-flow/tests/test_testrun.py`

**Interfaces:**
- Produces `run-tests --story M` → runs each suite (`subprocess.run(cmd, shell=True, timeout=suite.timeout_seconds)` in repo root), increments `round` in `stories/<M>/state.json`, writes `stories/<M>/test-report.md` (round, table suite/type/result/duration, last 80 lines of output per failed suite), returns `{"passed": bool, "round": int, "max_rounds": int, "exhausted": bool, "failed": [names], "report": path}`. `passed` ignores `required: false` suites. `exhausted` = `not passed and round >= max_fix_rounds`.
- `reset-rounds --story M` → sets round to 0 (used after a human "allow another round" gate and after QA/PR rejection).

- [ ] **Step 1: Write failing tests**: `test_all_pass` (`true` commands → passed, round 1); `test_required_failure_fails` (`exit 3`); `test_optional_failure_reported_not_blocking`; `test_exhausted_at_max_rounds` (max 2, fail twice → exhausted True); Review Focus 5 `test_timeout_marks_failed` (`sleep 5`, timeout 1 → failed, report contains `timed out`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(issue-flow): configurable test runner with fix rounds`.

---

### Task 7: Branches and pull requests (`start-story`, `open-pr`, `pr-status`)

**Files:**
- Create: `extensions/issue-flow/scripts/issue_flow/prs.py`
- Test: `extensions/issue-flow/tests/test_prs.py` (uses a temp git repo for branch tests)

**Interfaces:**
- Produces:
  - `slugify(title: str) -> str` (Global Constraints rule).
  - `start-story --story M` → base = previous story's branch if `git.stacked` and a previous story exists, else `state.json.feature_branch`; creates/checks out `<M>-<slug>` (reuses if exists); records `branch`, `base` in story state; labels issue `in-progress`; status `in_progress`. Returns `{"branch","base"}`.
  - `open-pr --story M` → commits nothing itself (implementation commits happen in agent steps); `git push -u origin <branch>`; if no PR exists for the branch: `pr_create(head=branch, base=base, title="<story title> (#M)", body)` where body = Story checklist, `Closes #M`, `Part of #<epic>`, latest test-report; else `pr_comment` with the new test report. Labels `in-review`, status `in_review`. Returns `{"pr","url","created": bool}`.
  - `pr-status --story M` → considers only reviews by `stages.pr` role logins submitted after the last push; latest per login: any `CHANGES_REQUESTED` → `{"status":"changes_requested","feedback": review bodies + inline comments}`; any `APPROVED` → `approved`; else `pending` with `waiting_for`. Writes `pr.json`.

- [ ] **Step 1: Write failing tests**: `test_slugify` (`"Login API: tokens & refresh!"` → `login-api-tokens-refresh`, ≤40 chars); `test_first_story_branches_from_feature_branch`; `test_second_story_stacks_on_first`; `test_open_pr_creates_then_comments` (second call → `pr_comment`, `created False`); `test_pr_status_ignores_non_team_lead_approval`; `test_pr_status_changes_requested_feedback`; `test_pr_status_ignores_reviews_before_last_push`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(issue-flow): story branches and pull requests`.

---

### Task 8: Agent commands (`setup`, `publish`, `fix`, `status`)

**Files:**
- Create: `extensions/issue-flow/commands/{setup,publish,fix,status}.md`; Modify: `extensions/issue-flow/extension.yml` (`provides.commands`)
- Create: `extensions/issue-flow/claude-permissions.json` (snippet for `.claude/settings.json`)

**Interfaces:**
- Consumes: CLI subcommands from Tasks 4–7 (commands call `.specify/extensions/issue-flow/bin/issue-flow …`).
- Produces commands:
  - `speckit.issue-flow.setup` — interactive (not used headless): asks repo, approver logins per role (verifies each with `gh api users/<login>`), resolves the user's personal Confluence space via the Atlassian MCP (`getConfluenceSpaces`, type personal) and writes `confluence.space_key`, asks test suites; writes config; creates labels `coding-agent`, `in-progress`, `in-review` if missing; prints the permissions snippet to merge.
  - `speckit.issue-flow.publish <epic> <stage>` — for spec/plan/tasks: creates or updates one Confluence page per artifact titled `#<epic> <issue title> — <Stage>` in `confluence.space_key` (page id stored in `state.json.pages[stage]`), then runs `issue-flow request --stage <stage> --target <epic> --artifact … --page-url <url>`. For `qa`: no Confluence; runs `request --stage qa --target <story> --artifact <test-report>`.
  - `speckit.issue-flow.fix <epic> <story>` — reads the newest of: failing `test-report.md`, rejected `qa.json` feedback, `pr.json` feedback; fixes code test-first; ticks tasks; commits on the story branch with message `fix(#<story>): …`.
  - `speckit.issue-flow.status <epic>` — read-only summary from state files: stage, who it waits for, links.
- `claude-permissions.json`: `allow` for `Bash(.specify/extensions/issue-flow/bin/issue-flow:*)`, `Bash(gh:*)`, `Bash(git:*)`, the Atlassian MCP Confluence tools `mcp__plugin_atlassian_atlassian__createConfluenceContent`, `…__updateConfluenceContent`, `…__getConfluenceContent`, `…__getAccessibleAtlassianResources`, `…__discover`, `…__executeRead`.

- [ ] **Step 1: Write the four command files** (frontmatter `description:`; body = numbered steps naming the exact CLI calls and outputs above; each command ends by printing one line `RESULT: {...json...}`).
- [ ] **Step 2: Validate** in speckit-sample: `specify extension add --dev ../speckit-catalog/extensions/issue-flow` → lists 4 commands; `.claude/skills/speckit-issue-flow-publish/SKILL.md` exists.
- [ ] **Step 3: Smoke** `claude -p "/speckit-issue-flow-status 1"` in speckit-sample with an empty state → prints "no run for #1".
- [ ] **Step 4: Commit** `feat(issue-flow): agent commands and permissions snippet`.

---

### Task 9: Workflows `issue-flow-stage` and `issue-to-pr`

**Files:**
- Create: `workflows/issue-flow-stage/workflow.yml`, `workflows/issue-to-pr/workflow.yml`

**Interfaces:**
- Consumes: Task 1 notes (exact loop/gate/call shapes), CLI subcommands, agent commands.
- `issue-flow-stage` inputs: `epic`, `stage` (`spec|plan|tasks`), `generator` (e.g. `speckit.plan`), `generator_args`, `dry_run`. Body: `command: speckit.issue-flow.publish` → `do-while (status != approved)`: shell `check` = `issue-flow check-approval --epic … --stage …` (`output_format: json`) → `switch` on `steps.check.output.data.status`: `pending` → `gate` ("Waiting for {{ waiting_for }} on #{{ epic }} — approve with /approve {{ request_id }}; then run specify workflow resume"); `rejected`/`stale` → `command: {{ inputs.generator }}` with args `generator_args + "\n\nReviewer feedback:\n" + feedback` → `command: speckit.issue-flow.publish`.
  *If Task 1 Q3/Q4 say templated commands or nested pauses don't work, inline this block three times in `issue-to-pr` instead.*
- `issue-to-pr` inputs: `epic` (required), `integration` (default `auto`), `dry_run` (default `false`, exported as `ISSUE_FLOW_DRY_RUN`). Steps in spec §6.1 order: shell `pull-issue` → `speckit.specify` (args = issue.md contents) → stage(spec) → `speckit.plan` → stage(plan) → `speckit.tasks` → shell `breakdown` → stage(tasks, generator `speckit.tasks`) → shell `create-stories` → `while` (`next-story.story != null`): shell `next-story` → shell `start-story` → `command: speckit.implement` (args: only task ids of this story) → `do-while (not tests.passed)`: shell `run-tests` → `if exhausted` → gate ("allow another round?" `options: [retry, abort]`, then shell `reset-rounds`) → else-if failed → `command: speckit.issue-flow.fix` → QA loop (publish qa, check, `rejected` → shell `reset-rounds`, fix, back into tests) → shell `open-pr` → PR loop (`pr-status`: `pending` → gate; `changes_requested` → fix → tests → QA → `open-pr`) → shell `set-story-status done`.

- [ ] **Step 1: Write both YAML files** per Task 1's confirmed shapes.
- [ ] **Step 2: Validate**: `specify workflow add workflows/issue-flow-stage/workflow.yml && specify workflow add workflows/issue-to-pr/workflow.yml && specify workflow info issue-to-pr` → step graph prints with no validation errors.
- [ ] **Step 3: Dry-run** in speckit-sample on a real `coding-agent` issue: `specify workflow run issue-to-pr -i epic=<n> -i dry_run=true` → pauses at the spec gate; stderr shows `DRY-RUN gh issue comment`; no comment appears on GitHub.
- [ ] **Step 4: Commit** `feat(issue-flow): issue-to-pr and stage workflows`.

---

### Task 10: Package and publish to the internal catalog

**Files:**
- Create: `scripts/build-extension.sh`, `workflows/catalog.json`
- Modify: `catalog.json`, `README.md`

**Interfaces:**
- `scripts/build-extension.sh <id> <version>` → `dist/<id>-<version>.zip` containing top folder `<id>-<version>/` with the extension files (excluding `tests/`); prints its sha256.
- `catalog.json` entry `issue-flow` (same fields as `jira`, `download_url` = GitHub release asset `https://github.com/integrationfirst/speckit-catalog/releases/download/issue-flow-v0.1.0/issue-flow-0.1.0.zip`, `vetting.archive_sha256`, `provides.commands: 4`, `hooks: 0`).
- `workflows/catalog.json`: same schema as `https://raw.githubusercontent.com/github/spec-kit/main/workflows/catalog.json` (fetch it and mirror its fields), entries `issue-to-pr`, `issue-flow-stage` with raw URLs to their `workflow.yml`.
- README: new rows, "Use issue-flow in a project" (catalog lines for extensions *and* `specify workflow catalog add … --name internal --install-allowed`, `specify extension add issue-flow`, `/speckit-issue-flow-setup`, merge permissions, `specify workflow add issue-to-pr`, run/resume commands).

- [ ] **Step 1: Write build script; run** `scripts/build-extension.sh issue-flow 0.1.0` → zip exists; `python3 -I -m zipfile -l dist/issue-flow-0.1.0.zip` shows `issue-flow-0.1.0/extension.yml` and no `tests/`.
- [ ] **Step 2: Update catalogs and README.**
- [ ] **Step 3: Commit** `feat: publish issue-flow extension and workflows in internal catalog`.
- [ ] **Step 4: Ask the user before pushing and releasing** (public repo). Then `git push` and `gh release create issue-flow-v0.1.0 dist/issue-flow-0.1.0.zip --title "issue-flow 0.1.0"`; verify `curl -sL <download_url> | sha256sum` equals the catalog hash.

---

### Task 11: End-to-end in `integrationfirst/speckit-sample`

**Files (speckit-sample, on branch `feat/issue-flow`):**
- Modify: `.specify/extension-catalogs.yml` (unchanged — internal already first); Create: `.specify/workflow-catalogs.yml`, `.specify/extensions/issue-flow/issue-flow-config.yml`, `.claude/settings.json`; tests for the sample: a trivial `tests/` + suite commands so the loop has something to run.

- [ ] **Step 1: Install from catalog**: `specify extension add issue-flow` and `specify workflow add issue-to-pr` → installed from `internal`, no untrusted-source prompt.
- [ ] **Step 2: Run `/speckit-issue-flow-setup`** with the user's logins for roles (ask the user which logins; for testing a single login may hold all roles) and personal Confluence space.
- [ ] **Step 3: Live scenarios** on a small `coding-agent` issue (ask the user before the first live run; repo is public). Check each:
  - [ ] issue without label → refused.
  - [ ] spec: reject with reason → regenerated, r2 posted; approve r1 after r2 → ignored; approve r2 by all spec roles → continues.
  - [ ] unlisted user's `/approve` → ignored.
  - [ ] edit spec.md after approval → stale → republished.
  - [ ] tasks approval → Stories created as sub-issues with label; re-run creates none.
  - [ ] forced failing test → fixed within the loop; exhaustion gate with `max_fix_rounds: 1`.
  - [ ] QA reject → fix → QA again.
  - [ ] PR changes requested by Team Lead → fix → tests → QA → PR updated; approval → next Story branches from previous branch and its PR targets it.
- [ ] **Step 4: Record results** in `docs/superpowers/plans/2026-11-26-issue-flow-e2e.md` (catalog repo); fix defects in the owning task's code with a regression test first.
- [ ] **Step 5: Commit** (both repos) `test(issue-flow): end-to-end verification`.
