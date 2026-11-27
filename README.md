# speckit-catalog

Internal catalog of **vetted** [Spec Kit](https://github.com/github/spec-kit) extensions.
Only extensions listed in `catalog.json` have been reviewed and are approved for use in our projects.

Catalog URL:

```
https://raw.githubusercontent.com/integrationfirst/speckit-catalog/main/catalog.json
```

## Extensions

| ID | Version | What it does | Vetted |
|----|---------|--------------|--------|
| `jira` | 2.1.0 | Creates Jira Epic / Story / Sub-task hierarchy from `spec.md` + `tasks.md` and syncs task status, via the Atlassian MCP server. | 2026-11-26 |
| `issue-flow` | 0.1.1 | GitHub issue-driven flow: approvals via issue comments, stacked PRs per Story, test gating. Python CLI + 4 commands (`setup`, `status`, `publish`, `fix`). | 2026-11-26 |

## Workflows

Listed in `workflows/catalog.json` (`https://raw.githubusercontent.com/integrationfirst/speckit-catalog/main/workflows/catalog.json`).

| ID | Version | What it does |
|----|---------|--------------|
| `issue-to-pr` | 0.1.1 | GitHub issue labelled `coding-agent` to approved spec, plan, tasks, then one tested, QA-approved, reviewed PR per Story. Needs the `issue-flow` extension and `issue-flow-stage`. |
| `issue-flow-stage` | 0.1.1 | One approval stage: publish an artifact, wait for approvers, regenerate on rejection. Called by `issue-to-pr`. |

## Use it in a project

Commit `.specify/extension-catalogs.yml` in the project:

```yaml
catalogs:
  - name: internal
    url: https://raw.githubusercontent.com/integrationfirst/speckit-catalog/main/catalog.json
    priority: 1
    install_allowed: true
    description: Vetted internal extensions
  - name: default
    url: https://raw.githubusercontent.com/github/spec-kit/main/extensions/catalog.json
    priority: 2
    install_allowed: true
    description: Built-in catalog of installable extensions
  - name: community
    url: https://raw.githubusercontent.com/github/spec-kit/main/extensions/catalog.community.json
    priority: 3
    install_allowed: false
    description: Community-contributed extensions (discovery only)
```

A project-level file **replaces** Spec Kit's built-in catalog stack, which is why `default` and
`community` are listed again. Keep `community` discovery-only — that is the vetting boundary.

Then:

```bash
specify extension catalog list     # internal should appear first
specify extension add jira         # installs from the internal catalog
```

Per-extension setup notes:

- **jira** — edit `.specify/extensions/jira/jira-config.yml`: set `project.key`, and point
  `mcp_server` at the Atlassian MCP configured in your agent. Run `/speckit-jira-discover-fields`
  to find required custom fields. No credentials go in the repo; auth is handled by the MCP server.

## Use issue-flow in a project

1. Register the extension catalog (above) and the workflow catalog:
   ```bash
   specify workflow catalog add https://raw.githubusercontent.com/integrationfirst/speckit-catalog/main/workflows/catalog.json --name internal
   ```
2. Install: `specify extension add issue-flow`
3. Run `/speckit-issue-flow-setup` (attended) to create the config.
4. **Commit and push the config to the repository's default branch** (e.g. `main`) before any run:
   ```bash
   git add .specify/extensions/issue-flow/issue-flow-config.yml
   git commit -m "chore: issue-flow config" && git push origin main
   ```
   `approvers`, `stages` and `git` are read only from the config committed on origin's default branch
   (asked from the remote with `git ls-remote`, then fetched by every CLI call), never from the working
   tree, so an agent step editing the file cannot change who may approve or where PRs go. A run refuses
   to start if the committed config is missing or origin cannot be reached. After changing approvers,
   commit and push; no manual fetch is needed. (Only a project with no `origin` remote falls back to the
   local branch named by the working tree's `git.default_branch`.)
5. Merge `.specify/extensions/issue-flow/claude-permissions.json` into `.claude/settings.json` (both `allow`
   and `deny`), and also allow your project's own test commands (headless runs cannot prompt). The full
   snippet:
   ```json
   {
     "permissions": {
       "allow": [
         "Read",
         "Edit",
         "Write",
         "Bash(.specify/scripts/bash/*:*)",
         "Bash(.specify/extensions/issue-flow/bin/issue-flow.sh:*)",
         "Bash(git add:*)",
         "Bash(git commit:*)",
         "Bash(git status:*)",
         "Bash(git diff:*)",
         "Bash(git log:*)",
         "Bash(git rev-parse:*)",
         "mcp__plugin_atlassian_atlassian__createConfluenceContent",
         "mcp__plugin_atlassian_atlassian__updateConfluenceContent",
         "mcp__plugin_atlassian_atlassian__getConfluenceContent",
         "mcp__plugin_atlassian_atlassian__getAccessibleAtlassianResources",
         "mcp__plugin_atlassian_atlassian__discover",
         "mcp__plugin_atlassian_atlassian__executeRead",
         "mcp__plugin_atlassian_atlassian__executeWrite"
       ],
       "deny": [
         "Edit(.specify/extensions/issue-flow/**)",
         "Write(.specify/extensions/issue-flow/**)",
         "Edit(.specify/issue-flow/**)",
         "Write(.specify/issue-flow/**)"
       ]
     }
   }
   ```
   The `deny` entries stop agent steps from editing the extension, its config and the run state
   (`.specify/issue-flow/`, written only by the CLI; the publish agent records Confluence page ids with
   `issue-flow.sh set-page`).
6. Choose the `team_lead` logins (they approve PRs) so that **none of them is the login that runs the
   workflow**: PRs are opened with the developer's `gh` login, and GitHub refuses an APPROVE review from
   the PR author, so that person could never approve the PR stage.
7. Create a GitHub issue labelled `coding-agent`.
8. Install the workflows:
   ```bash
   specify workflow add issue-to-pr
   specify workflow add issue-flow-stage
   ```
9. Run: `specify workflow run issue-to-pr -i epic=<n>`

Story 1 branches from the spec feature branch (the spec, plan and tasks are committed there first, so they
travel with Story 1's PR) and its PR targets the default branch. Story N branches from and targets
Story N-1's branch (stacked PRs; unstacked mode was removed in 0.1.1, and `git.stacked: false` is
refused). Run state lives in `.specify/issue-flow/`, which is git-ignored.
Removing the `coding-agent` label from the Epic stops the flow at its next GitHub step.

### While a run is waiting

The terminal only shows `Shell command exited with code 3`: the run is waiting for people, not broken.
Run `/speckit-issue-flow-status <n>` (or `specify workflow status <run-id>`) to see who it is waiting for.

- Approvers comment `/approve <id>` or `/reject <id> <reason>` on the issue.
- PR approval is a GitHub review by a `team_lead` (not the login running the workflow; see step 6).
- Then continue with `specify workflow resume <run-id>`.
- The fix-round exhaustion gate needs a TTY: resume it from an interactive terminal.

### Dry runs

`-i dry_run=true` logs GitHub writes (`DRY-RUN gh ...`) instead of making them. Confluence page writes
by the publish agent still happen (leave `confluence.space_key` empty to skip). A dry run stops at the
story loop, so no Stories are created. When switching between dry and real runs for an epic, start from a
fresh state dir: `rm -rf .specify/issue-flow/<n>`; the CLI refuses to mix them (exit 2). Running
`issue-flow-stage` on its own records the mode too (its first step, `init-mode`), so a standalone
`-i dry_run=true` stage run stays dry for the publish agent.

### Recovering a stuck run

If a loop guard assert fails, inspect `specify workflow status`, fix the cause, and start a new run with a
clean state dir (`rm -rf .specify/issue-flow/<n>`).

### Security notes

- Approvals only count from the logins configured per role, and only for the current request id, content
  hash, or PR head commit.
- The headless permissions deliberately exclude `gh` and `git push`; only the `issue-flow` CLI touches GitHub.
- Do not list the developer's own login as an approver for real use: the agent runs with the developer's
  `gh` credentials, so it could approve its own work.

## Adding or updating an extension

1. Find it (`specify extension search <term>`) and download the release archive for a **tagged** version.
2. Review every file in the archive: `extension.yml` (commands, hooks, `effect`), command prompts,
   and any scripts. Note what it writes and to which external systems.
3. Record the archive's SHA-256 (`sha256sum <archive>.zip`).
4. Add/update the entry in `catalog.json` with `download_url` pointing at that exact tag, and fill
   in the `vetting` block (`vetted_at`, `archive_sha256`, `notes`). Bump `updated_at`.
5. Update the table above (for first-party builds: `scripts/build-extension.sh <id> <version>`, attach `dist/<id>-<version>.zip` to a GitHub release, and record its sha256) and open a PR; a second person approves before merge.

> Spec Kit does not verify archive checksums. `download_url` points at the upstream tag, so the
> recorded `archive_sha256` is for manual re-verification — if a tag is ever moved upstream, the
> hash will no longer match. Re-check with:
> `curl -sL <download_url> | sha256sum`
