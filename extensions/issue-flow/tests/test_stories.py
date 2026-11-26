import argparse
import json
from pathlib import Path

import pytest

from fakes import FakeRunner
from issue_flow import cli
from issue_flow.approvals import content_hash
from issue_flow.commands import stories as cmd
from issue_flow.gh import GitHub
from issue_flow.state import StateDir
from issue_flow.stories import parse_tasks, plan_stories

FIX = Path(__file__).parent / "fixtures" / "tasks_template.md"
EPIC = 7


def ns(**kw):
    return argparse.Namespace(**kw)


class FakeGH:
    def __init__(self, labels=("coding-agent",)):
        self.created, self.subs, self.n = [], [], 100
        self.labels = list(labels)

    def issue(self, n):
        return {"number": n, "labels": self.labels}

    def create_issue(self, title, body, labels):
        self.n += 1
        self.created.append((title, body, labels, self.n))
        return self.n

    def add_sub_issue(self, p, c):
        self.subs.append((p, c))


def make_ctx(project, gh=None, dry=False):
    return cli.Context(project, dry_run=dry, gh=gh or FakeGH())


def setup_feature(project, md=None):
    fd = project / "specs" / "001-x"
    fd.mkdir(parents=True)
    (fd / "tasks.md").write_text(md if md is not None else FIX.read_text())
    st = StateDir(project, EPIC)
    st.write("state", {"feature_dir": "specs/001-x"})
    return fd


def approve(project, ctx):
    cmd.breakdown(ns(epic=EPIC, tasks=None), ctx)
    st = StateDir(project, EPIC)
    paths = [project / "specs/001-x/tasks.md", st.path / "breakdown.md"]
    h = content_hash(paths, base=project)
    st.write("requests", {"tasks": [{"id": "IF-tasks-r1", "hash": h}]})
    st.write("tasks", {"status": "approved", "request_id": "IF-tasks-r1"})


def test_parse_template_phases():
    ph = parse_tasks(FIX.read_text())
    assert [p.kind for p in ph] == ["setup", "foundational", "user_story", "user_story", "polish"]
    assert len(ph) == 5
    assert ph[0].tasks[0].id == "T001" and ph[0].tasks[0].done
    assert not ph[0].tasks[1].done
    assert ph[3].tasks[1].done  # [X]


def test_plan_folds_setup_and_polish():
    plans = plan_stories(parse_tasks(FIX.read_text()))
    assert len(plans) == 2
    assert plans[0].tasks[0].id == "T001"
    assert [t.id for t in plans[1].tasks][-2:] == ["T030", "T031"]
    assert plans[0].title.startswith("User Story 1")


def test_no_user_story_phase_refused():
    md = "## Phase 1: Setup\n\n- [ ] T001 a\n"
    with pytest.raises(cli.Refusal, match='no "User Story" phase'):
        plan_stories(parse_tasks(md))


def test_task_outside_phase_refused():
    with pytest.raises(cli.Refusal, match="outside"):
        parse_tasks("- [ ] T001 a\n## Phase 1: User Story 1 - x\n")


def test_breakdown_writes_file(project):
    setup_feature(project)
    r = cmd.breakdown(ns(epic=EPIC, tasks=None), make_ctx(project))
    assert len(r["stories"]) == 2 and r["stories"][0]["task_ids"][0] == "T001"
    assert (StateDir(project, EPIC).path / "breakdown.md").is_file()


def test_create_stories_requires_tasks_approval(project):
    setup_feature(project)
    ctx = make_ctx(project)
    cmd.breakdown(ns(epic=EPIC, tasks=None), ctx)
    with pytest.raises(cli.Refusal, match="approved"):
        cmd.create_stories(ns(epic=EPIC), ctx)
    assert ctx.gh.created == []


def test_create_stories_refuses_after_change(project):
    fd = setup_feature(project)
    ctx = make_ctx(project)
    approve(project, ctx)
    (fd / "tasks.md").write_text(FIX.read_text() + "\n- [ ] T099 extra\n")
    with pytest.raises(cli.Refusal, match="changed"):
        cmd.create_stories(ns(epic=EPIC), ctx)


def test_create_stories_idempotent(project):
    setup_feature(project)
    ctx = make_ctx(project)
    approve(project, ctx)
    r = cmd.create_stories(ns(epic=EPIC), ctx)
    assert len(ctx.gh.created) == 2 and len(ctx.gh.subs) == 2
    t, body, labels, _ = ctx.gh.created[0]
    assert labels == ["coding-agent"]
    assert "Part of #7" in body and "- [x] T001 Create project" in body
    assert "<!-- issue-flow:story epic=7 index=0 -->" in body
    assert [s["number"] for s in r["stories"]] == [101, 102]
    cmd.create_stories(ns(epic=EPIC), ctx)
    assert len(ctx.gh.created) == 2


def test_dry_run_does_not_persist_fake_numbers(project):
    setup_feature(project)
    runner = FakeRunner([(lambda a, i: a[-1] == "repos/a/b/issues/7",
                          {"number": 7, "labels": [{"name": "coding-agent"}]}),
                         (lambda a, i: True, "")])
    ctx = make_ctx(project, GitHub("a/b", runner=runner, dry_run=True), dry=True)
    approve(project, ctx)
    r = cmd.create_stories(ns(epic=EPIC), ctx)
    assert all(s["number"] is None and s["dry_run"] for s in r["stories"])
    assert StateDir(project, EPIC).read("issue-mapping") == {}
    ctx2 = make_ctx(project)
    r2 = cmd.create_stories(ns(epic=EPIC), ctx2)
    assert len(ctx2.gh.created) == 2 and r2["stories"][0]["number"] == 101


def test_next_story_skips_done_and_set_status(project):
    setup_feature(project)
    ctx = make_ctx(project)
    approve(project, ctx)
    cmd.create_stories(ns(epic=EPIC), ctx)
    assert cmd.next_story(ns(epic=EPIC), ctx)["story"] == 101
    cmd.set_story_status(ns(epic=EPIC, story=101, status="done"), ctx)
    n = cmd.next_story(ns(epic=EPIC), ctx)
    assert n["story"] == 102 and n["index"] == 1
    cmd.set_story_status(ns(epic=EPIC, story=102, status="done"), ctx)
    assert cmd.next_story(ns(epic=EPIC), ctx)["story"] is None


def test_set_status_refusals(project):
    setup_feature(project)
    ctx = make_ctx(project)
    approve(project, ctx)
    cmd.create_stories(ns(epic=EPIC), ctx)
    with pytest.raises(cli.Refusal, match="unknown story"):
        cmd.set_story_status(ns(epic=EPIC, story=999, status="done"), ctx)
    with pytest.raises(cli.Refusal, match="status"):
        cmd.set_story_status(ns(epic=EPIC, story=101, status="bogus"), ctx)


def test_link_failure_does_not_duplicate(project):
    setup_feature(project)
    ctx = make_ctx(project)
    approve(project, ctx)

    def boom(p, c):
        raise RuntimeError("link failed")
    ctx.gh.add_sub_issue = boom
    with pytest.raises(RuntimeError):
        cmd.create_stories(ns(epic=EPIC), ctx)
    m = StateDir(project, EPIC).read("issue-mapping")
    assert m["stories"][0]["number"] == 101 and m["stories"][0]["linked"] is False
    ctx2 = make_ctx(project)
    cmd.create_stories(ns(epic=EPIC), ctx2)
    assert [c[3] for c in ctx2.gh.created] == [101]  # only story 2 created (fresh fake counter)
    assert (EPIC, 101) in ctx2.gh.subs
    assert all(s["linked"] for s in StateDir(project, EPIC).read("issue-mapping")["stories"])


def test_handedited_breakdown_refused(project):
    setup_feature(project)
    ctx = make_ctx(project)
    cmd.breakdown(ns(epic=EPIC, tasks=None), ctx)
    st = StateDir(project, EPIC)
    bd = st.path / "breakdown.md"
    bd.write_text(bd.read_text() + "\n- [ ] T999 sneaky\n")
    h = content_hash([project / "specs/001-x/tasks.md", bd], base=project)
    st.write("requests", {"tasks": [{"id": "IF-tasks-r1", "hash": h}]})
    st.write("tasks", {"status": "approved", "request_id": "IF-tasks-r1"})
    with pytest.raises(cli.Refusal, match="does not match tasks.md"):
        cmd.create_stories(ns(epic=EPIC), ctx)
    assert ctx.gh.created == []


# (c) the approval must be for the LATEST tasks request
def test_create_stories_refuses_approval_of_older_request(project):
    setup_feature(project)
    ctx = make_ctx(project)
    approve(project, ctx)
    st = StateDir(project, EPIC)
    reqs = st.read("requests")
    reqs["tasks"].append({**reqs["tasks"][0], "id": "IF-tasks-r2"})
    st.write("requests", reqs)
    with pytest.raises(cli.Refusal, match="IF-tasks-r2"):
        cmd.create_stories(ns(epic=EPIC), ctx)
    assert ctx.gh.created == []


# I10: removing the trigger label stops the flow
def test_create_stories_refuses_when_label_removed(project):
    setup_feature(project)
    ctx = make_ctx(project, FakeGH(labels=()))
    approve(project, ctx)
    with pytest.raises(cli.Refusal, match="coding-agent"):
        cmd.create_stories(ns(epic=EPIC), ctx)
    assert ctx.gh.created == []
