import argparse
import json
import time

import pytest
import yaml

from issue_flow import cli, config as config_mod
from issue_flow.commands import testrun as cmd
from issue_flow.state import StateDir

EPIC, STORY = 7, 12


def ns(**kw):
    return argparse.Namespace(epic=EPIC, story=STORY, **kw)


def make_ctx(project, suites, max_rounds=5):
    p = project / config_mod.CONFIG_REL
    cfg = yaml.safe_load(p.read_text())
    cfg["tests"] = {"max_fix_rounds": max_rounds, "suites": suites}
    p.write_text(yaml.safe_dump(cfg))
    return cli.Context(project)


def suite(name, command, required=True, timeout=30):
    return {"name": name, "type": "unit", "command": command, "required": required,
            "timeout_seconds": timeout}


def test_all_pass(project):
    ctx = make_ctx(project, [suite("a", "true"), suite("b", "true")])
    r = cmd.run_tests(ns(), ctx)
    assert r["passed"] is True and r["round"] == 1 and r["failed"] == []
    assert r["max_rounds"] == 5 and r["exhausted"] is False
    assert (project / r["report"]).is_file()
    assert r["report"] == f".specify/issue-flow/{EPIC}/stories/{STORY}/test-report.md"
    st = StateDir(project, EPIC).read(f"stories/{STORY}/state")
    assert st["round"] == 1 and st["last_result"]["passed"] is True


def test_required_failure_fails(project, capfd):
    ctx = make_ctx(project, [suite("ok", "true"), suite("bad", "echo boom-output; exit 3")])
    r = cmd.run_tests(ns(), ctx)
    assert r["passed"] is False and r["failed"] == ["bad"] and r["exhausted"] is False
    assert capfd.readouterr().out == ""  # suite output must not leak to stdout
    text = (project / r["report"]).read_text()
    assert "# Test report — story #12 — round 1/5" in text
    assert "| Suite | Type | Required | Result | Duration |" in text
    assert "### bad" in text and "boom-output" in text


def test_optional_failure_reported_not_blocking(project):
    ctx = make_ctx(project, [suite("ok", "true"), suite("opt", "exit 1", required=False)])
    r = cmd.run_tests(ns(), ctx)
    assert r["passed"] is True and r["failed"] == []
    assert "### opt" in (project / r["report"]).read_text()


def test_exhausted_at_max_rounds(project):
    ctx = make_ctx(project, [suite("bad", "exit 1")], max_rounds=2)
    r1 = cmd.run_tests(ns(), ctx)
    assert r1["round"] == 1 and r1["exhausted"] is False
    r2 = cmd.run_tests(ns(), ctx)
    assert r2["round"] == 2 and r2["exhausted"] is True


def test_reset_rounds(project):
    ctx = make_ctx(project, [suite("bad", "exit 1")])
    cmd.run_tests(ns(), ctx)
    assert cmd.reset_rounds(ns(), ctx) == {"story": STORY, "round": 0}
    assert StateDir(project, EPIC).read(f"stories/{STORY}/state")["round"] == 0
    assert cmd.run_tests(ns(), ctx)["round"] == 1


def test_no_suites_refused(project):
    ctx = make_ctx(project, [])
    with pytest.raises(cli.Refusal):
        cmd.run_tests(ns(), ctx)


def test_timeout_marks_failed(project):
    ctx = make_ctx(project, [suite("slow", "sleep 5", timeout=1)])
    t = time.monotonic()
    r = cmd.run_tests(ns(), ctx)
    assert time.monotonic() - t < 4
    assert r["passed"] is False and r["failed"] == ["slow"]
    assert "timed out after 1s" in (project / r["report"]).read_text()


def test_report_keeps_last_80_lines(project):
    ctx = make_ctx(project, [suite("noisy", "seq 1 200; exit 1")])
    text = (project / cmd.run_tests(ns(), ctx)["report"]).read_text()
    assert "\n200\n" in text and "\n121\n" in text and "\n120\n" not in text


def test_cli_one_json_object(project, capfd):
    make_ctx(project, [suite("a", "echo noise")])
    code = cli.main(["--root", str(project), "run-tests", "--epic", "7", "--story", "12"])
    out = capfd.readouterr().out
    assert code == 0 and json.loads(out)["passed"] is True


def test_non_utf8_output_is_normal_failure(project):
    ctx = make_ctx(project, [suite("bin", "printf '\\377\\376 bad'; exit 1")])
    r = cmd.run_tests(ns(), ctx)
    assert r["passed"] is False and r["round"] == 1
    assert (project / r["report"]).is_file()


def test_stdin_is_devnull(project):
    ctx = make_ctx(project, [suite("rd", "cat", timeout=30)])
    t = time.monotonic()
    r = cmd.run_tests(ns(), ctx)
    assert time.monotonic() - t < 5 and r["passed"] is True
