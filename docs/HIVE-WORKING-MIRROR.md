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

When Philipedia cannot be reached (Mirai's Tailscale link drops for a minute
now and then), a pass changes nothing on either side and logs why; a Tabby
edit that could not be written is retried on the next pass. Retries are safe:
every reverse edit is conditional on the card's status.

## The writer protocol

Shared with the Operator MCP (`withLedgerLock`, Sumi `feat/hag23-operator-ledger`):

1. `mkdir tasks.json.lock`, then atomically write `owner` = `{pid, time, token}`,
   where `token` is new for every acquisition. A lock older than 30 s, or whose
   pid is dead, is stale; it is renamed away before it is removed, so two
   writers that both judged it stale cannot both take it.
2. Read the ledger, change one card, stamp it the way the hive app does
   (`createdAt`, `startedAt` on doing, `doneAt` on done, `reopenedAt`,
   `updatedAt`; plus `updatedBy`), write a temp file beside it.
3. Compare the ledger's bytes with what was read; if they differ, start over.
4. Check `owner` still carries our token; if the lock was taken over (we were
   paused for more than 30 s), write nothing and start over.
5. `os.replace` the temp file over the ledger. Release the lock only if `owner`
   still carries our token (rename it aside, re-check, then delete; a lock that
   turns out to be someone else's is put back). A writer that was paused and
   lost its lock therefore never deletes the new owner's lock.
6. 50 ms and 300 ms later, outside the lock, re-read: if the card's `updatedAt`
   is older than ours (a stale copy was written over ours), redo the edit; if it
   is newer, someone edited after us and we leave it.

New ids come from the ledger's counter (`ticket.prefix`/`ticket.next`), never
below an id already used in `tasks.json` or `tasks-archive.json`. A missing
`tasks.json` starts a new, empty ledger and a missing archive means nothing is
archived; an **existing** file that is empty, not valid JSON or has no task list
is an error and nothing is written (reading it as empty could reuse ticket ids).

Remaining limit between cooperating writers: steps 4 and 5 are two syscalls; a
writer paused for more than 30 s exactly between them could still rename once
over a newer owner's work. That needs a 30-second stall at one instruction.

## Why the hive app's own writes are not under the lock

The hive app on Philipedia is Munder (`~/Applications/Munder-Difflin.AppImage`),
an unmodified upstream build. It writes `tasks.json` in `HiveManager.writeTasks`,
`keyAgentTasks` (re-stamps the file each router tick after any outside edit)
and `applyTaskHygiene`, each as `readFileSync → merge → writeFileSync(tmp) →
renameSync` with no lock. Its code cannot be changed from here, so it cannot
take ours. **Protection against it is best effort, not a guarantee:**

- A Munder write that lands while we hold the lock but before step 3 is seen,
  and we start over: both edits survive.
- A Munder write that read before our rename and renames before our 300 ms
  check puts back a stale card; the check sees the older `updatedAt` and
  redoes ours.
- **Lost:** a Munder write that renames between our step 3 and step 5 (two
  syscalls apart) is overwritten by ours.
- **Lost:** a Munder write that read before our rename but renames after our
  last check (it was descheduled or paused for more than ~300 ms between its
  read and its rename) silently undoes our edit, after we reported success.
  There is no upper bound on that pause from our side.

Munder's read→rename normally runs in one synchronous JavaScript tick, so both
cases need unlucky timing, and Munder writes the ledger only on UI actions,
hygiene sweeps and after outside edits. But this is not a single serialized
writer while Munder stays an unlocked second writer. The board is still
recoverable: the hive directory is a git repository that Munder commits on its
writes, and the mirror converges to whatever the ledger says on the next pass.
Strict safety needs Munder to honour the same lock (or own a narrow mutation
API); that is a decision about the server app, outside these branches.
Agents that edit `tasks.json` by hand have the same, larger, problem; they
should use `hive-task` instead.

Rolling back by removing `bin/hive-task` is a safety downgrade, not only a
feature rollback: Loom then goes back to its old whole-file SSH edit, and the
Operator to its in-process fallback (same lock, token and `if_status` rules,
but no post-write re-checks).

## Tests and demo

`python3 -m unittest tests.test_hive_mirror` — writer (ticket counter,
stamps, guards, six concurrent processes with no lost update, stale/live
locks, a paused owner whose lock was taken over neither writes nor releases
the new owner's lock, empty/corrupt ledger and archive fail closed, an
unlocked writer before the compare and after the rename at both re-checks, a
newer edit not redone over) and mirror (full todo→doing→blocked→done lifecycle,
idempotent second pass, reverse complete/block/resume, stale Tabby edits,
manual dismissal, lost mapping, write failure) against the real `WorkingStore`.
