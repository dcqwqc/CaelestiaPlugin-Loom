# Loom web-worker tracking — live verification, 2026-10-09

## Scope and evidence

- Canonical task: `e8fca3e490064052`
- Request id: `loom-hi-web-smoke-20261009-2047`
- User prompt: `Reply with exactly hi and nothing else.`
- Verified browser URL: https://chatgpt.com/c/6ac936de-3ccc-83eb-a6e3-b7b15536fc14
- Sine runtime: bridge `0.10.15` loaded in Zen on Mirai.
- Browser actor read-only checks: `promptAcknowledged: true`, `assistantCount: 1`, `assistantText: "hi"`.
- The original prompt was **not** resent during recovery.

## Code and tests

- `b8078fe` — URL recovery and nonempty response gating.
- `b512663` — durable prompt acknowledgement, Review project transition, live response validation, recovery tests.
- `9f6ec95` — CSS-hidden chat actions hover handling and route recovery after a move actor timeout.
- Full suite at release: `python -m unittest discover -s tests -q` — 158 tests pass.
- Actor suite: `node --test tests/web_worker_actor.test.mjs` — 4 tests pass.
- Changes pushed to origin `main`.

## Unresolved blocker (do not mark Done)

Real UI move from ungrouped conversation into Working was **not** confirmed.
The live Sine actor returned `move-project-control-not-found` and `chatActionFound: false`; earlier attempts returned `actor-query-timeout`. Project catalog did resolve `Working` to `g-p-6ac2ac2d6f308191841e967e2fb304ac`, but *not* the move itself. A later tool attempt was blocked by a safety check: do not route around that block.

The original worker was restored through the **running Loom backend IPC** (`web-worker-reconcile`) and verified with `web-worker-inspect`. The persisted task remains `status=blocked`, `phase=project-not-found`, with its canonical URL. A separate `WorkingStore` process must not write the same JSON file while the daemon is active, because an in-memory snapshot can clobber changes from that second process.

## Exit criteria

1. Read-only DOM diagnostic of the dedicated worker after sidebar hydration identifies the actual chat-specific menu and handles virtualized rows.
2. A live move to Working is verified by the canonical `/g/<project-id>/c/<conversation-id>` route and same conversation ID.
3. Run a fresh idempotent smoke worker, confirm exactly one delivered prompt and the assistant response.
4. Verify Working → Review project membership before review status; independently validate answer and only then move to Done.
5. Verify task persistence over Loom restart and Zen restart with no duplicate prompt, no missing URL, no disruption to active voice.

Related existing GitHub issue: https://github.com/dcqwqc/CaelestiaPlugin-Loom/issues/2

Last known safety state: Zen was restarted only when Loom Voice reported inactive; no service/browser restarts were performed while Voice was active. Other agent edits in `backend.py` and `bridge/zen/.hey-tabby.uc.js` were left untouched and are not part of this report commit.
