# ChatGPT web workers and project routing

Loom runs background ChatGPT conversations through its bundled Zen controller and files every conversation into one of six ChatGPT projects. It never selects, reloads or restarts the user's own browser tabs or the Voice engine window; all worker and routing activity happens in hidden per-task worker windows.

## What this is, and what it is not

ChatGPT has **no public API for projects**. Neither Loom nor any agent has a native "move to project" tool. Loom drives the normal ChatGPT web UI (the conversation menu's *Move to project* control) through the Zen bridge, and trusts only what the page's own URL proves afterwards. When ChatGPT changes its UI, a control can go missing; Loom then reports a blocker instead of guessing. Agents must not describe these tools as ChatGPT-native features.

## The six lifecycle projects

The user creates these projects once in ChatGPT. Names are matched case-insensitively and must be unique.

| Lifecycle | Default name | Used for |
|-----------|--------------|----------|
| `new`     | New          | Blank worker chats right after creation, before any task is sent |
| `working` | Working      | Workers that are executing their task |
| `review`  | Review       | Settled output waiting for an independent review |
| `blocked` | Blocked      | Workers waiting on a human, a credential or a fix; rejected reviews |
| `vault`   | Vault        | Reference or parked conversations that are not active work |
| `done`    | Done         | Work approved by an independent reviewer |

To use different names, create `~/.config/tabby/chatgpt-projects.json`:

```json
{"projects": {"working": "Loom Working", "done": "Loom Done"}}
```

Unknown keys and blank names are ignored. A worker refuses to start until **all six** projects are visible in ChatGPT's sidebar, so a missing project surfaces at the very start, before any chat exists.

## Project identity and verification

A ChatGPT project route segment looks like `g-p-<32 hex>-<slug>`. The `g-p-<32 hex>` prefix is the project id; the slug follows the display name and changes when the project is renamed, so it is ignored. Custom GPT links (`/g/g-<id>` without `-p-`) are never treated as projects.

The current ChatGPT sidebar (observed live in Zen on 2026-10-09) renders projects as button rows **without links**. Each row has `New chat in <name>` and `Project actions for <name>` controls, so a project's id cannot be read from the sidebar. Loom resolves ids with `worker-resolve-projects`: in a blank window, it clicks the unique `New chat in <name>` control (falling back to the row itself, never its action menu) and reads the id from the `/g/<segment>/project` route. Two controls with the same name are refused as ambiguous. Creation resolves all six projects in the task's still-blank window. Moves and routes resolve their target in a separate throwaway window, so a chat window is never navigated away. If ChatGPT does not navigate to a project route, resolution fails with `project-route-not-observed`.

A move is accepted only when **both** of these hold:

1. The page actor reports the move, and its reported project id and conversation id match the request.
2. A separate status read of the worker window shows `https://chatgpt.com/g/<project>/c/<conversation>` where the project id is the requested one and the conversation id has not changed.

If the move control is missing, the menu has two entries with the same name, or the route never changes, the move fails closed.

## Creating a worker (`loom_web_worker_create`)

Creation is *move first*. Every step is written to `~/.config/tabby/working.json` before the browser action it guards:

| Phase | Meaning | On retry |
|-------|---------|----------|
| `reserved` | Durable record created; no browser I/O yet | Starts the flow |
| `prepare-failed`, `project-not-found` | Worker window or projects unavailable; nothing was typed | Starts again |
| `bootstrapping` → `bootstrapped` | A short, non-substantive setup message is typed into the **New** project's own composer, so the chat is born in New. The real task is not in it | — |
| `bootstrap-failed` | Definitely not sent | Starts again |
| `bootstrap-unverified` | Outcome unknown. If the task's own worker window still shows a chat, that chat is adopted; otherwise the worker stays blocked and **no second chat is created** | Adopt or stay blocked |
| `moving` → `working-verified` | Chat moved to **Working** and the Working project id verified; the user-turn count is recorded | — |
| `working-move-failed` | Move not verified; nothing substantive was sent | Reopens the same chat and moves again |
| `prompt-sending` → `running` | The task prompt is sent **exactly once** | — |
| `prompt-not-sent` | Definitely not sent (for example, composer missing) | Sends again, at most 3 attempts in total |
| `prompt-unconfirmed` | Outcome unknown | Reloads the chat and reads it: prompt present → `running`; prompt provably absent → send again; anything else → stays blocked |

Several guards keep the prompt from being delivered twice:

* `request_id` is the idempotency key. Reusing it never creates a second chat, and reusing it with a different prompt is rejected.
* The page actor checks the route (conversation id and Working project id) and the expected user-turn count in the same call as the click, and checks the route again just before clicking. A navigation or an extra turn aborts the send.
* The controller keeps a send-key ledger, so a re-delivered command file cannot send twice.
* The Python client never re-issues text-sending commands (`worker-create`, `worker-bootstrap`, `worker-send-prompt`) after a timeout. A timeout counts as an unknown outcome, never as a failure.

The tool returns at once with the reserved task; poll `loom_web_worker_inspect`. Pass `created_by` with the creating agent's identity so later review independence can be checked.

## Observing and reviewing

`loom_web_worker_inspect` returns the persisted phase, the verified `projectId`, `lifecycle`, `lastError` and live response state. Only reads from the worker's own conversation count. Once a non-empty response has settled, Loom moves the chat to **Review**. The worker becomes `awaiting-review` only after that move verifies; otherwise it stays `running`, records `lastError`, and retries no more than once a minute. Reaching Review is never evidence that the work is correct.

`loom_web_worker_review` needs a decision, a reviewer and evidence:

* The worker must be in the verified Review project.
* The reviewer must differ from `created_by`.
* The live chat must still show exactly the reviewed response and must not be generating. Otherwise the review is refused, so a stale or changed answer cannot be approved.
* **approved**: the chat moves to Done, the Done project id is verified, then the task becomes `done` and its window closes. If the move cannot be verified, the task stays `awaiting-review`.
* **rejected**: the decision is always recorded (`review-rejected`). The chat is filed in Blocked when that move verifies; otherwise `routed: false` and `lastError` say so.

Generic `loom_task_done` / `loom_task_update(status=done)` / `loom_task_reopen` are refused for web workers.

## Routing (`loom_web_worker_route`, `loom_chat_route`)

* `loom_web_worker_reconcile` (backend `web-worker-reconcile`) resumes an interrupted worker from its persisted phase through the same state machine. It never sends blind.
* `loom_web_worker_route(task_id, lifecycle, reason)` files a worker into `blocked` or `vault` (a reason is required) or back into `working`. It never sends a message. Moving back to Working resumes monitoring from the current assistant count. `new` and `done` cannot be targets. Routing is only allowed once creation reached `running` or later.
* `loom_chat_route(lifecycle, url)` routes ONLY the ChatGPT conversation named by a supplied canonical URL. It uses a dedicated hidden window and verifies exact conversation/project IDs. A tracked worker's URL is delegated to its owning manager. Done still requires independent reviewer and evidence.
* Remote MCP cannot infer the ChatGPT conversation that called the tool. The old `current=true` silently referred to Loom's own Voice engine, sometimes a totally unrelated Reply hi chat. This remote tool now **requires an explicit URL** and fails closed if none is available. The worker must obtain its own URL from its browser/session context or durable worker record. Never substitute the last-active tab, Voice chat, or a guessed URL.
* `loom_voice_chat_route` is explicitly for Loom's OWN Voice-engine conversation; it refuses active-Voice routing by default and is never a proxy for the MCP caller.
* If chat origin is unavailable, mark routing blocked but preserve task progress. Independent authorized code/UI work can continue if its execution policy permits. Never report a project move without verified browser URL state.

## Deployment notes

The backend copies the bundled bridge files into the Zen profile when it starts. This **does not** load them: Firefox keeps the old controller until the Sine mod is toggled or Zen restarts. Until Zen reports controller version `0.11.0`, `loom_web_worker_create` refuses before reserving anything, so an old controller can never run the old send-first flow. Reload the mod only when no Voice session is active.

`loom_web_worker_route`, `loom_chat_route` and `loom_web_worker_review` are refused the same way, before any browser I/O, because they depend on the new move verification. Voice and all other Loom tools keep working with the old controller.

## Reconciliation with a dirty checkout

Never copy files over a checkout that has uncommitted work. Fetch the branch into a clean worktree, inspect `git diff <base>...<branch>`, and merge or cherry-pick. Resolve overlaps in `backend.py`, `tabby/zen.py`, `loom_mcp.py` and the Zen bridge by hand. Run `python3 -m unittest discover -s tests` and `node --test tests/` before installing.
