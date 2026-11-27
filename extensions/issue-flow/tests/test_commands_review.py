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
    body = [a for a in posts(runner)[0] if a.startswith("body=")][0]
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
    body = posts(runner)[0][-1]
    assert "Document: https://wiki/p" in body and "hello" not in body


def test_request_truncates_large_artifact(project):
    ctx, runner = make_ctx(project, routes())
    review.request(req_args(_art(project, "x" * 100000)), ctx)
    body = posts(runner)[0][-1]
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


# ---------------------------------------------------------------- final review fixes
def _cli(capsys, *argv):
    code = cli.main(["--root", *argv])
    return code, json.loads(capsys.readouterr().out)


# I3: a dry run on a real-run state dir must refuse (agent steps without the env would be real)
def test_dry_env_on_real_state_refuses(project, capsys, monkeypatch):
    StateDir(project, 7).write("dry-run", {"dry_run": False})
    monkeypatch.setenv("ISSUE_FLOW_DRY_RUN", "true")
    code, out = _cli(capsys, str(project), "pull-issue", "--epic", "7")
    assert code == 2 and "remove .specify/issue-flow/7/" in out["error"]
    cli.register("t-any-epic", lambda a, ctx: {"ok": True}, lambda sp: sp.add_argument("--epic", type=int))
    code, out = _cli(capsys, str(project), "t-any-epic", "--epic", "7")
    assert code == 2 and "real run" in out["error"]
    monkeypatch.setenv("ISSUE_FLOW_DRY_RUN", "false")
    assert _cli(capsys, str(project), "t-any-epic", "--epic", "7") == (0, {"ok": True})


# I4: --artifact must stay inside the project
def test_request_rejects_artifact_outside_root(project):
    (project.parent / "secret").write_text("token")
    ctx, runner = make_ctx(project, routes())
    for bad in ("../secret", str(project.parent / "secret"), str(project / "specs/f/spec.md")):
        _art(project)
        with pytest.raises(cli.Refusal, match="artifact"):
            review.request(req_args(bad), ctx)
    (project / "link").symlink_to(project.parent / "secret")
    with pytest.raises(cli.Refusal, match="outside"):
        review.request(req_args("link"), ctx)
    assert not posts(runner)


# (a) the github_review stage is never decided by comments
def test_check_approval_refuses_github_review_stage(project):
    ctx, _ = make_ctx(project, routes())
    with pytest.raises(cli.Refusal, match="github_review"):
        review.check_approval(ns(epic=7, stage="pr", wait=False), ctx)


# I10: removing the trigger label stops the flow
def test_request_refuses_when_epic_label_removed(project):
    ctx, runner = make_ctx(project, routes(issue={**ISSUE, "labels": []}))
    with pytest.raises(cli.Refusal, match="coding-agent"):
        review.request(req_args(_art(project)), ctx)
    assert not posts(runner)


# I11: bot review requests and /approve|/reject commands are not pulled into issue.md
def test_pull_issue_skips_review_requests_and_commands(project):
    cs = [cm("bot", "### Review requested: spec — IF-spec-r1\n\nlots of spec text"),
          cm("po", "/approve IF-spec-r1"),
          cm("po", "  /REJECT IF-spec-r2 too vague\nmore"),
          cm("alice", "Please also support CSV export"),
          cm("bob", "I think /approve IF-spec-r1 is premature")]
    ctx, _ = make_ctx(project, routes(comments=cs))
    text = (project / review.pull_issue(ns(epic=7), ctx)["path"]).read_text()
    assert "CSV export" in text and "premature" in text
    assert "Review requested" not in text and "lots of spec text" not in text
    assert "/approve IF-spec-r1\n" not in text and "too vague" not in text


# C2: QA approval must not survive a code change
def _suites(project):
    import yaml
    from issue_flow import config as config_mod
    p = project / config_mod.CONFIG_REL
    cfg = yaml.safe_load(p.read_text())
    cfg["tests"] = {"max_fix_rounds": 5, "suites": [
        {"name": "u", "type": "unit", "command": "true", "required": True}]}
    p.write_text(yaml.safe_dump(cfg))


def _commit(project, name):
    import subprocess
    (project / name).write_text(name)
    subprocess.run(["git", "add", name], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", name], cwd=project, check=True)


def test_qa_approval_not_reused_after_code_change(project, monkeypatch):
    from issue_flow.commands import testrun
    _suites(project)
    rep = ".specify/issue-flow/7/stories/8/test-report.md"
    ts = iter(["2026-01-01T09:00:00Z", "2026-01-01T09:30:00Z"])
    monkeypatch.setattr(testrun, "now", lambda: next(ts))
    qa = lambda: req_args(rep, stage="qa", target=8)
    ctx, _ = make_ctx(project, routes())
    testrun.run_tests(ns(epic=7, story=8), ctx)
    r1 = review.request(qa(), ctx)
    assert r1["id"] == "IF-qa-r1"
    ok = [cm("qa_lead-user", "/approve IF-qa-r1")]
    ctx, _ = make_ctx(project, routes(comments=ok))
    assert review.check_approval(ns(epic=7, stage="qa", target=8, wait=False), ctx)["status"] == "approved"
    _commit(project, "code.py")                                   # code changes
    assert review.check_approval(ns(epic=7, stage="qa", target=8, wait=False), ctx)["status"] == "stale"
    testrun.run_tests(ns(epic=7, story=8), ctx)                   # identical suite results
    r2 = review.request(qa(), ctx)
    assert r2["id"] == "IF-qa-r2" and r2["reused"] is False
    assert review.check_approval(ns(epic=7, stage="qa", target=8, wait=False), ctx)["status"] == "pending"


def test_test_report_header_has_head_and_time(project, monkeypatch):
    import subprocess
    from issue_flow.commands import testrun
    _suites(project)
    ts = iter(["2026-01-01T09:00:00Z", "2026-01-01T09:30:00Z"])
    monkeypatch.setattr(testrun, "now", lambda: next(ts))
    ctx, _ = make_ctx(project, routes())
    p = project / testrun.run_tests(ns(epic=7, story=8), ctx)["report"]
    first = p.read_text()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True,
                          text=True).stdout.strip()
    assert f"Commit: `{head}`" in first and "Run at: 2026-01-01T09:00:00Z" in first
    testrun.run_tests(ns(epic=7, story=8), ctx)
    assert p.read_text() != first


# I6: assert-published
def ap(**kw):
    d = dict(epic=7, stage="spec", target=8)
    d.update(kw)
    return ns(**d)


def test_assert_published(project):
    art = _art(project)
    ctx, _ = make_ctx(project, routes())
    with pytest.raises(cli.Refusal, match="no spec request"):
        review.assert_published(ap(), ctx)
    review.request(req_args(art), ctx)
    out = review.assert_published(ap(), ctx)
    assert out["request_id"] == "IF-spec-r1" and out["published"] is True
    with pytest.raises(cli.Refusal, match="#9"):
        review.assert_published(ap(target=9), ctx)
    (project / art).write_text("edited after publishing")
    with pytest.raises(cli.Refusal, match="changed"):
        review.assert_published(ap(), ctx)
    (project / art).write_text("# Spec\nhello\n")
    rej = [cm("product_owner-user", "/reject IF-spec-r1 no")]
    ctx, _ = make_ctx(project, routes(comments=rej))
    with pytest.raises(cli.Refusal, match="rejected"):
        review.assert_published(ap(), ctx)


def test_assert_published_refuses_dry_record_in_real_run(project):
    art = _art(project)
    ctx, _ = make_ctx(project, routes(), dry=True)
    review.request(req_args(art), ctx)
    assert review.assert_published(ap(), ctx)["published"] is True
    ctx, _ = make_ctx(project, routes())
    with pytest.raises(cli.Refusal, match="never posted"):
        review.assert_published(ap(), ctx)


def test_assert_published_cli_exit_2(project, capsys, monkeypatch):
    monkeypatch.delenv("ISSUE_FLOW_DRY_RUN", raising=False)
    code, out = _cli(capsys, str(project), "assert-published", "--epic", "7", "--stage", "qa",
                     "--target", "8")
    assert code == 2 and "no qa request" in out["error"]


# Gap 3: init-mode records the step env's mode so a standalone stage run stays dry
def test_init_mode_records_dry_run(project):
    ctx, runner = make_ctx(project, routes(), dry=True)
    ctx.env_dry_run = True
    assert review.init_mode(ns(epic=7), ctx) == {"epic": 7, "dry_run": True}
    assert StateDir(project, 7).read("dry-run") == {"dry_run": True}
    assert runner.calls == []


def test_init_mode_noop_when_recorded(project):
    StateDir(project, 7).write("dry-run", {"dry_run": False})
    ctx, _ = make_ctx(project, routes())
    assert review.init_mode(ns(epic=7), ctx) == {"epic": 7, "dry_run": False}


def test_init_mode_refuses_mismatch(project):
    StateDir(project, 7).write("dry-run", {"dry_run": True})
    ctx, _ = make_ctx(project, routes())          # env not dry
    with pytest.raises(cli.Refusal, match="dry run"):
        review.init_mode(ns(epic=7), ctx)


def test_init_mode_via_main_with_env(project, monkeypatch, capsys):
    monkeypatch.setenv("ISSUE_FLOW_DRY_RUN", "true")
    assert cli.main(["--root", str(project), "init-mode", "--epic", "7"]) == 0
    assert json.loads(capsys.readouterr().out) == {"epic": 7, "dry_run": True}
    monkeypatch.delenv("ISSUE_FLOW_DRY_RUN")
    # a later real-mode call on the dry state refuses (exit 2)
    assert cli.main(["--root", str(project), "init-mode", "--epic", "7"]) == 2


# Gap 1: the publish agent records page ids through the CLI
def test_set_page_records_page_and_keeps_state(project):
    StateDir(project, 7).write("state", {"feature_dir": "specs/x", "pages": {"spec": "1"}})
    ctx, _ = make_ctx(project, routes())
    out = review.set_page(argparse.Namespace(epic=7, key="plan", page_id="42"), ctx)
    assert out["pages"] == {"spec": "1", "plan": "42"}
    assert StateDir(project, 7).read("state") == {"feature_dir": "specs/x",
                                                  "pages": {"spec": "1", "plan": "42"}}


@pytest.mark.parametrize("pid", ["", "12a", "../x", "1 2", "-1"])
def test_set_page_rejects_non_numeric(project, pid):
    ctx, _ = make_ctx(project, routes())
    with pytest.raises(cli.Refusal):
        review.set_page(argparse.Namespace(epic=7, key="spec", page_id=pid), ctx)


def test_set_page_rejects_unknown_key(project, capsys):
    assert cli.main(["--root", str(project), "set-page", "--epic", "7", "--key", "feature_dir",
                     "--page-id", "1"]) == 2


# Review 0.1.1 #7: the mode is recorded before the publish agent runs
def test_stage_workflow_records_mode_before_publish():
    import yaml
    from conftest import EXT
    wf = yaml.safe_load((EXT.parent.parent / "workflows/issue-flow-stage/workflow.yml").read_text())
    ids = [s["id"] for s in wf["steps"]]
    assert ids.index("stage_mode") < ids.index("stage_publish")
    assert "init-mode" in wf["steps"][ids.index("stage_mode")]["run"]
