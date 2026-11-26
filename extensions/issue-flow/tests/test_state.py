from issue_flow.state import StateDir


def test_state_roundtrip(tmp_path):
    s = StateDir(tmp_path, 42)
    assert s.read("state") == {}
    s.write("state", {"a": 1, "b": [1, 2]})
    assert s.read("state") == {"a": 1, "b": [1, 2]}
    assert (tmp_path / ".specify/issue-flow/42/state.json").is_file()


def test_nested_name_creates_dirs(tmp_path):
    s = StateDir(tmp_path, 7)
    s.write("stories/12/state", {"x": 1})
    assert (tmp_path / ".specify/issue-flow/7/stories/12/state.json").is_file()
    assert s.read("stories/12/state") == {"x": 1}


# I9: state files must never be committed by a `git add -A`
def test_first_write_creates_gitignore(tmp_path):
    StateDir(tmp_path, 7).write("state", {"a": 1})
    gi = tmp_path / ".specify/issue-flow/.gitignore"
    assert gi.read_text() == "*\n"
    gi.write_text("*\n# kept\n")
    StateDir(tmp_path, 8).write("state", {})
    assert gi.read_text() == "*\n# kept\n"     # not rewritten once present


def test_gitignore_hides_state_from_git(project):
    import subprocess
    StateDir(project, 7).write("state", {"a": 1})
    (StateDir(project, 7).path / "issue.md").write_text("x")
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=project,
                         capture_output=True, text=True, check=True).stdout
    assert ".specify/issue-flow" not in out


def test_direct_file_writers_create_gitignore(project):
    import argparse
    from fakes import FakeRunner
    from issue_flow import cli
    from issue_flow.commands import review
    from issue_flow.gh import GitHub
    issue = {"number": 7, "title": "T", "body": "B", "labels": [{"name": "coding-agent"}]}
    runner = FakeRunner([(lambda a, i: "--paginate" in a, [[]]), (lambda a, i: True, issue)])
    ctx = cli.Context(project, dry_run=True, gh=GitHub("acme/widgets", runner=runner, dry_run=True))
    review.pull_issue(argparse.Namespace(epic=7), ctx)
    assert (project / ".specify/issue-flow/.gitignore").read_text() == "*\n"
