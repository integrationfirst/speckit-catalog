"""Config loader for issue-flow."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_REL = Path(".specify/extensions/issue-flow/issue-flow-config.yml")
_BRANCH_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*")


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


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    except OSError:
        return None


def _git_show(root: Path, ref: str) -> str | None:
    """Contents of the config file at `ref` (path relative to `root`), or None."""
    r = _run_git(root, ["show", f"{ref}:./{CONFIG_REL.as_posix()}"])
    return r.stdout if r is not None and r.returncode == 0 else None


def _origin_default_branch(root: Path) -> str | None:
    """The default branch of the `origin` remote, asked from the remote itself (never from the
    working tree). None when there is no `origin` remote; ConfigError when it cannot be reached."""
    r = _run_git(root, ["remote", "get-url", "origin"])
    if r is None or r.returncode != 0:
        return None
    r = _run_git(root, ["ls-remote", "--symref", "origin", "HEAD"])
    if r is not None and r.returncode == 0:
        m = re.search(r"^ref: refs/heads/(\S+)\tHEAD$", r.stdout, re.M)
        if m and _BRANCH_RE.fullmatch(m.group(1)):
            return m.group(1)
    raise ConfigError("cannot read the default branch of 'origin' (git ls-remote failed); "
                      "approvers are read from it, so the run cannot continue offline")


def _fetch(root: Path, branch: str) -> None:
    """Refresh origin/<branch> so approver changes pushed since the last fetch are seen."""
    r = _run_git(root, ["fetch", "--quiet", "--no-tags", "origin",
                        f"+refs/heads/{branch}:refs/remotes/origin/{branch}"])
    if r is None or r.returncode != 0:
        err = (r.stderr.strip() if r is not None else "git not found")
        raise ConfigError(f"cannot fetch origin/{branch}: {err}")


def _committed(root: Path, wt_default_branch: str) -> tuple[dict, str]:
    """The config committed on the remote's default branch, freshly fetched. Only a project
    without an `origin` remote falls back to the local branch named by the working tree
    (`git.default_branch`)."""
    remote_default = _origin_default_branch(root)
    if remote_default is not None:
        _fetch(root, remote_default)
        ref = f"origin/{remote_default}"
    else:
        ref = wt_default_branch
    text = _git_show(root, ref)
    if text is None:
        raise ConfigError(
            f"issue-flow config is not committed on {ref}: commit and push "
            f"{CONFIG_REL.as_posix()} to the default branch before a run "
            "(approvers, stages and git settings are read only from there)")
    return _read_yaml(text, f"{ref}:{CONFIG_REL.as_posix()}"), ref


def _git_cfg(raw: dict) -> GitCfg:
    g = _map(raw, "git")
    default_branch = g.get("default_branch", "main")
    if not isinstance(default_branch, str) or not _BRANCH_RE.fullmatch(default_branch):
        raise ConfigError("git.default_branch must be a branch name")
    stacked = g.get("stacked", True)
    if stacked is not True:
        raise ConfigError("git.stacked: unstacked mode was removed in issue-flow 0.1.1 (Story "
                          "PRs always stack); delete the setting")
    return GitCfg(default_branch=default_branch)


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
    """Working-tree config, except `approvers`, `stages` and `git`: those are read from the
    config committed on the remote's default branch (fetched first), so an agent editing the
    working tree cannot change who may approve or where PRs go."""
    root = Path(root)
    path = root / CONFIG_REL
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    raw = _read_yaml(path.read_text(), str(path))

    gh = _map(raw, "github")
    repo = gh.get("repo")
    if not repo or not isinstance(repo, str):
        raise ConfigError("github.repo is required")

    wt_git = _git_cfg(raw)   # validated, but only used to locate the config without a remote
    committed, _ = _committed(root, wt_git.default_branch)
    git = _git_cfg(committed)
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
