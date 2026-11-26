"""pull-issue, request, check-approval, record-feature."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from issue_flow import cli
from issue_flow.approvals import content_hash, evaluate, next_request_id
from issue_flow.state import StateDir

MAX_BODY = 60000
STAGES = ["spec", "plan", "tasks", "qa", "pr"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_branch(root: Path) -> str:
    return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()


# -- pull-issue
def pull_issue(args, ctx) -> dict:
    issue = ctx.gh.issue(args.epic)
    label = ctx.config.trigger_label
    if label not in issue["labels"]:
        raise cli.Refusal(f"issue #{args.epic} does not have the '{label}' label")
    lines = [f"# {issue['title']}", "", f"Source: {issue['url']}", "", issue["body"], "", "## Comments", ""]
    for c in ctx.gh.comments(args.epic):
        lines += [f"**@{c['author']}** ({c['createdAt']}):", c["body"], ""]
    rel = Path(".specify/issue-flow") / str(args.epic) / "issue.md"
    path = ctx.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return {"epic": args.epic, "title": issue["title"], "path": rel.as_posix()}


# -- request
def _fence(text: str) -> str:
    import re
    n = max([len(m) for m in re.findall(r"`+", text)] + [2]) + 1
    return "`" * n


def _logins(ctx, stage: str) -> list[str]:
    out: list[str] = []
    for r in ctx.config.stages[stage].roles:
        for lg in ctx.config.approvers.get(r, []):
            if lg not in out:
                out.append(lg)
    return out


def _body(ctx, stage, rid, summary, page_url, artifacts) -> str:
    head = [f"### Review requested: {stage} — {rid}", ""]
    if summary:
        head += [summary, ""]
    tail = []
    logins = _logins(ctx, stage)
    if logins:
        tail += ["Approvers: " + " ".join(f"@{lg}" for lg in logins), ""]
    tail += [f"/approve {rid}", f"/reject {rid} <reason>"]
    tail_s = "\n".join(tail)
    if page_url:
        mid = [f"Document: {page_url}", ""]
        return "\n".join(head + mid) + "\n" + tail_s
    fixed = len("\n".join(head)) + len(tail_s) + 200
    budget = MAX_BODY - fixed
    mid: list[str] = []
    truncated = False
    for rel, text in artifacts:
        if budget <= 0:
            truncated = True
            break
        if len(text) > budget:
            text, truncated = text[:budget], True
        budget -= len(text) + len(rel) + 20
        f = _fence(text)
        mid += [f"{rel}:", f"{f}markdown", text, f, ""]
        if truncated:
            mid += [f"(truncated — full file: {rel})", ""]
            break
    return "\n".join(head + mid) + "\n" + tail_s


def request(args, ctx) -> dict:
    stage = args.stage
    if stage not in ctx.config.stages:
        raise cli.Refusal(f"unknown stage '{stage}'")
    paths = []
    for a in args.artifact:
        p = (ctx.root / a)
        if not p.is_file():
            raise cli.Refusal(f"artifact not found: {a}")
        paths.append(p)
    digest = content_hash(paths, base=ctx.root)
    state = StateDir(ctx.root, args.epic)
    reqs = state.read("requests")
    history = reqs.get(stage, [])
    if history and history[-1]["hash"] == digest:
        last = history[-1]
        status = evaluate(last, ctx.gh.comments(last["target"]), ctx.config.stages[stage],
                          ctx.config.approvers, digest)["status"]
        if status == "rejected":
            raise cli.Refusal(f"artifact unchanged since rejected {last['id']}; "
                              "revise it before re-requesting")
        if last["comment_id"] != 0 or ctx.dry_run:
            return {**last, "reused": True}
        # a dry-run record never posted a comment: fall through and post for real
    rid = next_request_id(reqs, stage)
    body = _body(ctx, stage, rid, getattr(args, "summary", None), getattr(args, "page_url", None),
                 [(a, p.read_text(errors="replace")) for a, p in zip(args.artifact, paths)])
    posted_at = now()
    comment_id = ctx.gh.comment(args.target, body)
    rec = {"id": rid, "stage": stage, "target": args.target, "hash": digest,
           "posted_at": posted_at, "artifacts": list(args.artifact),
           "comment_id": comment_id, "page_url": getattr(args, "page_url", None) or ""}
    reqs.setdefault(stage, []).append(rec)
    state.write("requests", reqs)
    return {**rec, "reused": False}


# -- check-approval
def check_approval(args, ctx) -> dict:
    stage = args.stage
    if stage not in ctx.config.stages:
        raise cli.Refusal(f"unknown stage '{stage}'")
    state = StateDir(ctx.root, args.epic)
    history = state.read("requests").get(stage, [])
    if not history:
        raise cli.Refusal(f"no approval request recorded for stage '{stage}'")
    req = history[-1]
    paths = [ctx.root / a for a in req["artifacts"]]
    missing = [a for a, p in zip(req["artifacts"], paths) if not p.is_file()]
    current = "missing:" + ",".join(missing) if missing else content_hash(paths, base=ctx.root)
    result = evaluate(req, ctx.gh.comments(req["target"]), ctx.config.stages[stage],
                      ctx.config.approvers, current)
    state.write(stage, result)
    if getattr(args, "wait", False) and result["status"] == "pending":
        roles = result["waiting_for"]
        logins = []
        for r in roles:
            logins += [f"@{lg}" for lg in ctx.config.approvers.get(r, []) if f"@{lg}" not in logins]
        raise cli.Waiting(result, f"waiting for {', '.join(roles)} ({', '.join(logins)}) "
                                  f"on #{req['target']} — comment /approve {req['id']}")
    return result


# -- record-feature
def record_feature(args, ctx) -> dict:
    branch = _git_branch(ctx.root)
    fdir = f"specs/{branch}"
    if not (ctx.root / fdir).is_dir():
        raise cli.Refusal(f"feature directory not found: {fdir}")
    state = StateDir(ctx.root, args.epic)
    st = state.read("state")
    st.update(feature_branch=branch, feature_dir=fdir)
    state.write("state", st)
    return {"feature_branch": branch, "feature_dir": fdir}


def _epic(sp):
    sp.add_argument("--epic", type=int, required=True)


def _setup_request(sp):
    _epic(sp)
    sp.add_argument("--stage", required=True, choices=STAGES)
    sp.add_argument("--target", type=int, required=True)
    sp.add_argument("--artifact", action="append", required=True)
    sp.add_argument("--page-url", dest="page_url", default=None)
    sp.add_argument("--summary", default=None)


def _setup_check(sp):
    _epic(sp)
    sp.add_argument("--stage", required=True, choices=STAGES)
    sp.add_argument("--wait", action="store_true")


cli.register("pull-issue", pull_issue, _epic)
cli.register("request", request, _setup_request)
cli.register("check-approval", check_approval, _setup_check)
cli.register("record-feature", record_feature, _epic)
