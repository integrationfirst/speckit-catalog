import json

import pytest

from issue_flow import cli
from issue_flow.config import ConfigError


def _run(capsys, argv):
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_success_and_ctx(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_FLOW_DRY_RUN", "1")
    cli.register("t-ok", lambda a, ctx: {"root": str(ctx.root), "dry": ctx.dry_run})
    code, out = _run(capsys, ["--root", str(tmp_path), "t-ok"])
    assert code == 0 and out == {"root": str(tmp_path), "dry": True}


@pytest.mark.parametrize("value,dry", [("1", True), ("true", True), ("TRUE", True), ("yes", True),
                                       ("0", False), ("false", False), ("", False)])
def test_dry_run_env_values(capsys, tmp_path, monkeypatch, value, dry):
    # workflows export ISSUE_FLOW_DRY_RUN="{{ inputs.dry_run | to_json }}" -> "true"/"false"
    monkeypatch.setenv("ISSUE_FLOW_DRY_RUN", value)
    cli.register("t-dry", lambda a, ctx: {"dry": ctx.dry_run})
    assert _run(capsys, ["--root", str(tmp_path), "t-dry"]) == (0, {"dry": dry})


def test_refusal_and_config_error_exit_2(capsys, tmp_path):
    def refuse(a, ctx): raise cli.Refusal("nope")
    cli.register("t-refuse", refuse)
    assert _run(capsys, ["t-refuse"]) == (2, {"error": "nope"})
    cli.register("t-cfg", lambda a, ctx: ctx.config)  # lazy load -> ConfigError (no file)
    code, out = _run(capsys, ["--root", str(tmp_path), "t-cfg"])
    assert code == 2 and "error" in out


def test_unexpected_exit_1(capsys):
    def boom(a, ctx): raise RuntimeError("bang")
    cli.register("t-boom", boom)
    code, out = _run(capsys, ["t-boom"])
    assert code == 1 and "bang" in out["error"]


import os
import subprocess
import sys
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent


def _entry(tmp_path, *args):
    (tmp_path / "plug_cmd.py").write_text(
        "from issue_flow import cli\n"
        "def ok(a, ctx): return {'ok': True}\n"
        "def bad(a, ctx): raise cli.Refusal('denied')\n"
        "cli.register('plug-ok', ok)\n"
        "cli.register('plug-bad', bad)\n")
    env = dict(os.environ, PYTHONPATH=f"{EXT / 'scripts'}{os.pathsep}{tmp_path}",
               ISSUE_FLOW_COMMAND_MODULES="plug_cmd")
    return subprocess.run([sys.executable, "-m", "issue_flow", *args],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def test_entry_registers_external_command(tmp_path):
    r = _entry(tmp_path, "plug-ok")
    assert r.returncode == 0 and json.loads(r.stdout) == {"ok": True}


def test_entry_refusal_exit_2(tmp_path):
    r = _entry(tmp_path, "plug-bad")
    assert r.returncode == 2 and json.loads(r.stdout) == {"error": "denied"}


def test_help_goes_to_stderr(tmp_path):
    r = _entry(tmp_path, "--help")
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"help": True}
    assert "usage" in r.stderr


def test_ctx_gh_lazy_built_from_config(project):
    ctx = cli.Context(project, dry_run=True)
    assert ctx.gh.repo == "acme/widgets" and ctx.gh.dry_run is True
    assert ctx.gh is ctx.gh


def test_ctx_gh_injectable(tmp_path):
    sentinel = object()
    assert cli.Context(tmp_path, gh=sentinel).gh is sentinel


# R19: dry-run is sticky per epic via state.json, independent of env reaching agent steps
def _no_gh(monkeypatch):
    from issue_flow import gh as gh_mod

    def boom(args, input=None):
        # reads (the Epic label check, comments) still happen in a dry run; writes must not
        if args[:1] == ["api"] and "-X" not in args:
            if args[-1].endswith("/comments"):
                return "[]"
            return json.dumps({"number": 7, "title": "t", "labels": [{"name": "coding-agent"}]})
        raise AssertionError("gh writes must not be called in dry-run")
    monkeypatch.setattr(gh_mod, "run_gh", boom)
    monkeypatch.setattr(gh_mod.GitHub.__init__, "__defaults__", (boom, False))


def test_state_dry_run_makes_request_post_nothing(capsys, project, monkeypatch):
    from issue_flow.state import StateDir
    monkeypatch.delenv("ISSUE_FLOW_DRY_RUN", raising=False)
    _no_gh(monkeypatch)
    StateDir(project, 7).write("dry-run", {"dry_run": True})
    art = project / "specs/f/spec.md"
    art.parent.mkdir(parents=True)
    art.write_text("# Spec\n")
    code = cli.main(["--root", str(project), "request", "--epic", "7", "--stage", "spec",
                     "--target", "7", "--artifact", "specs/f/spec.md"])
    cap = capsys.readouterr()
    assert code == 0 and json.loads(cap.out)["comment_id"] == 0
    assert "DRY-RUN gh api -X POST repos/acme/widgets/issues/7/comments" in cap.err


def test_state_without_dry_run_is_not_dry(capsys, project, monkeypatch):
    from issue_flow.state import StateDir
    monkeypatch.delenv("ISSUE_FLOW_DRY_RUN", raising=False)
    StateDir(project, 7).write("dry-run", {"dry_run": False})
    cli.register("t-dry2", lambda a, ctx: {"dry": ctx.dry_run}, lambda sp: sp.add_argument("--epic", type=int))
    assert _run(capsys, ["--root", str(project), "t-dry2", "--epic", "7"]) == (0, {"dry": False})
    StateDir(project, 8).write("dry-run", {"dry_run": True})
    assert _run(capsys, ["--root", str(project), "t-dry2", "--epic", "8"]) == (0, {"dry": True})


def test_dry_run_survives_state_json_rewrite(capsys, project, monkeypatch):
    # an agent rewriting state.json (e.g. pages[...]) must not drop the dry-run flag
    from issue_flow.state import StateDir
    monkeypatch.delenv("ISSUE_FLOW_DRY_RUN", raising=False)
    StateDir(project, 7).write("dry-run", {"dry_run": True})
    StateDir(project, 7).write("state", {"pages": {"spec": "1"}})
    cli.register("t-dry3", lambda a, ctx: {"dry": ctx.dry_run}, lambda sp: sp.add_argument("--epic", type=int))
    assert _run(capsys, ["--root", str(project), "t-dry3", "--epic", "7"]) == (0, {"dry": True})


# Review 0.1.1 #2: agents may run only request/set-page/pull-issue
@pytest.mark.parametrize("argv", [
    ["set-story-status", "--epic", "7", "--story", "11", "--status", "done"],
    ["open-pr", "--epic", "7", "--story", "11"],
    ["reset-rounds", "--epic", "7", "--story", "11"],
    ["init-mode", "--epic", "7"],
    ["record-feature", "--epic", "7"],
])
def test_state_changing_commands_need_workflow_step_env(tmp_path, monkeypatch, capsys, argv):
    monkeypatch.delenv("ISSUE_FLOW_STEP")
    assert cli.main(["--root", str(tmp_path), *argv]) == 2
    assert "workflow shell step" in json.loads(capsys.readouterr().out)["error"]
    assert not (tmp_path / ".specify").exists()


def test_agent_commands_allowed_without_step_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ISSUE_FLOW_STEP")
    assert cli.main(["--root", str(tmp_path), "set-page", "--epic", "7", "--key", "spec",
                     "--page-id", "5"]) == 0
