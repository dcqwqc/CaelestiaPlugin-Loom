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

## Central QWQC MCP Hub integration (October 15)

The production **headless** entrypoint is now the existing
\`https://mcp.qwqc.de/mcp/chatgpt\` OAuth gateway's \`loom-core\` server.
It launches \`loom_mcp.py stdio\` through the existing owner-only Unix-socket
host relay; the container never receives Loom ledger or SUMI credentials.
The gateway permits 15 headless tools, including \`loom_project_links\`,
\`loom_project_link\`, mission and idea APIs, health and handoff.

The Philipedia source includes the 89-name Mirai tool catalog plus three
Philipedia-only tools. Extra Mirai-only tools fail closed if its optional
desktop credential is not installed. The separate \`loom-mirai\` adapter in
MCPHub remains as the desktop fallback while Mirai is reachable.

**Do not switch the legacy \`tabby-mcp.qwqc.de\` direct connector yet.**
Its current authentication secret has not been installed on Philipedia, and
the old tunnel still points to Mirai. Routing that host to the new listener
without verifying the existing credential would break authentication.
The central OAuth connector is independent of Mirai for headless tools.

The central gateway files have protected pre-change copies under
\`~/services/mcphub/ops/\`. To disable the new upstream, set
\`mcpServers.loom-core.enabled=false\` in \`data/mcp_settings.json\`
and restart only the \`mcphub\` container. The old \`loom-mirai\`
upstream and public route were not removed or modified.
