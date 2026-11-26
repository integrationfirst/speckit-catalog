import pytest
import yaml

from issue_flow import config
from issue_flow.config import ConfigError

CFG = ".specify/extensions/issue-flow/issue-flow-config.yml"


def _edit(project, fn):
    p = project / CFG
    d = yaml.safe_load(p.read_text())
    fn(d)
    p.write_text(yaml.safe_dump(d))


def test_load_valid_config(project):
    cfg = config.load(project)
    assert cfg.repo == "acme/widgets"
    assert cfg.trigger_label == "coding-agent"
    assert cfg.stages["spec"].roles == ["product_owner", "business_analyst", "solution_architect"]
    assert cfg.stages["pr"].via == "github_review"
    assert cfg.tests.max_fix_rounds == 5
    assert cfg.tests.suites[0].timeout_seconds == 1800
    assert cfg.git.stacked is True


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
