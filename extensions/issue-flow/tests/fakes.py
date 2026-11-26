"""Test doubles for the gh adapter."""
from __future__ import annotations

import json


class FakeRunner:
    """Records calls as (argv, input); returns canned outputs.

    `responses` is either a list (returned in sequence) or a list of
    (predicate(argv, input) -> bool, output) pairs checked in order. Non-str
    outputs are JSON-encoded.
    """

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[tuple[list[str], str | None]] = []
        self._i = 0

    def __call__(self, args, input=None):
        self.calls.append((list(args), input))
        if self.responses and isinstance(self.responses[0], tuple):
            for pred, out in self.responses:
                if pred(args, input):
                    return out if isinstance(out, str) else json.dumps(out)
            raise AssertionError(f"FakeRunner: no response matched {args}")
        if self._i >= len(self.responses):
            return ""
        out = self.responses[self._i]
        self._i += 1
        return out if isinstance(out, str) else json.dumps(out)
