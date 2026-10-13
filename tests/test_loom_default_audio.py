"""Unit regressions for Zen voice default-device routing; no live audio touched."""
import json
import unittest
from unittest.mock import patch

from scripts import loom_default_audio as a


SINKS = (
    "178\talsa_output.speaker\tPipeWire\tfloat32le 2ch 48000Hz\n"
    "32434\tqsw_quest_output\tPipeWire\tfloat32le 2ch 48000Hz\n"
)
STREAMS = [
    {"index": 34325, "sink": 178, "properties":
        {"application.name": "Zen", "media.name": "Desktop Companion Setup"}},
    {"index": 34355, "sink": 32434, "properties":
        {"application.name": "Zen", "media.name": "Desktop Companion Setup",
         "target.object": "qsw_quest_output", "node.dont-reconnect": "true"}},
    {"index": 38787, "sink": 178, "properties":
        {"application.name": "Zen", "media.name": "Another ChatGPT chat"}},
    {"index": 55, "sink": 178, "properties":
        {"application.name": "MusicPlayer", "media.name": "Music"}},
    {"index": 99, "sink": 178, "properties":
        {"application.name": "sunshine", "media.name": "Quest capture"}},
]


class DefaultAudioTests(unittest.TestCase):
    def test_routes_zen_only_to_current_quest_default(self):
        self.assertEqual(a.desired_moves("qsw_quest_output", SINKS, STREAMS),
                         [34325, 38787])

    def test_reverse_switch_follows_laptop_and_explicit_quest_target(self):
        self.assertEqual(a.desired_moves("alsa_output.speaker", SINKS, STREAMS),
                         [34355])

    def test_title_scope_leaves_unrelated_zen_tabs_untouched(self):
        self.assertEqual(a.desired_moves("qsw_quest_output", SINKS, STREAMS,
                                        title="Desktop Companion Setup"), [34325])

    def test_no_target_means_no_moves_on_disconnect(self):
        self.assertEqual(a.desired_moves("qsw_quest_output", SINKS.splitlines()[0],
                                         STREAMS), [])

    def test_empty_and_unchanged(self):
        self.assertEqual(a.desired_moves("", SINKS, STREAMS), [])
        self.assertEqual(a.desired_moves("qsw_quest_output", SINKS, [STREAMS[1]]), [])

    @patch.object(a, "pactl")
    def test_moves_only_needed_streams_never_touching_mic_or_volume(self, pactl):
        def fake(*args):
            if args == ("get-default-sink",):
                return "qsw_quest_output\n"
            if args == ("list", "short", "sinks"):
                return SINKS
            if args == ("-f", "json", "list", "sink-inputs"):
                return json.dumps(STREAMS)
            if args[0] == "move-sink-input":
                return ""
            raise AssertionError(args)
        pactl.side_effect = fake
        self.assertEqual(a.move_zen_streams(), 2)
        self.assertEqual(
            [x.args for x in pactl.call_args_list if x.args[0] == "move-sink-input"],
            [("move-sink-input", "34325", "qsw_quest_output"),
             ("move-sink-input", "38787", "qsw_quest_output")]
        )

    @patch.object(a, "pactl")
    def test_quest_reconnect_can_change_sink_id(self, pactl):
        rows=[{**STREAMS[0], "sink": 178}]
        def fake(*args):
            if args == ("get-default-sink",): return "qsw_quest_output"
            if args == ("list", "short", "sinks"):
                return "178\talsa_output.speaker\tPipeWire\n5000\tqsw_quest_output\tPipeWire\n"
            if args == ("-f", "json", "list", "sink-inputs"):
                return json.dumps(rows)
            if args == ("move-sink-input", "34325", "qsw_quest_output"):
                return ""
            raise AssertionError(args)
        pactl.side_effect=fake
        self.assertEqual(a.move_zen_streams(), 1)

    @patch.object(a, "pactl", side_effect=OSError("PipeWire unavailable"))
    def test_pipewire_restart_fails_safe(self, pactl):
        self.assertEqual(a.move_zen_streams(), -1)


if __name__ == "__main__":
    unittest.main()
