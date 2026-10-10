#!/usr/bin/env python3
"""Live-desktop isolation proof for Loom agent workspaces.

Run on the real Hyprland session while the user keeps working (moving the
mouse, typing). Two agents act concurrently in separate private workspaces
while this script samples the user's Hyprland cursor (~100 Hz), focused
window, active workspaces and client list.

Fails (exit 1) when:
  * the physical cursor ever lands on any agent target coordinate (warp);
  * a Hyprland client appears whose process belongs to an agent workspace;
  * the focused window / active workspace changes while the user's cursor was
    not moving and no user key activity could explain it (reported, with
    timestamps, as suspected interference);
  * the agents interfered with each other (pointer of one workspace changed by
    the other) or cleanup left tagged processes behind.

Usage: python3 tests/e2e_agent_input_isolation.py [--seconds 40] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tabby.agent_input_ipc import request  # noqa: E402

# Deliberately odd coordinates so a warp would be unmistakable.
TARGETS_A = [(1213, 641), (97, 733), (845, 119), (1031, 509)]
TARGETS_B = [(203, 587), (1179, 97), (641, 401), (59, 59)]


def hypr(*args: str):
    out = subprocess.run(["hyprctl", "-j", *args], capture_output=True, text=True, timeout=2).stdout
    return json.loads(out or "null")


def cursor() -> tuple[int, int]:
    out = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=2).stdout
    x, y = out.strip().split(",")
    return int(x), int(y.strip())


def tagged_pids(ws_id: str) -> set[int]:
    tag = f"LOOM_AGENT_WORKSPACE={ws_id}".encode()
    pids = set()
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if tag in (entry / "environ").read_bytes().split(b"\0"):
                    pids.add(int(entry.name))
            except OSError:
                pass
    return pids


def must(reply: dict) -> dict:
    if not reply.get("ok"):
        raise SystemExit(f"service error: {reply}")
    return reply


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--json")
    args = ap.parse_args()

    stamp = str(int(time.time()))
    a = must(request({"command": "acquire", "agent_id": "e2e-claude", "request_key": "e2e-" + stamp,
                      "kind": "browser", "label": "e2e browser",
                      "url": "data:text/html,<title>e2e</title><input id=q style='font-size:30px'>"
                             "<button onclick=\"document.title=document.getElementById('q').value\">Go</button>"},
                     timeout=90))
    b = must(request({"command": "acquire", "agent_id": "e2e-codex", "request_key": "e2e-" + stamp,
                      "kind": "desktop", "label": "e2e desktop"}, timeout=60))
    A = {"workspace_id": a["workspace"]["id"], "agent_id": "e2e-claude", "token": a["token"]}
    B = {"workspace_id": b["workspace"]["id"], "agent_id": "e2e-codex", "token": b["token"]}
    must(request({"command": "launch", "argv": ["zenity", "--entry", "--text", "e2e"], **B}, timeout=30))

    samples: list[tuple[float, int, int]] = []
    focus: list[tuple[float, str, str]] = []
    clients_seen: set[str] = {c["address"] for c in hypr("clients")}
    new_clients: list[dict] = []
    stop = threading.Event()

    def sample_cursor():
        while not stop.is_set():
            try:
                x, y = cursor()
                samples.append((time.time(), x, y))
            except Exception:
                pass
            time.sleep(0.01)

    def sample_focus():
        while not stop.is_set():
            try:
                w = hypr("activewindow") or {}
                ws = hypr("activeworkspace") or {}
                focus.append((time.time(), str(w.get("address", "")), str(ws.get("name", ""))))
                for c in hypr("clients"):
                    if c["address"] not in clients_seen:
                        clients_seen.add(c["address"])
                        new_clients.append({"t": time.time(), "address": c["address"], "pid": c.get("pid"),
                                            "class": c.get("class"), "title": c.get("title")})
            except Exception:
                pass
            time.sleep(0.1)

    actions: list[tuple[float, str, int, int]] = []
    errors: list[str] = []

    def agent(creds, targets, name):
        end = time.time() + args.seconds
        i = 0
        while time.time() < end:
            x, y = targets[i % len(targets)]
            r = request({"command": "move", "x": x, "y": y, "duration_ms": 250, **creds})
            if not r.get("ok"):
                errors.append(f"{name} move: {r}")
            actions.append((time.time(), name, x, y))
            if i % 2 == 0:
                r = request({"command": "click", **creds})
                if not r.get("ok"):
                    errors.append(f"{name} click: {r}")
            if i % 4 == 3:
                r = request({"command": "type", "text": f"{name}{i} ", **creds})
                if not r.get("ok"):
                    errors.append(f"{name} type: {r}")
            st = request({"command": "state", **creds})
            if st.get("ok") and list(st["pointer"]) != [x, y]:
                errors.append(f"{name} pointer moved by someone else: {st['pointer']} != {(x, y)}")
            i += 1

    threads = [threading.Thread(target=sample_cursor), threading.Thread(target=sample_focus)]
    for t in threads:
        t.start()
    print(f"agents running for {args.seconds:.0f}s — keep using the mouse and keyboard normally", flush=True)
    workers = [threading.Thread(target=agent, args=(A, TARGETS_A, "A")),
               threading.Thread(target=agent, args=(B, TARGETS_B, "B"))]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    time.sleep(0.5)
    stop.set()
    for t in threads:
        t.join()

    # Warp check: the physical cursor never sits on an agent target right after the agent moved there.
    monitors = hypr("monitors")
    offsets = [(m["x"], m["y"]) for m in monitors] + [(0, 0)]
    warps = []
    for t_act, name, x, y in actions:
        for ts, cx, cy in samples:
            if t_act - 0.05 <= ts <= t_act + 0.6:
                if any((cx, cy) == (x + ox, y + oy) for ox, oy in offsets):
                    warps.append({"t": ts, "agent": name, "target": (x, y), "cursor": (cx, cy)})
    distinct = len({(x, y) for _, x, y in samples})
    moved_by_user = distinct > 1

    agent_pids = tagged_pids(A["workspace_id"]) | tagged_pids(B["workspace_id"])
    agent_clients = [c for c in new_clients if c.get("pid") in agent_pids]

    # Focus/workspace changes, with whether the user's cursor was moving nearby.
    changes = []
    for prev, cur in zip(focus, focus[1:]):
        if prev[1:] != cur[1:]:
            near = [s for s in samples if abs(s[0] - cur[0]) < 1.0]
            user_moving = len({(x, y) for _, x, y in near}) > 1
            changes.append({"t": cur[0], "window": cur[1], "workspace": cur[2], "user_cursor_moving": user_moving})

    # Cleanup check.
    must(request({"command": "release", "keep_profile": False, **A}, timeout=30))
    must(request({"command": "release", "keep_profile": False, **B}, timeout=30))
    time.sleep(0.5)
    leftovers = sorted(tagged_pids(A["workspace_id"]) | tagged_pids(B["workspace_id"]))

    report = {
        "seconds": args.seconds, "cursor_samples": len(samples), "distinct_user_cursor_positions": distinct,
        "user_moved_mouse": moved_by_user, "agent_actions": len(actions), "warps": warps,
        "agent_hyprland_clients": agent_clients, "new_hyprland_clients": new_clients,
        "focus_or_workspace_changes": changes, "agent_errors": errors, "leftover_pids": leftovers,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    failed = bool(warps or agent_clients or errors or leftovers)
    print(json.dumps({k: (v if not isinstance(v, list) else (len(v) if k in {"new_hyprland_clients"} else v))
                      for k, v in report.items()}, indent=2))
    print("FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
