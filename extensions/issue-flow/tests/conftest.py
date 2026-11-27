import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXT / "scripts"))
TEMPLATE = EXT / "issue-flow-config.template.yml"
CONFIG_REL = ".specify/extensions/issue-flow/issue-flow-config.yml"


def git(root, *a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True,
                          check=True).stdout.strip()


def commit_config(root, msg="config"):
    """Commit the working-tree config on the current branch (approvers/stages are read from
    the default branch's committed config)."""
    git(root, "add", CONFIG_REL)
    git(root, "commit", "-q", "--allow-empty", "-m", msg)


@pytest.fixture
def project(tmp_path):
    """Git project on `main` with a config copied from the template (repo filled in, roles
    populated), committed so the default-branch config matches the working tree."""
    import yaml
    d = tmp_path / ".specify/extensions/issue-flow"
    d.mkdir(parents=True)
    cfg = yaml.safe_load(TEMPLATE.read_text())
    cfg["github"]["repo"] = "acme/widgets"
    for r in cfg["approvers"]:
        cfg["approvers"][r] = [f"{r}-user"]
    (d / "issue-flow-config.yml").write_text(yaml.safe_dump(cfg))
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    commit_config(tmp_path, "init")
    return tmp_path


@pytest.fixture(autouse=True)
def _workflow_step_env(monkeypatch):
    """Tests run CLI subcommands as a workflow shell step would (see cli.AGENT_COMMANDS)."""
    monkeypatch.setenv("ISSUE_FLOW_STEP", "1")
