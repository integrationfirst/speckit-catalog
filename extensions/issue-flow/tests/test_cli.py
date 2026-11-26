import json

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
