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

## Optional mobile delivery

The optional **outbound-only** ntfy transport activates only when an owner
explicitly configures `LOOM_NTFY_URL` to an HTTPS topic endpoint. If the ntfy
server requires a bearer token, point `LOOM_NTFY_TOKEN_FILE` to a protected
0600 file. Never commit the topic, token, personal data or credentials to Git.
Sender reports `accepted_by_provider`, *not* phone delivery. A subscribed
Android ntfy client and notification permissions require separate setup and
on-device verification.

**Important:** choice and approval notifications are intentionally **not
published to ntfy** in this version. An authenticated mobile response endpoint
with per-user authorization and one-time action tokens has not yet been
deployed. Desktop panel choices are supported by the local backend.
Untrusted notification links/third-party apps must never be allowed to
approve a destructive operation.

## Agent routing

- Routine progress: update the pinned Loom Working task, not a push.
- Important verified completion, user-actionable failure, time-sensitive alert:
  `loom_notify` with one reusable request ID.
- Genuine user judgment blocking the next step: `loom_request_decision`,
  persist the Work status as Waiting in SUMI, and poll `loom_notification_get`
  for an actual authenticated response. No answer means remain Waiting.
- Avoid floods. Low/normal notifications are bounded to 50 new entries/hour;
  agents should use stable IDs and dedupe.
- Phone delivery should not contain secrets or unnecessary personal content.
- Stop at existing authorization gates for risky external actions.

## Acceptance/evidence

`python3 -m unittest tests.test_notifications -v` covers database restart,
idempotency, one-use response, expiry, dismiss, delivery configuration,
limits, and MCP/backend contracts. Full Loom suite:
`python3 -m unittest discover -s tests`. A hardware-specific Mirai keyboard
test cannot pass on headless Philipedia and is confirmed to fail on the
unchanged baseline too. GUI/touch checks, active Mirai deployment, and Android
push/interactive responses must be verified independently before this full
feature can be marked Done.
