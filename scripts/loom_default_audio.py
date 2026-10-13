#!/usr/bin/env python3
"""Follow the current default sink for Zen/ChatGPT playback on Mirai.

Does not alter sink selection, microphones, sink volume, or unrelated apps.
The user-selected destination is never cached: Quest reconnections/default
switches are handled by checking current PulseAudio-on-PipeWire state.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time

LOG = logging.getLogger("loom-default-audio")


def pactl(*args: str) -> str:
    return subprocess.run(
        ["pactl", *args], check=True, capture_output=True,
        text=True, timeout=4
    ).stdout


def sink_index(default_name: str, rows: str) -> str | None:
    if not default_name:
        return None
    for line in rows.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == default_name:
            return parts[0]
    return None


def desired_moves(default_name: str, sinks: str, streams: list[dict],
                  title: str | None = None) -> list[int]:
    """Only move wrong-destination Zen playback. All other streams untouched."""
    selected = sink_index(default_name, sinks)
    if selected is None:
        return []
    moves: list[int] = []
    for entry in streams:
        props = entry.get("properties") or {}
        if not isinstance(props, dict):
            continue
        if props.get("application.name") != "Zen":
            continue
        if title is not None and props.get("media.name") != title:
            continue
        if str(entry.get("sink")) == selected:
            continue
        try:
            moves.append(int(entry["index"]))
        except (TypeError, ValueError, KeyError):
            continue
    return moves


def move_zen_streams(title: str | None = None) -> int:
    """Return number of successful moves; -1 on lookup failure."""
    try:
        default = pactl("get-default-sink").strip()
        sinks = pactl("list", "short", "sinks")
        # Do nothing if the selected sink is temporarily absent (reconnect).
        if sink_index(default, sinks) is None:
            return -1
        streams = json.loads(pactl("-f", "json", "list", "sink-inputs"))
        changes = desired_moves(default, sinks, streams, title=title)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        LOG.warning("Audio graph unavailable: %s", exc)
        return -1
    successes = 0
    for stream in changes:
        try:
            pactl("move-sink-input", str(stream), default)
            successes += 1
            LOG.info("Zen stream %s -> current default %s", stream, default)
        except (OSError, subprocess.SubprocessError) as exc:
            # Can vanish naturally between snapshot and move.
            LOG.debug("Stream %s vanished before move: %s", stream, exc)
    return successes


def watch() -> None:
    """Use PipeWire's event stream; reconnect on server/Quest restart."""
    while True:
        move_zen_streams()
        try:
            with subprocess.Popen(
                ["pactl", "subscribe"], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1
            ) as events:
                assert events.stdout is not None
                for line in events.stdout:
                    if any(fragment in line for fragment in
                           ("on sink-input", "on sink ", "on server")):
                        # Allow WirePlumber's normal target policy to settle.
                        time.sleep(0.06)
                        move_zen_streams()
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.warning("Audio event stream disconnected: %s", exc)
        time.sleep(1.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if "--watch" in sys.argv:
        watch()
    else:
        result = move_zen_streams()
        print("moved", result)
        sys.exit(0 if result >= 0 else 1)
