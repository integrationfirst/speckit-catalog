"""Config loader for issue-flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_REL = Path(".specify/extensions/issue-flow/issue-flow-config.yml")


class ConfigError(Exception):
    pass


@dataclass
class StageCfg:
    roles: list[str]
    require: str = "all"
    via: str = "comment"


@dataclass
class Suite:
    name: str
    type: str
    command: str
    required: bool = True
    timeout_seconds: int = 1800


@dataclass
class TestsCfg:
    max_fix_rounds: int = 5
    suites: list[Suite] = field(default_factory=list)


@dataclass
class GitCfg:
    default_branch: str = "main"
    stacked: bool = True


@dataclass
class Config:
    repo: str
    trigger_label: str
    progress_labels: dict[str, str]
    confluence: dict
    approvers: dict[str, list[str]]
    stages: dict[str, StageCfg]
    tests: TestsCfg
    git: GitCfg


def _map(d, key):
    v = d.get(key) or {}
    if not isinstance(v, dict):
        raise ConfigError(f"'{key}' must be a mapping")
    return v


def load(root: Path) -> Config:
    path = Path(root) / CONFIG_REL
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}")
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    gh = _map(raw, "github")
    repo = gh.get("repo")
    if not repo or not isinstance(repo, str):
        raise ConfigError("github.repo is required")

    approvers = {k: list(v or []) for k, v in _map(raw, "approvers").items()}
    stages: dict[str, StageCfg] = {}
    for name, s in _map(raw, "stages").items():
        s = s or {}
        roles = list(s.get("roles") or [])
        for r in roles:
            if r not in approvers:
                raise ConfigError(f"stage '{name}' references unknown role '{r}'")
        require = s.get("require", "all")
        via = s.get("via", "comment")
        if require not in ("all", "any"):
            raise ConfigError(f"stage '{name}': require must be 'all' or 'any'")
        if via not in ("comment", "github_review"):
            raise ConfigError(f"stage '{name}': via must be 'comment' or 'github_review'")
        stages[name] = StageCfg(roles=roles, require=require, via=via)

    t = _map(raw, "tests")
    suites = []
    for i, s in enumerate(t.get("suites") or []):
        for k in ("name", "type", "command"):
            if k not in s:
                raise ConfigError(f"tests.suites[{i}] missing '{k}'")
        suites.append(Suite(
            name=s["name"], type=s["type"], command=s["command"],
            required=s.get("required", True),
            timeout_seconds=s.get("timeout_seconds", 1800)))
    tests = TestsCfg(max_fix_rounds=t.get("max_fix_rounds", 5), suites=suites)

    g = _map(raw, "git")
    git = GitCfg(default_branch=g.get("default_branch", "main"),
                 stacked=g.get("stacked", True))

    return Config(
        repo=repo,
        trigger_label=gh.get("trigger_label", "coding-agent"),
        progress_labels=dict(gh.get("progress_labels") or {}),
        confluence=dict(_map(raw, "confluence")),
        approvers=approvers, stages=stages, tests=tests, git=git)
