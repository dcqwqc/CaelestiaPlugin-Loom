# Loom Fast Path + Orbit Handoff v1 — isolated staging checkpoint

Status: **working staging implementation, not a release candidate and not deployed**.
User approval is captured as Philipedia Loom idea `f3a767487eb4d059fefd5973`;
canonical SUMI Loom source page revision 142. No supervisor mission ID was
safely admitted.

## Implemented in this isolated checkout

- Explicit allowed app intents in English/German, no arbitrary shell text.
- Per-client active desktop routing; headless Philipedia cannot guess presence.
- Real compositor observation required for a `verified` app focus receipt.
- Desktop ID launch through `gio launch`, with no shell interpolation.
- SUMI page UUID encoded into a fixed `sumi://page/<uuid>` URI.
  URI dispatch deliberately returns `dispatched_unverified`: there is no
  independently observed SUMI page-ID callback yet.
- SQLite requests are admitted before possible side effects. Replays reuse
  receipts; a crash in the uncertain interval refuses blind relaunch.
- One public Loom MCP action `loom_fast_action` on the local desktop, with a
  fixed authenticated forwarding route from headless Philipedia to Mirai only.

## Integration/release gates still open

1. Validate actual voice/chat → `loom_fast_action` tool selection and measure
   voice transcription → action latency with warm/live Mirai and Tachibana.
2. Independently confirm the exact destination SUMI page ID after navigation.
3. Reconcile the real active-device presence registry. Philipedia currently
   permits explicitly named Mirai, never a guessed active desktop.
4. Bind an idempotent request to the **existing Orbit Lead/Supervisor**,
   preserving actual job IDs, independent reviews and no duplicate dispatch.
   The existing Loom mission creation API is **not** an Orbit admission receipt.
5. Restore the externally facing Loom connector (HTTP 429 observed) and check
   the standalone Tachibana Loom WebView. Tachibana's Caelestia process runs;
   an inactive secondary systemd unit is not evidence the shell is down.
6. Repair the failed Orbit/SUMI status projection (`InvalidPayload`), then
   prove end-to-end correct status.
7. Independently review this branch, inspect changes and run live acceptance
   tests. Production deployment/service restarts require separate approval.

## Sandbox / provider blockers confirmed 21 October 2026

- System Bubblewrap 0.12 fails to bind mounts with `Function not implemented`.
- Codex-bundled Bubblewrap can bind mount but cannot mount `/proc` in a
  new PID namespace on this host. A no-/proc jail makes the Codex CLI fail.
  Do not weaken the sandbox; address as a separate trusted host recovery.
- Isolated Docker development image built. Codex GPT-6.1 Sol returned a model
  entitlement error, GPT-5.6 Sol returned a usage limit without editing files.
- Claude Opus 5.5 returned a five-hour usage limit without editing files.
- Antigravity Opus 4.6 produced only an initial planning statement and then
  exited without changes.
- Gemini CLI independent reviewer failed authentication with Unsupported Client.
  **No independent review has passed.**

## Tests

Focused mocked tests cover app lookup, focus, dispatch, duplicates, crashes,
denied targets, SUMI UUIDs, offline routing and MCP forwarding.
The larger suite has one test requiring Mirai's physical
`AT Translated Set 2 keyboard`, which does not exist on headless Philipedia.
Do not turn that environment-specific failure into a product pass or falsely
claim physical keyboard verification.

This document and source are staging only; the running services and device
installations are unchanged.
