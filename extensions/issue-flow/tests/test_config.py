import pytest
import yaml

from conftest import commit_config, git
from issue_flow import config
from issue_flow.config import ConfigError

CFG = ".specify/extensions/issue-flow/issue-flow-config.yml"


def _edit(project, fn, commit=True):
    p = project / CFG
    d = yaml.safe_load(p.read_text())
    fn(d)
    p.write_text(yaml.safe_dump(d))
    if commit:
        commit_config(project)


def test_load_valid_config(project):
    cfg = config.load(project)
    assert cfg.repo == "acme/widgets"
    assert cfg.trigger_label == "coding-agent"
    assert cfg.stages["spec"].roles == ["product_owner", "business_analyst", "solution_architect"]
    assert cfg.stages["pr"].via == "github_review"
    assert cfg.tests.max_fix_rounds == 5
    assert cfg.tests.suites[0].timeout_seconds == 1800
    assert cfg.git.default_branch == "main"


def test_unknown_role_in_stage_raises(project):
    _edit(project, lambda d: d["stages"]["plan"].update(roles=["architect"]))
    with pytest.raises(ConfigError, match="architect"):
        config.load(project)


def test_missing_repo_raises(project):
    _edit(project, lambda d: d["github"].update(repo=""))
    with pytest.raises(ConfigError, match="repo"):
        config.load(project)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        config.load(tmp_path)


# I5: malformed approver/stage/suite shapes are ConfigError (exit 2), never a crash
@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["approvers"].update(team_lead="alice"), "approvers.team_lead"),
    (lambda d: d["approvers"].update(team_lead=["alice", ""]), "approvers.team_lead"),
    (lambda d: d["approvers"].update(team_lead=[1]), "approvers.team_lead"),
    (lambda d: d["approvers"].update(team_lead=None), "approvers.team_lead"),
    (lambda d: d["stages"].update(plan="team_lead"), "stages.plan"),
    (lambda d: d["stages"].update(plan=["team_lead"]), "stages.plan"),
    (lambda d: d["stages"]["plan"].update(roles="team_lead"), "stages.plan"),
    (lambda d: d["stages"]["plan"].update(roles=[]), "stages.plan"),
    (lambda d: d["tests"].update(suites=["npm test"]), r"tests.suites\[0\]"),
    (lambda d: d["tests"].update(suites={"name": "x"}), "tests.suites"),
    (lambda d: d["tests"]["suites"][0].update(command=["npm", "test"]), r"tests.suites\[0\]"),
    (lambda d: d["tests"]["suites"][0].update(timeout_seconds="soon"), r"tests.suites\[0\]"),
    (lambda d: d["tests"]["suites"][0].update(required="no"), r"tests.suites\[0\]"),
    (lambda d: d["tests"].update(max_fix_rounds="5"), "max_fix_rounds"),
    (lambda d: d["git"].update(default_branch="--output=/tmp/x"), "default_branch"),
])
def test_malformed_shapes_raise_config_error(project, mutate, match):
    _edit(project, mutate)
    with pytest.raises(ConfigError, match=match):
        config.load(project)


def test_scalar_approver_never_splits_into_characters(project):
    _edit(project, lambda d: d["approvers"].update(team_lead="ab"))
    with pytest.raises(ConfigError):
        config.load(project)


# I8 (R23): approvers and stages come from the committed default-branch config
def test_approvers_and_stages_from_default_branch_not_working_tree(project):
    def hijack(d):
        d["approvers"]["team_lead"] = ["mallory"]
        d["stages"]["plan"]["roles"] = ["qa_lead"]
        d["tests"]["max_fix_rounds"] = 9
    _edit(project, hijack, commit=False)
    cfg = config.load(project)
    assert cfg.approvers["team_lead"] == ["team_lead-user"]
    assert cfg.stages["plan"].roles == ["team_lead"]
    assert cfg.tests.max_fix_rounds == 9          # everything else from the working tree


def test_committed_config_on_feature_branch_does_not_count(project):
    git(project, "checkout", "-q", "-b", "feat")
    _edit(project, lambda d: d["approvers"].update(team_lead=["mallory"]))   # committed on feat
    assert config.load(project).approvers["team_lead"] == ["team_lead-user"]


def _bare_origin(project, tmp_path_factory, branch="main"):
    remote = tmp_path_factory.mktemp("remote")
    git(remote, "init", "-q", "--bare", "-b", branch)
    git(project, "remote", "add", "origin", str(remote))
    git(project, "push", "-q", "origin", f"HEAD:{branch}")
    return remote


def test_origin_default_branch_preferred(project, tmp_path_factory):
    _bare_origin(project, tmp_path_factory)
    _edit(project, lambda d: d["approvers"].update(team_lead=["local-only"]))  # local main only
    assert config.load(project).approvers["team_lead"] == ["team_lead-user"]
    git(project, "push", "-q", "origin", "main")
    # no manual fetch: load() fetches origin's default branch itself
    assert config.load(project).approvers["team_lead"] == ["local-only"]


# Gap 2: approvers pushed by someone else are seen without a manual fetch
def test_load_fetches_origin_before_reading_approvers(project, tmp_path_factory):
    remote = _bare_origin(project, tmp_path_factory)
    git(project, "fetch", "-q", "origin")
    other = tmp_path_factory.mktemp("other")
    git(other, "clone", "-q", str(remote), "c")
    clone = other / "c"
    git(clone, "config", "user.email", "o@o")
    git(clone, "config", "user.name", "o")
    git(clone, "config", "commit.gpgsign", "false")
    _edit(clone, lambda d: d["approvers"].update(team_lead=["new-lead"]))
    git(clone, "push", "-q", "origin", "main")
    assert config.load(project).approvers["team_lead"] == ["new-lead"]


# Gap 2: the working tree cannot pick which branch approvers are read from
def test_working_tree_default_branch_ignored_with_origin(project, tmp_path_factory):
    _bare_origin(project, tmp_path_factory)
    git(project, "checkout", "-q", "-b", "evil")
    _edit(project, lambda d: d["approvers"].update(team_lead=["mallory"]))   # committed on evil
    git(project, "push", "-q", "origin", "evil")
    _edit(project, lambda d: d["git"].update(default_branch="evil"), commit=False)
    cfg = config.load(project)
    assert cfg.approvers["team_lead"] == ["team_lead-user"]
    assert cfg.git.default_branch == "main"          # git settings come from the committed config


def test_unreachable_origin_refused(project):
    git(project, "remote", "add", "origin", str(project / "does-not-exist"))
    with pytest.raises(ConfigError, match="origin"):
        config.load(project)


# Gap 4: unstacked mode was removed
@pytest.mark.parametrize("commit", [True, False])
def test_unstacked_mode_refused(project, commit):
    _edit(project, lambda d: d["git"].update(stacked=False), commit=commit)
    with pytest.raises(ConfigError, match="stacked"):
        config.load(project)


def test_default_branch_from_working_tree_config(project):
    git(project, "branch", "-m", "main", "trunk")
    with pytest.raises(ConfigError, match="not committed"):
        config.load(project)
    _edit(project, lambda d: d["git"].update(default_branch="trunk"), commit=False)
    assert config.load(project).approvers["team_lead"] == ["team_lead-user"]


def test_uncommitted_config_refused(tmp_path):
    import shutil
    from conftest import TEMPLATE
    d = tmp_path / ".specify/extensions/issue-flow"
    d.mkdir(parents=True)
    cfg = yaml.safe_load(TEMPLATE.read_text())
    cfg["github"]["repo"] = "acme/widgets"
    (d / "issue-flow-config.yml").write_text(yaml.safe_dump(cfg))
    git(tmp_path, "init", "-q", "-b", "main")
    with pytest.raises(ConfigError, match="not committed"):
        config.load(tmp_path)
