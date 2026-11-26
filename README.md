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

## Adding or updating an extension

1. Find it (`specify extension search <term>`) and download the release archive for a **tagged** version.
2. Review every file in the archive: `extension.yml` (commands, hooks, `effect`), command prompts,
   and any scripts. Note what it writes and to which external systems.
3. Record the archive's SHA-256 (`sha256sum <archive>.zip`).
4. Add/update the entry in `catalog.json` with `download_url` pointing at that exact tag, and fill
   in the `vetting` block (`vetted_at`, `archive_sha256`, `notes`). Bump `updated_at`.
5. Update the table above and open a PR; a second person approves before merge.

> Spec Kit does not verify archive checksums. `download_url` points at the upstream tag, so the
> recorded `archive_sha256` is for manual re-verification — if a tag is ever moved upstream, the
> hash will no longer match. Re-check with:
> `curl -sL <download_url> | sha256sum`
