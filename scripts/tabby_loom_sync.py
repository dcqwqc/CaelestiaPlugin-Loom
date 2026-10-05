#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tabby.ipc import send_command

CONFIG = Path(os.environ.get("TABBY_LOOM_SYNC_CONFIG", Path.home() / ".config/tabby/loom-sync.json"))
DEFAULT_HOST = "philipedia"
DEFAULT_LOOM_DIR = "/home/qwqc/loom"
DONE_OUTCOMES = {"autonomous_verified_success", "reviewed_verified_success", "accepted_by_human"}


def load_config() -> dict[str, Any]:
    if not CONFIG.exists():
        return {"mappings": []}
    data = json.loads(CONFIG.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("mappings", []), list):
        raise ValueError("loom-sync config must contain a mappings array")
    return data


def fetch_status(host: str, loom_dir: str) -> dict[str, Any]:
    remote = f"cd {loom_dir} && echo '{{\"action\":\"status\"}}' | node lib/docklys_bridge.js"
    proc = subprocess.run(
        ["ssh", "-F", str(Path.home() / ".ssh/config"), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, remote],
        check=True,
        text=True,
        capture_output=True,
        timeout=15,
    )
    data = json.loads(proc.stdout)
    if not data.get("ok") or not isinstance(data.get("tasks"), list):
        raise RuntimeError("LOOM status bridge returned an invalid response")
    return data


def complete_if_needed(tabby_task_id: str, loom_task: dict[str, Any]) -> bool:
    listed = send_command({"command": "work-list"}, timeout=5)
    tasks = listed.get("tasks") or listed.get("items") or []
    local = next((t for t in tasks if str(t.get("id")) == tabby_task_id), None)
    if not local:
        raise RuntimeError(f"Tabby task {tabby_task_id} not found")
    if local.get("status") == "done" and float(local.get("progress") or 0.0) >= 1.0:
        return False

    outcome = str(loom_task.get("outcome") or "")
    summary = f"LOOM {loom_task.get('id')} abgeschlossen"
    if outcome:
        summary += f" · {outcome}"
    result = send_command(
        {"command": "work-complete", "task_id": tabby_task_id, "summary": summary},
        timeout=5,
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Tabby work-complete failed")
    return True


def main() -> int:
    cfg = load_config()
    mappings = [m for m in cfg.get("mappings", []) if isinstance(m, dict) and m.get("loom_task_id") and m.get("tabby_task_id")]
    if not mappings:
        return 0

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mapping in mappings:
        host = str(mapping.get("host") or DEFAULT_HOST)
        loom_dir = str(mapping.get("loom_dir") or DEFAULT_LOOM_DIR)
        grouped.setdefault((host, loom_dir), []).append(mapping)

    changed = 0
    for (host, loom_dir), group in grouped.items():
        status = fetch_status(host, loom_dir)
        by_id = {str(t.get("id")): t for t in status["tasks"] if isinstance(t, dict)}
        for mapping in group:
            loom_id = str(mapping["loom_task_id"])
            tabby_id = str(mapping["tabby_task_id"])
            task = by_id.get(loom_id)
            if not task:
                continue
            if task.get("status") != "done":
                continue
            outcome = str(task.get("outcome") or "")
            if outcome and outcome not in DONE_OUTCOMES:
                continue
            changed += int(complete_if_needed(tabby_id, task))

    if changed:
        print(f"completed {changed} Tabby task(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
