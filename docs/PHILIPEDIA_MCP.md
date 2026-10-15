# Philipedia-hosted Loom MCP core

The same `loom_mcp.py` registry now has two explicit runtime roles.  The
Philipedia role executes the existing LOOM ledger bridge locally with the fixed
argv `/usr/bin/node /home/qwqc/loom/lib/docklys_bridge.js`; it never accepts a
host, executable, or shell fragment from MCP input.  Mission, status, activity,
idea capture/list/dispatch, capability intake, handoff, saved modules, cached
task snapshots, MCP discovery, and health therefore do not depend on Mirai.

Desktop calls remain the existing public Loom tool signatures.  On Philipedia
they are translated to those same tool names at the fixed Tailscale endpoint
`100.100.120.58:8766`, with a dedicated bearer token read from the mode-0600
`~/.config/tabby/mirai-mcp-token`.  Only the enumerated backend commands can use
the adapter.  Missing credentials or an offline laptop returns `Mirai desktop
is unavailable`; it does not affect headless calls.

`GET /healthz` and authenticated `loom_runtime_health` distinguish the local
Philipedia core from the optional Mirai adapter.  The MCP endpoint itself keeps
the existing source-network allowlist and bearer-token gate.

## Rollout (not performed by this change)

First ensure `/home/qwqc/loom/lib/docklys_bridge.js` exists on Philipedia and
that its current tests pass.  Install this committed branch at
`~/Projects/CaelestiaPlugin-Loom`, then:

```sh
install -m 600 /dev/null ~/.config/tabby/mirai-mcp-token
# Populate the file out of band with the existing Mirai MCP bearer token.
install -m 644 systemd/loom-mcp-philipedia.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now loom-mcp-philipedia.service
curl --fail --silent http://127.0.0.1:8766/healthz
```

Verify authenticated `tools/list`, `loom_mission_health`, and
`loom_runtime_health` locally before changing any reverse-proxy or Cloudflare
configuration.  Routing changes are deliberately outside this branch.

## Rollback

```sh
systemctl --user disable --now loom-mcp-philipedia.service
rm ~/.config/systemd/user/loom-mcp-philipedia.service
systemctl --user daemon-reload
```

Then restore the previous tunnel origin.  The runtime owns no ledger and writes
no duplicate mission/task store, so rollback needs no state migration.
