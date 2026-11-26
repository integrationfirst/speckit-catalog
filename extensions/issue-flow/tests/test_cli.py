import json

from issue_flow import cli
from issue_flow.config import ConfigError


def _run(capsys, argv):
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_success_and_ctx(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_FLOW_DRY_RUN", "1")
    cli.register("t-ok", lambda a, ctx: {"root": str(ctx.root), "dry": ctx.dry_run, "gh": ctx.gh})
    code, out = _run(capsys, ["--root", str(tmp_path), "t-ok"])
    assert code == 0 and out == {"root": str(tmp_path), "dry": True, "gh": None}


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
