"""Config loader for issue-flow."""
from __future__ import annotations

import re
import subprocess
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


def _str_list(v, where: str, allow_empty: bool = True) -> list[str]:
    """A list of non-empty strings. A scalar is an error (never split into characters)."""
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"{where} must be a list of non-empty strings")
    if not allow_empty and not v:
        raise ConfigError(f"{where} must not be empty")
    return list(v)


def _read_yaml(text: str, where: str) -> dict:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {where}: {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping ({where})")
    return raw


def _git_show(root: Path, ref: str) -> str | None:
    """Contents of the config file at `ref` (path relative to `root`), or None."""
    try:
        r = subprocess.run(["git", "show", f"{ref}:./{CONFIG_REL.as_posix()}"], cwd=root,
                           capture_output=True, text=True)
    except OSError:
        return None
    return r.stdout if r.returncode == 0 else None


def _committed(root: Path, default_branch: str) -> tuple[dict, str]:
    """The config committed on origin/<default_branch> (fallback <default_branch>)."""
    for ref in (f"origin/{default_branch}", default_branch):
        text = _git_show(root, ref)
        if text is not None:
            return _read_yaml(text, f"{ref}:{CONFIG_REL.as_posix()}"), ref
    raise ConfigError(
        f"issue-flow config is not committed on origin/{default_branch} or {default_branch}: "
        f"commit and push {CONFIG_REL.as_posix()} to the default branch before a run "
        "(approvers and stages are read only from there)")


def _approvers(raw: dict) -> dict[str, list[str]]:
    return {str(k): _str_list(v, f"approvers.{k}") for k, v in _map(raw, "approvers").items()}


def _stages(raw: dict, approvers: dict[str, list[str]]) -> dict[str, StageCfg]:
    stages: dict[str, StageCfg] = {}
    for name, s in _map(raw, "stages").items():
        if not isinstance(s, dict):
            raise ConfigError(f"stages.{name} must be a mapping")
        roles = _str_list(s.get("roles"), f"stages.{name}.roles", allow_empty=False)
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
    return stages


def _suites(t: dict) -> list[Suite]:
    raw = t.get("suites") or []
    if not isinstance(raw, list):
        raise ConfigError("tests.suites must be a list")
    suites = []
    for i, s in enumerate(raw):
        where = f"tests.suites[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{where} must be a mapping")
        for k in ("name", "type", "command"):
            if k not in s:
                raise ConfigError(f"{where} missing '{k}'")
            if not isinstance(s[k], str) or not s[k].strip():
                raise ConfigError(f"{where}.{k} must be a non-empty string")
        required = s.get("required", True)
        timeout = s.get("timeout_seconds", 1800)
        if not isinstance(required, bool):
            raise ConfigError(f"{where}.required must be true or false")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ConfigError(f"{where}.timeout_seconds must be a positive integer")
        suites.append(Suite(name=s["name"], type=s["type"], command=s["command"],
                            required=required, timeout_seconds=timeout))
    return suites


def load(root: Path) -> Config:
    """Working-tree config, except `approvers` and `stages`: those are read from the config
    committed on the default branch, so an agent editing the working tree cannot change who
    may approve."""
    root = Path(root)
    path = root / CONFIG_REL
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    raw = _read_yaml(path.read_text(), str(path))

    gh = _map(raw, "github")
    repo = gh.get("repo")
    if not repo or not isinstance(repo, str):
        raise ConfigError("github.repo is required")

    g = _map(raw, "git")
    default_branch = g.get("default_branch", "main")
    stacked = g.get("stacked", True)
    if not isinstance(default_branch, str) or not re.fullmatch(r"[A-Za-z0-9._][A-Za-z0-9._/-]*",
                                                                default_branch):
        raise ConfigError("git.default_branch must be a branch name")
    if not isinstance(stacked, bool):
        raise ConfigError("git.stacked must be true or false")
    git = GitCfg(default_branch=default_branch, stacked=stacked)

    committed, _ = _committed(root, default_branch)
    approvers = _approvers(committed)
    stages = _stages(committed, approvers)

    t = _map(raw, "tests")
    rounds = t.get("max_fix_rounds", 5)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ConfigError("tests.max_fix_rounds must be a positive integer")
    tests = TestsCfg(max_fix_rounds=rounds, suites=_suites(t))

    return Config(
        repo=repo,
        trigger_label=gh.get("trigger_label", "coding-agent"),
        progress_labels=dict(_map(gh, "progress_labels")),
        confluence=dict(_map(raw, "confluence")),
        approvers=approvers, stages=stages, tests=tests, git=git)
