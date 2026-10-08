#!/usr/bin/env python3
"""Loom Tasks tile helper for the QML host (one compact JSON line on stdout).

  loom_tasks.py snapshot                 cached projection, no network
  loom_tasks.py refresh                  read Philipedia LOOM status + inbox, update cache
  loom_tasks.py tile                     get/create the plugin-owned tasks tile module
  loom_tasks.py resize ID WIDTH HEIGHT   persist a new size for that tile (other modules refused)
  loom_tasks.py place ID ANCHOR X Y      persist its anchor and offset

The Philipedia host and bridge executable are fixed in tabby/missions.py; this
CLI accepts no host, command or path arguments.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tabby import tasks_tile  # noqa: E402
from tabby.spaces import SpaceError, SpaceStore  # noqa: E402

TILE_REQUEST_ID = "loom-tasks-tile-default"
TILE_PLACEMENT = {"surface": "performance", "anchor": "free", "width": 360, "height": 300}
TILE_DATA = {"source": "philipedia", "role": "loom-panel-tasks-tile"}


def ensure_tile(store):
    """Return the plugin-owned tile, resolved only by its reserved request ID.

    Other tasks modules (including ones on the performance surface) belong to
    the user's Spaces and are never selected or resized here.
    """
    module = store.module_for_request(TILE_REQUEST_ID)
    if module is not None:
        return module
    return store.create_module(kind="tasks", title="Loom tasks", data=TILE_DATA,
                               placement=TILE_PLACEMENT, request_id=TILE_REQUEST_ID)


def parser():
    p = argparse.ArgumentParser(prog="loom_tasks.py", description="Loom Tasks tile helper")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot", help="print the cached projection without network access")
    sub.add_parser("refresh", help="refresh from the fixed Philipedia LOOM bridge")
    sub.add_parser("tile", help="get or create the saved tasks module")
    resize = sub.add_parser("resize", help="persist a saved module size")
    resize.add_argument("module_id")
    resize.add_argument("width", type=int)
    resize.add_argument("height", type=int)
    place = sub.add_parser("place", help="persist a saved module anchor and offset")
    place.add_argument("module_id")
    place.add_argument("anchor")
    place.add_argument("x", type=int)
    place.add_argument("y", type=int)
    return p


def main(argv=None, *, store=None, cache=None):
    args = parser().parse_args(argv)
    store = store or SpaceStore()
    cache = cache or tasks_tile.TasksCache()
    try:
        if args.command == "snapshot":
            result = cache.load()
        elif args.command == "refresh":
            result = tasks_tile.refresh(cache)
        elif args.command == "tile":
            result = ensure_tile(store)
        elif args.command in ("resize", "place"):
            owned = store.module_for_request(TILE_REQUEST_ID)
            if owned is None or owned["id"] != args.module_id:
                raise SpaceError("module is not the Loom tasks tile; only the plugin-owned tile can be positioned")
            placement = ({"width": args.width, "height": args.height}
                         if args.command == "resize"
                         else {"anchor": args.anchor, "x": args.x, "y": args.y})
            result = store.update_module(args.module_id, placement=placement)
    except (SpaceError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
