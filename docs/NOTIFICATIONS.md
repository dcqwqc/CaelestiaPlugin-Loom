# Loom notifications and decisions (v1.2)

This extends the existing Loom MCP. It is **not** a separate conversational AI
and does not create new ChatGPT chats.

## Behavior

- `loom_notify(title, body, kind, urgency, request_id)` stores an informational,
  status, or action-required alert, and attempts delivery. Use a stable
  `request_id` for one underlying event. The response distinguishes desktop
  delivery from optional phone-provider acceptance.
- `loom_request_decision(title, body, kind, options, urgency, ttl_minutes,
  request_id)` stores a durable question. `kind=approval` defaults to
  Approve/Reject; `kind=choice` uses 2–6 explicit options.
- `loom_notification_list` and `loom_notification_get` return actual state,
  including `pending`, `answered`, `dismissed`, `expired` and response.
  An unanswered, expired, or dismissed item **never** implies approval.
- Persistent SQLite DB defaults to
  `~/.local/share/tabby/notifications.sqlite3` (0600), or
  `LOOM_NOTIFICATIONS_DB` for isolated tests. It survives chat/plugin restarts.
- Set optional `LOOM_QUIET_HOURS=22:00-08:00` (local clock) to suppress
  nonurgent push attempts. High-urgency events bypass quiet hours.
- Backend `notification-refresh` exposes pending notifications to the
  Quickshell panel without starting Voice or stealing focus. Desktop choice and
  dismiss taps call backend `notification-answer` or `notification-dismiss`.
  These local UI IPC paths are deliberately **not** advertised as MCP agent
  tools. Same-user local processes may access the Unix socket, so a UI response
  is not cryptographic evidence of the human's identity. Do not treat it as
  authorization to bypass any separate platform or API confirmation.

## Mobile delivery, icon, and choice buttons

A configured ntfy Android subscription receives messages with a `Loom ·` title
and optional monochrome Loom icon (`LOOM_NTFY_ICON_URL`, HTTPS PNG). ntfy Android
still controls the *application* name/small status-bar icon; true app-level Loom
branding needs a separate branded Android APK. Rename the topic locally to Loom.

The standard delivery method remains outbound-only and requires
`LOOM_NTFY_URL=https://ntfy.sh/<unguessable-topic>` in a private mode-0600
`~/.config/tabby/notifications.env`. Don't include passwords or sensitive data
in messages on the public ntfy server, which stores unencrypted topics.

If `LOOM_PHONE_ACTION_BASE` points to a **tailnet-only HTTPS** service such as
`https://mirai.tailNN.ts.net/loom-phone`, approval/choice pushes can include
**2 or 3** HTTP action buttons. Larger choices stay on desktop rather than
silently truncating. Tokens are generated with 256 bits of randomness, stored
only as SHA-256 hashes, expire within one hour (or the request's sooner TTL),
and become unusable after the first successful answer. The callback server
listens only on loopback and is published through Tailscale Serve; do not expose
it through Funnel/the public Internet. The sender distinguishes provider
acceptance from actual Android delivery. Confirmation is sent to ntfy after a
button click (with no buttons attached).

The selected option becomes `status=answered` and `response=<label>` in the
canonical SQLite store, available to `loom_notification_get`. Android buttons
do NOT independently authorize risky actions (payments, code deployments,
security changes, etc.); obtain any required separate confirmation/permissions.
The bearer button URL is visible to anyone who can read the topic message, so
use a random topic, tailnet-only callbacks, short TTLs, and avoid sensitive
choices on shared or public channels.

Production service: `systemd/loom-phone-callback.service` binds
`127.0.0.1:8767`. Install with the same locked-down environment file as the
Loom MCP and route `/loom-phone` to port 8767 using `tailscale serve` without
changing the existing `/` route.
