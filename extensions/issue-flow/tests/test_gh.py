import json

import pytest

from fakes import FakeRunner
from issue_flow import gh as gh_mod
from issue_flow.gh import GitHub


def test_comments_parses_gh_json():
    pages = [[{"id": 1, "user": {"login": "ann"}, "body": "hi",
               "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}],
             [{"id": 2, "user": None, "body": None,
               "created_at": "c", "updated_at": "u"}]]
    r = FakeRunner([pages])
    out = GitHub("acme/widgets", runner=r).comments(5)
    assert out[0] == {"id": 1, "author": "ann", "body": "hi",
                      "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z"}
    assert out[1]["author"] == "" and out[1]["body"] == "" and len(out) == 2
    argv = r.calls[0][0]
    assert argv[:2] == ["api", "--paginate"] and "--slurp" in argv
    assert "repos/acme/widgets/issues/5/comments" in argv


def test_concatenated_arrays_parsed_robustly():
    r = FakeRunner(['[{"id":1,"user":{"login":"a"},"body":"x","created_at":"c","updated_at":"u"}]'
                    '[{"id":2,"user":{"login":"b"},"body":"y","created_at":"c","updated_at":"u"}]'])
    assert [c["id"] for c in GitHub("a/b", runner=r).comments(1)] == [1, 2]


def test_issue_normalized():
    r = FakeRunner([{"number": 7, "title": "T", "body": None, "labels": [{"name": "x"}],
                     "state": "open", "html_url": "https://u"}])
    assert GitHub("a/b", runner=r).issue(7) == {
        "number": 7, "title": "T", "body": "", "labels": ["x"], "state": "open", "url": "https://u"}


def test_reviews_and_review_comments():
    r = FakeRunner([
        [[{"user": {"login": "r"}, "state": "APPROVED", "body": "", "submitted_at": "t"}]],
        [[{"user": {"login": "r"}, "body": "b", "path": "f.py", "line": 3, "created_at": "t"}]],
    ])
    g = GitHub("a/b", runner=r)
    assert g.pr_reviews(2) == [{"author": "r", "state": "APPROVED", "body": "", "submittedAt": "t",
                                "commit_id": ""}]
    assert g.pr_review_comments(2) == [
        {"author": "r", "body": "b", "path": "f.py", "line": 3, "createdAt": "t"}]
    assert "repos/a/b/pulls/2/reviews" in r.calls[0][0]
    assert "repos/a/b/pulls/2/comments" in r.calls[1][0]


def test_sub_issues():
    r = FakeRunner([{"data": {"repository": {"issue": {"subIssues": {"nodes": [
        {"number": 3}, {"number": 4}]}}}}}])
    assert GitHub("a/b", runner=r).sub_issues(1) == [3, 4]
    assert "subIssues(first:100)" in " ".join(r.calls[0][0])


def test_dry_run_write_does_not_call_runner(capsys):
    r = FakeRunner()
    g = GitHub("a/b", runner=r, dry_run=True)
    assert g.comment(5, "x") == 0
    assert g.create_issue("t", "b", ["l"]) == 0
    assert g.pr_create("h", "b", "t", "body") == {"number": 0, "url": "dry-run"}
    assert g.add_labels(1, ["a"]) is None and g.add_sub_issue(1, 2) is None
    assert r.calls == []
    err = capsys.readouterr().err
    assert "DRY-RUN gh" in err


def test_dry_run_truncates_long_body(capsys):
    GitHub("a/b", runner=FakeRunner(), dry_run=True).comment(1, "z" * 1000)
    err = capsys.readouterr().err
    assert "z" * 201 not in err and "z" * 200 in err


def test_dry_run_reads_still_call_runner():
    r = FakeRunner([{"number": 1, "title": "", "body": "", "labels": [], "state": "open", "html_url": ""}])
    GitHub("a/b", runner=r, dry_run=True).issue(1)
    assert len(r.calls) == 1


def test_add_sub_issue_uses_node_ids():
    def node(n):
        return lambda a, i: "issue(number:" in " ".join(a) and f"number={n}" in " ".join(a)
    r = FakeRunner([
        (node(1), {"data": {"repository": {"issue": {"id": "NODE_PARENT"}}}}),
        (node(2), {"data": {"repository": {"issue": {"id": "NODE_CHILD"}}}}),
        (lambda a, i: "addSubIssue" in " ".join(a), {"data": {}}),
    ])
    GitHub("acme/widgets", runner=r).add_sub_issue(1, 2)
    mut = " ".join(r.calls[-1][0])
    assert "addSubIssue" in mut and "issueId=NODE_PARENT" in mut and "subIssueId=NODE_CHILD" in mut


def test_hostile_body_stays_one_argv_element():
    r = FakeRunner([{"id": 123}])
    body = "x'; rm -rf / $(whoami) `id`"
    assert GitHub("a/b", runner=r).comment(9, body) == 123
    argv = r.calls[0][0]
    assert f"body={body}" in argv


def test_create_issue_and_pr_create_and_labels():
    r = FakeRunner(["https://github.com/a/b/issues/12\n", "https://github.com/a/b/pull/30\n", "", ""])
    g = GitHub("a/b", runner=r)
    assert g.create_issue("T", "B", ["l1", "l2"]) == 12
    assert g.pr_create("h", "main", "T", "BODY") == {"number": 30, "url": "https://github.com/a/b/pull/30"}
    argv, inp = r.calls[1]
    assert argv == ["pr", "create", "--repo", "a/b", "--head", "h", "--base", "main",
                    "--title", "T", "--body-file", "-"] and inp == "BODY"
    g.add_labels(1, ["a", "b"])
    g.remove_labels(1, ["a"])
    assert "labels[]=a" in r.calls[2][0] and "labels[]=b" in r.calls[2][0]
    assert any(a.endswith("/labels/a") for a in r.calls[3][0])


def test_run_gh_raises_with_stderr(monkeypatch):
    import subprocess

    def fake(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom not found")
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(gh_mod.GhError, match="boom not found"):
        gh_mod.run_gh(["api", "x"])
