"""Pure logic for story branches and pull requests."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

DECISIVE = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")


def slugify(title: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:max_len].strip("-")


def branch_name(story: int, title: str) -> str:
    return f"{story}-{slugify(title)}"


def git(args: list[str], cwd: Path) -> str:
    """Run git via argv (never a shell); raises CalledProcessError on failure."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def pick_base(stories: list[dict], index: int, branches: dict[int, str],
              feature_branch: str, default_branch: str, stacked: bool) -> str | None:
    """Base branch for story `index`; None when stacked and the previous story has no branch."""
    if not stacked:
        return default_branch
    if index == 0:
        return feature_branch
    return branches.get(stories[index - 1]["number"]) or None


def render_pr_body(story: dict, epic: int, report: str) -> str:
    lines = [f"## {story['title']}", "", "### Tasks"]
    lines += [f"- [ ] {t}" for t in story.get("task_ids", [])]
    lines += ["", f"Closes #{story['number']}", f"Part of #{epic}", "",
              "### Latest test report", "", report or "_No test report available._"]
    return "\n".join(lines)


def evaluate_reviews(reviews: list[dict], comments: list[dict], logins: list[str],
                     since: str) -> dict:
    """Only reviews by `logins` submitted after `since` count; latest decisive review per login."""
    latest: dict[str, dict] = {}
    for r in sorted((r for r in reviews if r["author"] in logins
                     and r["state"] in DECISIVE and r["submittedAt"] > since),
                    key=lambda r: r["submittedAt"]):
        latest[r["author"]] = r
    changes = [r for r in latest.values() if r["state"] == "CHANGES_REQUESTED"]
    if changes:
        fb = [f"@{r['author']}: {r['body']}" for r in changes if r["body"].strip()]
        fb += [f"@{c['author']} {c['path']}:{c['line']}: {c['body']}" for c in comments
               if c["author"] in logins and c["createdAt"] > since]
        return {"status": "changes_requested", "feedback": "\n".join(fb)}
    if any(r["state"] == "APPROVED" for r in latest.values()):
        return {"status": "approved"}
    return {"status": "pending", "waiting_for": list(logins)}
