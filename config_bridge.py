from __future__ import annotations
import json, os, sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
patch = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
try:
    current = json.loads(path.read_text()) if path.exists() else {}
except Exception:
    current = {}
if not isinstance(current, dict): current = {}
if not isinstance(patch, dict): patch = {}
current.update(patch)
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
tmp.write_text(json.dumps(current, indent=2) + "\n")
os.chmod(tmp, 0o600)
tmp.replace(path)
