# Hive ledger ↔ Tabby Working (HAG-23)

One task store: the Hive ledger, `tasks.json` in the hive on Philipedia
(`/home/qwqc/HarnessAgents/hive`). Tabby Working is a view of it, never a
second store.

## What shows in Working

| Card status | Working |
|---|---|
| `todo` (backlog) | nothing |
| `doing` | task `HAG-n · title`, status `working`, summary = assignee |
| `blocked` | same task, status `blocked`, summary = `assignee · blocked: <reason>` (card `blocked` field, else the open `humanQA` question) |
| `done` | task completed (`done · <result>`), removed after 10 minutes |
| back to `todo`, archived, deleted | task removed |

The HAG-13 chip counts `working` tasks, so it counts the cards in `doing`.

## Editing from Tabby (reverse path)

| In Tabby | Card |
|---|---|
| complete the task (`tabby_task_done` / `work-complete`) | `done`, `result` = the summary given (only if the card is still `doing`/`blocked`) |
| set it `blocked` / `waiting` | `blocked`, `blocked` = the summary given (only if the card is `doing`) |
| set it `working` again | `doing`, `blocked` cleared (only if the card is `blocked`/`done`) |
| remove it from Working by hand | card unchanged; the task stays hidden until the card's status changes |

Each card edit is conditional on the card's status, so an old Tabby view
never overrides a newer card (the edit is skipped, logged, and Working is
set back to what the card says). Summary edits while a task is `working`
are not copied to the card.

## Pieces

- `scripts/hive_task.py` — the ledger's one serialized writer (stdlib only).
  Installed on Philipedia as `<hive>/bin/hive-task` for agents and Loom:

  ```
  hive-task show HAG-23
  hive-task update HAG-23 status=blocked blocked="needs Chrome" --if-status doing
  hive-task complete HAG-23 "what was done"
  hive-task create "Title" assignee=claude-x priority=high
  ```

  `key=value` sets text, `key:=<json>` any JSON (`null` removes a field).
  Set `HIVE_AGENT_ID` (or `--by`) so the card records `updatedBy`.
- `scripts/hive_mirror.py` — the mirror. One pass reads the ledger over SSH,
  lists Working over Tabby's IPC socket, applies Tabby-side edits to the
  ledger first, then makes Working match the ledger. The mapping (card id →
  Working task id, and what the mirror last wrote) is in
  `~/.local/state/tabby/hive-mirror.json`; if it is lost, mirrored tasks are
  re-adopted by their `HAG-n · ` title, never duplicated.
- `scripts/tabby_loom_sync.py` — the existing timer job (`tabby-loom-sync.timer`)
  now runs the mirror every 30 s, then the older LOOM-kernel mappings as before.
  Config `~/.config/tabby/loom-sync.json`:
  `{"hive": {"enabled": true, "host": "philipedia", "root": "/home/qwqc/HarnessAgents/hive", "done_linger_s": 600}}`
  (all optional). Edits reach the ledger by piping this checkout's
  `hive_task.py` to `python3 -` over SSH, so the two machines can never run
  different writer versions.

## The writer protocol

Shared with the Operator MCP (`withLedgerLock`, Sumi `feat/hag21-operator-mcp-3`):

1. `mkdir tasks.json.lock`, then write `owner` = `{pid, time}`. A lock older
   than 30 s, or whose pid is dead, is stale; it is renamed away before it is
   removed, so two writers that both judged it stale cannot both take it.
2. Read the ledger, change one card, stamp it the way the hive app does
   (`createdAt`, `startedAt` on doing, `doneAt` on done, `reopenedAt`,
   `updatedAt`; plus `updatedBy`), write a temp file beside it.
3. Compare the ledger's bytes with what was read; if they differ, start over.
4. `os.replace` the temp file over the ledger, release the lock.
5. 50 ms later, outside the lock, re-read: if the card's `updatedAt` is older
   than ours (a stale copy was written over ours), redo the edit; if it is
   newer, someone edited after us and we leave it.

New ids come from the ledger's counter (`ticket.prefix`/`ticket.next`), never
below an id already used in `tasks.json` or `tasks-archive.json`.

## Why the hive app's own writes are not under the lock

The hive app on Philipedia is Munder (`~/Applications/Munder-Difflin.AppImage`),
an unmodified upstream build. It writes `tasks.json` in `HiveManager.writeTasks`,
`keyAgentTasks` (re-stamps the file each router tick after any outside edit)
and `applyTaskHygiene`, each as a synchronous `readFileSync → merge →
writeFileSync(tmp) → renameSync` with no lock. Its code cannot be changed
from here, so it cannot take ours. What that leaves:

- A Munder write that lands while we hold the lock but before step 3 is seen,
  and we start over: both edits survive.
- A Munder write that read before our rename and renamed after it would put
  back a stale card; step 5 sees the older `updatedAt` and redoes ours. Munder
  then merges our edit on its next tick.
- A Munder write that renames between our step 3 and step 4 is lost. That gap
  is two syscalls (microseconds), and Munder writes the ledger only on UI
  actions, hygiene sweeps and after outside edits.

Fully closing that last gap needs Munder to honour the same lock (or own a
narrow mutation API). Agents that edit `tasks.json` by hand have the same,
larger, problem; they should use `hive-task` instead.

## Tests and demo

`python3 -m unittest tests.test_hive_mirror` — writer (ticket counter,
stamps, guards, six concurrent processes with no lost update, stale/live
locks, an unlocked writer before the compare and after the rename, a newer
edit not redone over) and mirror (full todo→doing→blocked→done lifecycle,
idempotent second pass, reverse complete/block/resume, stale Tabby edits,
manual dismissal, lost mapping, write failure) against the real `WorkingStore`.
