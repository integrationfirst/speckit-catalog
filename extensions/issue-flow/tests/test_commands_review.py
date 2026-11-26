import argparse
import json

import pytest

from fakes import FakeRunner
from issue_flow import cli
from issue_flow.commands import review
from issue_flow.gh import GitHub
from issue_flow.state import StateDir

ISSUE = {"number": 7, "title": "Add widgets", "body": "Please", "labels": [{"name": "coding-agent"}],
         "state": "open", "html_url": "https://github.com/acme/widgets/issues/7"}


def make_ctx(project, responses=None, dry=False):
    runner = FakeRunner(responses)
    ctx = cli.Context(project, dry_run=dry, gh=GitHub("acme/widgets", runner=runner, dry_run=dry))
    return ctx, runner


def ns(**kw):
    return argparse.Namespace(**kw)


def posts(runner):
    return [a for a, _ in runner.calls if "POST" in a]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(review, "now", lambda: "2026-01-01T10:00:00Z")


def routes(issue=ISSUE, comments=()):
    def issue_p(a, i): return a[-1].endswith("/issues/7")
    def comments_get(a, i): return "--paginate" in a
    def post(a, i): return "POST" in a
    return [(post, {"id": 555}), (comments_get, [list(comments)]), (issue_p, issue)]


def test_pull_issue_refuses_without_label(project):
    ctx, _ = make_ctx(project, routes(issue={**ISSUE, "labels": []}))
    with pytest.raises(cli.Refusal, match="coding-agent"):
        review.pull_issue(ns(epic=7), ctx)


def test_pull_issue_writes_issue_md(project):
    cm = {"id": 1, "user": {"login": "bob"}, "body": "hi", "created_at": "t1", "updated_at": "t1"}
    ctx, _ = make_ctx(project, routes(comments=[cm]))
    out = review.pull_issue(ns(epic=7), ctx)
    assert out == {"epic": 7, "title": "Add widgets", "path": ".specify/issue-flow/7/issue.md"}
    text = (project / out["path"]).read_text()
    assert text.startswith("# Add widgets") and "Source: https://github.com/acme/widgets/issues/7" in text
    assert "## Comments" in text and "**@bob** (t1):" in text and "Please" in text


def _art(project, text="# Spec\nhello\n"):
    p = project / "specs" / "f" / "spec.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return "specs/f/spec.md"


def req_args(art, **kw):
    d = dict(epic=7, stage="spec", target=8, artifact=[art], page_url=None, summary="Spec ready")
    d.update(kw)
    return ns(**d)


def test_request_posts_comment_with_mentions_and_commands(project):
    ctx, runner = make_ctx(project, routes())
    out = review.request(req_args(_art(project)), ctx)
    assert out["id"] == "IF-spec-r1" and out["comment_id"] == 555 and out["posted_at"] == "2026-01-01T10:00:00Z"
    body = [a for a in runner.calls[0][0] if a.startswith("body=")][0]
    first = body.split("\n")[0]
    assert first == "body=### Review requested: spec — IF-spec-r1"
    for s in ("@product_owner-user", "@business_analyst-user", "@solution_architect-user",
              "/approve IF-spec-r1", "/reject IF-spec-r1 <reason>", "# Spec", "Spec ready"):
        assert s in body
    saved = StateDir(project, 7).read("requests")["spec"][0]
    assert saved["target"] == 8 and saved["artifacts"] == ["specs/f/spec.md"] and saved["hash"]


def test_request_page_url_instead_of_inline(project):
    ctx, runner = make_ctx(project, routes())
    review.request(req_args(_art(project), page_url="https://wiki/p"), ctx)
    body = runner.calls[0][0][-1]
    assert "Document: https://wiki/p" in body and "hello" not in body


def test_request_truncates_large_artifact(project):
    ctx, runner = make_ctx(project, routes())
    review.request(req_args(_art(project, "x" * 100000)), ctx)
    body = runner.calls[0][0][-1]
    assert len(body) <= 60000 and "(truncated — full file: specs/f/spec.md)" in body
    assert "/approve IF-spec-r1" in body


def test_request_same_hash_is_idempotent(project):
    ctx, runner = make_ctx(project, routes())
    art = _art(project)
    first = review.request(req_args(art), ctx)
    n = len(posts(runner))
    second = review.request(req_args(art), ctx)
    assert len(posts(runner)) == n == 1 and second["id"] == first["id"]
    (project / art).write_text("changed")
    third = review.request(req_args(art), ctx)
    assert third["id"] == "IF-spec-r2" and len(posts(runner)) == 2


def test_request_dry_run_still_records(project):
    ctx, runner = make_ctx(project, routes(), dry=True)
    out = review.request(req_args(_art(project)), ctx)
    assert out["comment_id"] == 0 and StateDir(project, 7).read("requests")["spec"][0]["id"] == "IF-spec-r1"


def cm(login, body, t="2026-01-01T11:00:00Z"):
    return {"id": 9, "user": {"login": login}, "body": body, "created_at": t, "updated_at": t}


def test_check_approval_flow(project, capsys):
    art = _art(project)
    ctx, _ = make_ctx(project, routes())
    review.request(req_args(art), ctx)
    cs = [cm(f"{r}-user", "/approve IF-spec-r1") for r in ("product_owner", "business_analyst")]
    ctx, _ = make_ctx(project, routes(comments=cs))
    out = review.check_approval(ns(epic=7, stage="spec", wait=False), ctx)
    assert out["status"] == "pending" and out["waiting_for"] == ["solution_architect"]
    assert StateDir(project, 7).read("spec")["status"] == "pending"
    with pytest.raises(cli.Waiting) as e:
        review.check_approval(ns(epic=7, stage="spec", wait=True), ctx)
    assert e.value.result["status"] == "pending"
    msg = str(e.value)
    assert msg == ("waiting for solution_architect (@solution_architect-user) on #8 — "
                   "comment /approve IF-spec-r1")
    cs.append(cm("solution_architect-user", "/approve IF-spec-r1"))
    ctx, _ = make_ctx(project, routes(comments=cs))
    assert review.check_approval(ns(epic=7, stage="spec", wait=True), ctx)["status"] == "approved"
    (project / art).write_text("edited")
    assert review.check_approval(ns(epic=7, stage="spec", wait=True), ctx)["status"] == "stale"


def test_check_approval_without_request_refuses(project):
    ctx, _ = make_ctx(project, routes())
    with pytest.raises(cli.Refusal):
        review.check_approval(ns(epic=7, stage="plan", wait=False), ctx)


def test_wait_exit_code_3_single_json(project, capsys):
    cli.register("t-wait", lambda a, ctx: (_ for _ in ()).throw(
        cli.Waiting({"status": "pending"}, "waiting for x")))
    code = cli.main(["t-wait"])
    cap = capsys.readouterr()
    assert code == 3 and json.loads(cap.out) == {"status": "pending"} and "waiting for x" in cap.err


def test_record_feature(project, monkeypatch):
    monkeypatch.setattr(review, "_git_branch", lambda root: "007-widgets")
    ctx, _ = make_ctx(project)
    with pytest.raises(cli.Refusal):
        review.record_feature(ns(epic=7), ctx)
    (project / "specs" / "007-widgets").mkdir(parents=True)
    out = review.record_feature(ns(epic=7), ctx)
    assert out == {"feature_branch": "007-widgets", "feature_dir": "specs/007-widgets"}
    st = StateDir(project, 7).read("state")
    assert st["feature_branch"] == "007-widgets" and st["feature_dir"] == "specs/007-widgets"


def test_dry_run_request_does_not_block_real_request(project):
    art = _art(project)
    ctx, _ = make_ctx(project, routes(), dry=True)
    assert review.request(req_args(art), ctx)["comment_id"] == 0
    ctx, runner = make_ctx(project, routes())
    out = review.request(req_args(art), ctx)
    assert len(posts(runner)) == 1 and out["id"] == "IF-spec-r2" and out["comment_id"] == 555
    assert [r["id"] for r in StateDir(project, 7).read("requests")["spec"]] == ["IF-spec-r1", "IF-spec-r2"]


def test_rejected_unchanged_refuses_changed_reposts_pending_reused(project):
    art = _art(project)
    ctx, _ = make_ctx(project, routes())
    review.request(req_args(art), ctx)
    # (c) pending + unchanged -> reused
    assert review.request(req_args(art), ctx)["reused"] is True
    rej = [cm("product_owner-user", "/reject IF-spec-r1 not good")]
    ctx, runner = make_ctx(project, routes(comments=rej))
    # (a) rejected + unchanged -> refusal
    with pytest.raises(cli.Refusal, match="artifact unchanged since rejected IF-spec-r1; "
                                          "revise it before re-requesting"):
        review.request(req_args(art), ctx)
    assert not posts(runner)
    # (b) rejected + changed -> new request posted
    (project / art).write_text("revised")
    out = review.request(req_args(art), ctx)
    assert out["id"] == "IF-spec-r2" and len(posts(runner)) == 1


# R19: pull-issue records dry_run and refuses a real run on a dry-run state dir
def test_pull_issue_records_dry_run(project):
    ctx, runner = make_ctx(project, routes(), dry=True)
    review.pull_issue(ns(epic=7), ctx)
    assert StateDir(project, 7).read("dry-run") == {"dry_run": True}
    assert "dry_run" not in StateDir(project, 7).read("state")
    assert posts(runner) == []


def test_pull_issue_records_real_run(project):
    ctx, _ = make_ctx(project, routes())
    review.pull_issue(ns(epic=7), ctx)
    assert StateDir(project, 7).read("dry-run") == {"dry_run": False}


def test_pull_issue_refuses_real_run_on_dry_state(project):
    StateDir(project, 7).write("dry-run", {"dry_run": True})
    ctx, _ = make_ctx(project, routes())          # env not dry
    ctx.dry_run = True                            # as main() derives it from state
    with pytest.raises(cli.Refusal, match=r"remove \.specify/issue-flow/7/"):
        review.pull_issue(ns(epic=7), ctx)


def test_pull_issue_dry_env_on_dry_state_ok(project):
    StateDir(project, 7).write("dry-run", {"dry_run": True})
    ctx, _ = make_ctx(project, routes(), dry=True)
    review.pull_issue(ns(epic=7), ctx)
    assert StateDir(project, 7).read("dry-run") == {"dry_run": True}


# Review fix 1: a QA approval for one Story must never satisfy another Story's wait
def test_check_approval_target_mismatch_is_pending(project):
    art = _art(project)
    ctx, _ = make_ctx(project, routes())
    review.request(req_args(art, stage="qa", target=8), ctx)       # Story #8's QA request
    ctx, _ = make_ctx(project, routes(comments=[cm("qa_lead-user", "/approve IF-qa-r1")]))
    assert review.check_approval(ns(epic=7, stage="qa", wait=False, target=8), ctx)["status"] == "approved"
    out = review.check_approval(ns(epic=7, stage="qa", wait=False, target=9), ctx)
    assert out["status"] == "pending" and out["request_id"] is None and out["target"] == 9
    assert StateDir(project, 7).read("qa")["status"] == "pending"
    with pytest.raises(cli.Waiting, match=r"no qa request for #9 yet \(latest IF-qa-r1 is for #8\)"):
        review.check_approval(ns(epic=7, stage="qa", wait=True, target=9), ctx)


def test_check_approval_target_mismatch_exit_3(project, capsys, monkeypatch):
    art = _art(project)
    ctx, _ = make_ctx(project, routes())
    review.request(req_args(art, stage="qa", target=8), ctx)
    monkeypatch.delenv("ISSUE_FLOW_DRY_RUN", raising=False)
    code = cli.main(["--root", str(project), "check-approval", "--epic", "7", "--stage", "qa",
                     "--target", "9", "--wait"])      # mismatch is decided before any gh read
    cap = capsys.readouterr()
    assert code == 3 and json.loads(cap.out)["status"] == "pending" and "#9" in cap.err
