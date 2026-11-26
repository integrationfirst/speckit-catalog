"""Per-epic state directory: .specify/issue-flow/<epic>/<name>.json"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class StateDir:
    def __init__(self, root: Path, epic: int):
        self.path = Path(root) / ".specify" / "issue-flow" / str(epic)

    def _file(self, name: str) -> Path:
        return self.path / f"{name}.json"

    def read(self, name: str) -> dict:
        f = self._file(name)
        if not f.is_file():
            return {}
        return json.loads(f.read_text())

    def write(self, name: str, data: dict) -> None:
        f = self._file(name)
        f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, f)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
