import argparse
import subprocess
from pathlib import Path

import pytest

from issue_flow import cli
from issue_flow.commands import prs as cmd
from issue_flow.prs import slugify
from issue_flow.state import StateDir

EPIC = 7


def ns(story, **kw):
    return argparse.Namespace(epic=EPIC, story=story, **kw)


def git(root, *a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


class FakeGh:
    def __init__(self, reviews=None, comments=None):
        self.calls, self._reviews, self._comments = [], reviews or [], comments or []

    def pr_create(self, head, base, title, body):
        self.calls.append(("pr_create", head, base, title, body))
        return {"number": 55, "url": "https://github.com/acme/widgets/pull/55"}

    def pr_comment(self, pr, body):
        self.calls.append(("pr_comment", pr, body))

    def add_labels(self, n, labels):
        self.calls.append(("add_labels", n, labels))

    def remove_labels(self, n, labels):
        self.calls.append(("remove_labels", n, labels))

    def pr_reviews(self, pr):
        return self._reviews

    def pr_review_comments(self, pr):
        return self._comments


@pytest.fixture
def repo(project):
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.email", "t@t")
    git(project, "config", "user.name", "t")
    (project / "f.txt").write_text("x")
    git(project, "add", "f.txt")
    git(project, "commit", "-qm", "init")
    git(project, "branch", "specfeat")
    state = StateDir(project, EPIC)
    state.write("state", {"feature_branch": "specfeat", "feature_dir": "specs/specfeat"})
    state.write("issue-mapping", {"epic": EPIC, "stories": [
        {"number": 11, "title": "Login API: tokens & refresh!", "task_ids": ["T001", "T002"],
         "status": "todo", "linked": True},
        {"number": 12, "title": "Profile page", "task_ids": ["T003"], "status": "todo",
         "linked": True}]})
    return project


def mkctx(root, gh=None, dry=False):
    return cli.Context(root, dry_run=dry, gh=gh or FakeGh())


def test_slugify():
    s = slugify("Login API: tokens & refresh!")
    assert s == "login-api-tokens-refresh"
    long = slugify("a" * 30 + " " + "b" * 30)
    assert len(long) <= 40 and not long.endswith("-") and not long.startswith("-")


def test_first_story_branches_from_feature_branch(repo):
    gh = FakeGh()
    r = cmd.start_story(ns(11), mkctx(repo, gh))
    assert r == {"branch": "11-login-api-tokens-refresh", "base": "specfeat"}
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "11-login-api-tokens-refresh"
    st = StateDir(repo, EPIC).read("stories/11/state")
    assert st["branch"] == r["branch"] and st["base"] == "specfeat"
    assert ("add_labels", 11, ["in-progress"]) in gh.calls
    m = StateDir(repo, EPIC).read("issue-mapping")
    assert m["stories"][0]["status"] == "in_progress"
    # reuse existing branch
    git(repo, "checkout", "-q", "main")
    assert cmd.start_story(ns(11), mkctx(repo))["branch"] == r["branch"]
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == r["branch"]


def test_second_story_stacks_on_first(repo):
    with pytest.raises(cli.Refusal):
        cmd.start_story(ns(12), mkctx(repo))
    cmd.start_story(ns(11), mkctx(repo))
    r = cmd.start_story(ns(12), mkctx(repo))
    assert r == {"branch": "12-profile-page", "base": "11-login-api-tokens-refresh"}


def test_start_story_refusals(repo):
    with pytest.raises(cli.Refusal):
        cmd.start_story(ns(99), mkctx(repo))
    StateDir(repo, EPIC).write("state", {})
    with pytest.raises(cli.Refusal):
        cmd.start_story(ns(11), mkctx(repo))


def test_open_pr_creates_then_comments(repo, monkeypatch):
    pushes = []
    monkeypatch.setattr(cmd, "git", lambda args, cwd: pushes.append(args) or "")
    monkeypatch.setattr(cmd, "now", lambda: "2026-01-01T00:00:00Z")
    state = StateDir(repo, EPIC)
    state.write("stories/11/state", {"round": 2, "branch": "11-x", "base": "specfeat"})
    rep = repo / ".specify/issue-flow/7/stories/11/test-report.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text("REPORT-ONE")
    gh = FakeGh()
    r = cmd.open_pr(ns(11), mkctx(repo, gh))
    assert r == {"pr": 55, "url": "https://github.com/acme/widgets/pull/55", "created": True}
    assert ["push", "-u", "origin", "11-x"] in pushes
    create = [c for c in gh.calls if c[0] == "pr_create"][0]
    assert create[1:4] == ("11-x", "specfeat", "Login API: tokens & refresh! (#11)")
    body = create[4]
    assert "T001" in body and "Closes #11" in body and "Part of #7" in body and "REPORT-ONE" in body
    assert ("remove_labels", 11, ["in-progress"]) in gh.calls
    assert ("add_labels", 11, ["in-review"]) in gh.calls
    st = state.read("stories/11/state")
    assert st["pr"] == 55 and st["last_push_at"] == "2026-01-01T00:00:00Z" and st["round"] == 2
    assert state.read("stories/11/pr")["pr"] == 55
    assert state.read("issue-mapping")["stories"][0]["status"] == "in_review"
    rep.write_text("REPORT-TWO")
    gh.calls.clear()
    r2 = cmd.open_pr(ns(11), mkctx(repo, gh))
    assert r2["created"] is False and r2["pr"] == 55
    assert not [c for c in gh.calls if c[0] == "pr_create"]
    cm = [c for c in gh.calls if c[0] == "pr_comment"][0]
    assert cm[1] == 55 and "REPORT-TWO" in cm[2]


def test_open_pr_dry_run_skips_push(repo, capfd):
    StateDir(repo, EPIC).write("stories/11/state", {"branch": "11-x", "base": "specfeat"})
    cmd.open_pr(ns(11), mkctx(repo, FakeGh(), dry=True))
    assert "DRY-RUN git push -u origin 11-x" in capfd.readouterr().err


def pr_ctx(repo, reviews, comments=None, pushed="2026-01-01T00:00:00Z"):
    StateDir(repo, EPIC).write("stories/11/state",
                               {"branch": "b", "base": "x", "pr": 5, "pr_url": "u://pr",
                                "last_push_at": pushed})
    return mkctx(repo, FakeGh(reviews, comments))


def rv(author, state, at="2026-01-02T00:00:00Z", body=""):
    return {"author": author, "state": state, "body": body, "submittedAt": at}


def test_pr_status_ignores_non_team_lead_approval(repo):
    ctx = pr_ctx(repo, [rv("random", "APPROVED")])
    r = cmd.pr_status(ns(11, wait=False), ctx)
    assert r["status"] == "pending" and r["waiting_for"] == ["team_lead-user"]
    r = cmd.pr_status(ns(11, wait=False), pr_ctx(repo, [rv("team_lead-user", "APPROVED")]))
    assert r["status"] == "approved"


def test_pr_status_changes_requested_feedback(repo):
    ctx = pr_ctx(repo, [rv("team_lead-user", "CHANGES_REQUESTED", body="fix it")],
                 [{"author": "team_lead-user", "body": "rename", "path": "a.py", "line": 3,
                   "createdAt": "2026-01-02T00:00:00Z"}])
    r = cmd.pr_status(ns(11, wait=False), ctx)
    assert r["status"] == "changes_requested"
    assert "fix it" in r["feedback"] and "@team_lead-user a.py:3: rename" in r["feedback"]
    assert StateDir(repo, EPIC).read("stories/11/pr")["status"] == "changes_requested"


def test_pr_status_ignores_reviews_before_last_push(repo):
    ctx = pr_ctx(repo, [rv("team_lead-user", "CHANGES_REQUESTED", at="2025-12-31T00:00:00Z")])
    assert cmd.pr_status(ns(11, wait=False), ctx)["status"] == "pending"


def test_pr_status_latest_per_login_and_wait(repo):
    ctx = pr_ctx(repo, [rv("team_lead-user", "CHANGES_REQUESTED", at="2026-01-02T00:00:00Z"),
                        rv("team_lead-user", "APPROVED", at="2026-01-03T00:00:00Z")])
    assert cmd.pr_status(ns(11, wait=False), ctx)["status"] == "approved"
    with pytest.raises(cli.Waiting) as e:
        cmd.pr_status(ns(11, wait=True), pr_ctx(repo, []))
    assert "waiting for PR review from team_lead-user on u://pr" in str(e.value)
