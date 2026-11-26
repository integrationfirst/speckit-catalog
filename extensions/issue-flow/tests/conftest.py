import shutil
import sys
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXT / "scripts"))
TEMPLATE = EXT / "issue-flow-config.template.yml"


@pytest.fixture
def project(tmp_path):
    """Project root with a config copied from the template, repo filled in, roles populated."""
    import yaml
    d = tmp_path / ".specify/extensions/issue-flow"
    d.mkdir(parents=True)
    cfg = yaml.safe_load(TEMPLATE.read_text())
    cfg["github"]["repo"] = "acme/widgets"
    for r in cfg["approvers"]:
        cfg["approvers"][r] = [f"{r}-user"]
    (d / "issue-flow-config.yml").write_text(yaml.safe_dump(cfg))
    return tmp_path
