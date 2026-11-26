"""run-tests, reset-rounds."""
from __future__ import annotations

from pathlib import Path

from issue_flow import cli
from issue_flow.state import StateDir
from issue_flow.testrun import render_report, run_suite


def _sname(story: int) -> str:
    return f"stories/{story}/state"


def run_tests(args, ctx) -> dict:
    tests = ctx.config.tests
    if not tests.suites:
        raise cli.Refusal("no test suites configured (tests.suites is empty)")
    state = StateDir(ctx.root, args.epic)
    st = state.read(_sname(args.story))
    rnd = int(st.get("round", 0)) + 1
    results = [run_suite(s, ctx.root) for s in tests.suites]
    failed = [r.name for r in results if r.required and not r.ok]
    passed = not failed
    rel = Path(".specify/issue-flow") / str(args.epic) / "stories" / str(args.story) / "test-report.md"
    path = ctx.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(args.story, rnd, tests.max_fix_rounds, results))
    result = {"passed": passed, "round": rnd, "max_rounds": tests.max_fix_rounds,
              "exhausted": (not passed) and rnd >= tests.max_fix_rounds,
              "failed": failed, "report": rel.as_posix()}
    st.update(round=rnd, last_result=result)
    state.write(_sname(args.story), st)
    return result


def reset_rounds(args, ctx) -> dict:
    state = StateDir(ctx.root, args.epic)
    st = state.read(_sname(args.story))
    st["round"] = 0
    state.write(_sname(args.story), st)
    return {"story": args.story, "round": 0}


def _setup(sp):
    sp.add_argument("--epic", type=int, required=True)
    sp.add_argument("--story", type=int, required=True)


cli.register("run-tests", run_tests, _setup)
cli.register("reset-rounds", reset_rounds, _setup)
