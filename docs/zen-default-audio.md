# Zen and Loom voice: selected default audio output

The Zen cubeb-output-v1:Zen streams previously remembered the laptop
Speaker target in WirePlumber, while Loom's ZenClient._route_voice_audio()
re-pinned voice to Speaker every 150 ms. Some Zen streams also arrive with
an explicit target.object=qsw_quest_output and node.dont-reconnect=true.
These three mechanisms conflicted with the desktop's default device.

tabby/zen.py now asks scripts/loom_default_audio.py for the live default
and never sets a volume or microphone source. The event-driven, enabled
loom-zen-default-audio.service corrects running/new Zen playback streams,
including ones with explicit targets, whenever the output changes
or the headset reconnects. It does not touch audio from other applications.
All Zen streams (not only Loom's engine tab) follow the default because
PipeWire exposes generic Zen stream identities and conversation titles change.
Intentional nondefault Zen per-tab output selections will be superseded.

wireplumber/52-loom-zen-default-output.conf prevents restoring the obsolete
per-application Zen sink target on new streams at next WirePlumber startup.
It does not change saved volumes or the source selection. Do not restart
WirePlumber or Zen just to activate it during a live call; the watcher
handles the running graph. The config lives in
~/.config/wireplumber/wireplumber.conf.d/ and the service in
~/.config/systemd/user/. Enable it with:
systemctl --user daemon-reload
systemctl --user enable --now loom-zen-default-audio.service

Verification: pactl get-default-sink; pactl -f json list sink-inputs;
journalctl --user -u loom-zen-default-audio.service.
No live Quest USB disconnect was performed, because it would disrupt VR.
