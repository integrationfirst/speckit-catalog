"""issue-flow CLI dispatcher. One JSON object on stdout per invocation."""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Callable

from issue_flow import config as config_mod  # noqa: E402
from issue_flow.config import ConfigError  # noqa: E402


class Refusal(Exception):
    """Validation/refusal: exit 2."""


class Context:
    def __init__(self, root: Path, dry_run: bool = False, gh=None):
        self.root = Path(root)
        self.dry_run = dry_run
        self.gh = gh
        self._config = None

    @property
    def config(self):
        if self._config is None:
            self._config = config_mod.load(self.root)
        return self._config


# name -> (func, setup(parser) | None)
_REGISTRY: dict[str, tuple[Callable, Callable | None]] = {}


def register(name: str, func: Callable, setup: Callable | None = None) -> None:
    _REGISTRY[name] = (func, setup)


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")


class _Parser(argparse.ArgumentParser):
    """Help/usage/errors go to stderr so stdout carries only the JSON result."""

    def _print_message(self, message, file=None):
        if message:
            sys.stderr.write(message)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _Parser(prog="issue-flow")
    parser.add_argument("--root", default=None, help="project root (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_, setup) in sorted(_REGISTRY.items()):
        sp = sub.add_parser(name)
        if setup:
            setup(sp)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
        if code != 0:
            _emit({"error": "invalid arguments"})
            return 2
        _emit({"help": True})
        return 0
    try:
        ctx = Context(Path(args.root) if args.root else Path.cwd(),
                      dry_run=os.environ.get("ISSUE_FLOW_DRY_RUN") == "1")
        func = _REGISTRY[args.command][0]
        _emit(func(args, ctx))
        return 0
    except (Refusal, ConfigError) as e:
        _emit({"error": str(e)})
        return 2
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        _emit({"error": f"{type(e).__name__}: {e}"})
        return 1
