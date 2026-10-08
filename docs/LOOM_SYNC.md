# Loom Office to Loom Companion Working task sync

`loom-office-sync.timer` checks LOOM once per minute and marks mapped Loom Working cards `done` at 100% when their LOOM mission reaches a verified `done` outcome. Finished cards stay pinned until the user removes them.

Configuration lives at `~/.config/tabby/loom-sync.json`:

```json
{
  "mappings": [
    {
      "loom_task_id": "261005-4ha7",
      "tabby_task_id": "1eca64cc94884a52",
      "host": "philipedia",
      "loom_dir": "/home/qwqc/loom"
    }
  ]
}
```

The sync uses SSH in batch mode and Loom's local mode-0600 IPC socket. It does not expose a shell tool through MCP and does not remove completed cards.

The tabby_task_id field is a legacy storage key for a Loom Working task.
