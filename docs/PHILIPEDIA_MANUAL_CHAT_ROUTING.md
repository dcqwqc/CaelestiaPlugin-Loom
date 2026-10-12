# Philipedia manual-chat routing rollout

## Verified source behavior

`loom_chat_route` is the supported entry point. It accepts either a canonical
ChatGPT conversation URL or 1–3 exact recent user messages. It never uses the
last active browser tab or Loom Voice as an identity fallback. A successful
route persists one `kind=chat` task for the verified conversation id and uses
the same task for Working, Review and Done. Done requires that same task to be
in verified Review plus an independent reviewer name and evidence.

The current message-fingerprint resolver can inspect only normal tabs exposed
by the connected browser bridge. If neither a canonical URL nor one unique
fingerprint match is available, routing must return an identity error without
browser writes.

## Always-on host boundary

Philipedia is the intended long-running host, but source installation alone is
not a deployment. A supported browser controller must be able to read and
operate an explicitly dedicated, authenticated ChatGPT profile on Philipedia.
Do not copy cookies, browser profiles, tokens or secrets from Mirai. Existing
Playwright/Chrome processes owned by unrelated jobs are not an authentication
source and must not be attached to or reused.

The repository does not claim an official ChatGPT project API. Project moves
remain browser UI automation and count only after the exact conversation URL
contains the target project's stable `g-p-...` id. Until a Philipedia browser
controller provides the same identity and verification contract as the Zen
bridge, keep routing blocked rather than forwarding to a missing local Loom IPC
socket or silently depending on Mirai.

## Safe deployment checklist

1. Push and review the committed branch; merge it through the normal GitHub
   workflow. Do not copy it over Mirai's dirty checkout.
2. On Philipedia, create a dedicated persistent browser profile and authenticate
   it once through an interactive user-controlled browser session. Do not pass
   credentials through command-line arguments, environment files, task cards,
   logs or repository files.
3. Install a browser controller that implements these fail-closed operations:
   list normal ChatGPT tabs; resolve an exact recent-message fingerprint to one
   canonical conversation URL; open only that URL in a dedicated routing page;
   discover lifecycle projects by stable id and exact name; move the chat; then
   independently reread the page URL and verify both conversation id and target
   project id. It must serialize moves and never send conversation messages.
4. Run that controller and the current `loom_mcp.py http` service as restricted
   user services on Philipedia. Bind privately (loopback/Tailscale), retain MCP
   bearer authentication, restrict state/profile paths, and enable restart.
   Do not expose Chrome DevTools/CDP publicly; prefer a local pipe or loopback
   endpoint available only to the service account.
5. Confirm `loom_origin_resolve` returns distinct failures for absent,
   ambiguous and unauthenticated origins. Confirm each failure produces zero
   clicks/navigation. Then test a disposable conversation through New →
   Working → Review → Done and compare its conversation id after every
   move. Independently confirm a second open chat never moves.
6. In ChatGPT, open **Plugins**, select the private Loom/Tabby app, and use its
   per-app refresh action. If the existing App binding still presents the stale
   Tabby tool catalog, add the reviewed Philipedia MCP endpoint via **Plugins →
   + → Add custom MCP server** (Server URL or Secure MCP Tunnel), authenticate
   it, and verify the tool list contains `loom_chat_route` and
   `loom_origin_resolve`. This is an explicit user/account action; do not alter
   the App binding with Plugin Creator or claim refresh from server-side code.
7. Invoke `loom_origin_resolve` read-only from a disposable ChatGPT chat before
   enabling route writes. Verify its returned conversation id against the URL,
   route to Working, and check the persisted Loom task. Only then enable normal
   use. Retain Review and independent Done evidence as mandatory gates.

## Current blocker

Phase 2 now provides the standalone controller and local authenticated HTTP
Streamable MCP surface in [`philipedia_router/`](../philipedia_router/README.md).
It owns an explicit persistent Playwright profile, serializes all operations,
resolves origin fingerprints against bounded dedicated-profile conversations,
and verifies the exact conversation and target project IDs from browser routes.
It does not send messages or expose CDP. The service and Compose files are
review-only scaffolds and have not been installed or deployed.

Live integration remains blocked: Philipedia does not yet have the dedicated
profile authenticated by the user, and the ChatGPT private App has not refreshed
its MCP catalog. Therefore only mock browser/page tests are currently verified;
no claim is made that current chatgpt.com selectors work live. Follow the exact
one-time login, local disposable-chat test, secure-tunnel, and App refresh steps
in the module README. Once set up, the host path has no dependency on Mirai or
its Loom Unix socket.
