#!/usr/bin/env bash
# Usage: scripts/build-extension.sh <id> <version>
# Builds dist/<id>-<version>.zip (top folder <id>-<version>/), excluding tests and caches,
# preserving executable bits. Prints the sha256.
set -euo pipefail
id="${1:?usage: build-extension.sh <id> <version>}"; ver="${2:?usage: build-extension.sh <id> <version>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
python3 -I - "$root" "$id" "$ver" <<'PY'
import hashlib, os, sys, zipfile
root, id_, ver = sys.argv[1:4]
src = os.path.join(root, "extensions", id_)
if not os.path.isdir(src):
    sys.exit(f"no such extension: {src}")
out_dir = os.path.join(root, "dist"); os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, f"{id_}-{ver}.zip")
skip_dirs = {"tests", "__pycache__", ".pytest_cache"}
if os.path.exists(out): os.remove(out)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for d, dirs, files in os.walk(src):
        dirs[:] = sorted(x for x in dirs if x not in skip_dirs)
        for f in sorted(files):
            if f.endswith(".pyc"): continue
            p = os.path.join(d, f)
            arc = f"{id_}-{ver}/" + os.path.relpath(p, src).replace(os.sep, "/")
            mode = 0o755 if arc.split("/")[1] == "bin" else 0o644
            zi = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
            zi.external_attr = (0o100000 | mode) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as fh: z.writestr(zi, fh.read())
print(out)
print(hashlib.sha256(open(out, "rb").read()).hexdigest())
PY
