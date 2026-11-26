from pathlib import Path

from issue_flow.approvals import content_hash, evaluate, next_request_id, parse_command
from issue_flow.config import StageCfg

APPROVERS = {"product_owner": ["PO-Login"], "business_analyst": ["ba-login"],
             "solution_architect": ["sa-login"], "team_lead": ["tl-login"], "qa_lead": ["qa-login"]}
SPEC = StageCfg(roles=["product_owner", "business_analyst", "solution_architect"], require="all")
REQ = {"id": "IF-spec-r2", "stage": "spec", "hash": "h1", "posted_at": "2026-01-01T10:00:00Z"}
T1, T2, T3 = "2026-01-01T11:00:00Z", "2026-01-01T12:00:00Z", "2026-01-01T13:00:00Z"


def c(author, body, created=T1, updated=None, cid=1):
    return {"id": cid, "author": author, "body": body, "createdAt": created,
            "updatedAt": updated or created}


def ev(comments, stage=SPEC, req=REQ, approvers=APPROVERS, h="h1"):
    return evaluate(req, comments, stage, approvers, h)


def test_request_ids_increment():
    assert next_request_id({}, "plan") == "IF-plan-r1"
    assert next_request_id({"plan": [{"id": "IF-plan-r1"}, {"id": "IF-plan-r2"}]}, "plan") == "IF-plan-r3"


def test_parse_command():
    assert parse_command("/approve IF-spec-r2") == ("approve", "IF-spec-r2", "")
    assert parse_command("/REJECT IF-qa-r1 missing API tests") == ("reject", "IF-qa-r1", "missing API tests")
    assert parse_command("LGTM /approve IF-spec-r2") is None
    assert parse_command("/approve IF-spec-r2\nthanks") == ("approve", "IF-spec-r2", "")


def test_content_hash_depends_on_path_and_bytes(tmp_path):
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.md").write_text("y")
    h = content_hash([tmp_path / "b.md", tmp_path / "a.md"], base=tmp_path)
    assert h == content_hash([tmp_path / "a.md", tmp_path / "b.md"], base=tmp_path)
    (tmp_path / "a.md").write_text("z")
    assert h != content_hash([tmp_path / "a.md", tmp_path / "b.md"], base=tmp_path)


def test_spec_needs_all_three_roles():
    cs = [c("po-login", "/approve IF-spec-r2", cid=1), c("BA-Login", "/approve IF-spec-r2", cid=2)]
    r = ev(cs)
    assert r["status"] == "pending" and r["waiting_for"] == ["solution_architect"]
    r = ev(cs + [c("sa-login", "/approve IF-spec-r2", cid=3)])
    assert r["status"] == "approved" and r["waiting_for"] == []
    assert r["approved_by"] == {"product_owner": "po-login", "business_analyst": "BA-Login",
                                "solution_architect": "sa-login"}
    assert r["request_id"] == "IF-spec-r2"


def test_unlisted_login_ignored():
    r = ev([c("random-dev", "/approve IF-spec-r2")])
    assert r["status"] == "pending" and r["approved_by"] == {}
    r = ev([c("random-dev", "/reject IF-spec-r2 no")])
    assert r["status"] == "pending"


def test_reject_wins_with_reason():
    r = ev([c("po-login", "/approve IF-spec-r2"),
            c("ba-login", "/reject IF-spec-r2 needs offline mode", T2)])
    assert r["status"] == "rejected" and r["reason"] == "needs offline mode"
    assert "needs offline mode" in r["feedback"] and "@ba-login" in r["feedback"]


def test_old_request_id_does_not_count():
    r = ev([c("po-login", "/approve IF-spec-r1")])
    assert r["status"] == "pending"


def test_comment_before_request_ignored():
    r = ev([c("po-login", "/approve IF-spec-r2", created="2026-01-01T09:00:00Z")])
    assert r["status"] == "pending" and r["approved_by"] == {}


def test_hash_change_is_stale():
    cs = [c(a, "/approve IF-spec-r2") for a in ("po-login", "ba-login", "sa-login")]
    assert ev(cs, h="other")["status"] == "stale"


def test_edited_comment_uses_current_body():
    # created before the request as /reject, edited after to /approve (body shown is current)
    cs = [c("po-login", "/approve IF-spec-r2", created="2026-01-01T09:00:00Z", updated=T1)]
    assert ev(cs)["approved_by"] == {"product_owner": "po-login"}
    # edited the other way: approve text replaced by reject
    cs = [c("po-login", "/reject IF-spec-r2 changed my mind", created="2026-01-01T09:00:00Z", updated=T1)]
    assert ev(cs)["status"] == "rejected"


def test_latest_command_per_login_wins():
    tl = StageCfg(roles=["team_lead"])
    req = {**REQ, "id": "IF-plan-r1", "stage": "plan"}
    cs = [c("tl-login", "/approve IF-plan-r1", T1, cid=1),
          c("tl-login", "/reject IF-plan-r1 on reflection no", T2, cid=2)]
    assert ev(cs, stage=tl, req=req)["status"] == "rejected"
    cs = [c("tl-login", "/reject IF-plan-r1 no", T1, cid=1),
          c("tl-login", "/approve IF-plan-r1", T2, cid=2)]
    assert ev(cs, stage=tl, req=req)["status"] == "approved"


def test_one_login_in_two_roles():
    appr = {**APPROVERS, "product_owner": ["dual"], "business_analyst": ["dual"]}
    r = ev([c("dual", "/approve IF-spec-r2"), c("sa-login", "/approve IF-spec-r2", cid=2)], approvers=appr)
    assert r["status"] == "approved"
    assert r["approved_by"]["product_owner"] == r["approved_by"]["business_analyst"] == "dual"


def test_require_any():
    st = StageCfg(roles=["product_owner", "business_analyst"], require="any")
    r = ev([c("ba-login", "/approve IF-spec-r2")], stage=st)
    assert r["status"] == "approved"
