# Loom permanent phone notification specification (2026-10-19)

Design: elegant native Android notification, functional, quiet, monochrome.
The Android app already draws its small left icon.

- Title: product or task + middot + specific status, e.g. SUMI · Project ready.
  Soft target 30-56 chars, one line; do NOT prepend Loom to the title since
  Android displays Loom as the sender already.
- Body: one meaningful sentence or two short lines, preferably under 140 chars.
  Do not truncate critical safety/security/approval/deadline information.
- Icons: keep only the **native small left** app icon. Never send ntfy custom
  icon, attachment, large right duplicate logo, decorative banner or tags.
- Colors and emojis: no colorful decorative emojis or ntfy tags. Prefer no
  emoji, or monochrome symbols such as the arrow ↗, middot and dash.
  A colored emoji is acceptable only for genuinely topical subjects such as
  pineapple when actually discussing pineapples. SUMI's vermilion orange is
  a legitimate UI palette accent, not a notification decoration.
- One action: exactly one **view** action for ordinary actionable alerts,
  concise verb/noun, ideally 12-24 chars, e.g. Open in SUMI ↗.
  Decisions may have explicit Approve/Reject/Choice buttons instead.
- Tapping notification body: its click URL must equal the action URL.
  Do not print raw links in the description.
- SUMI redirect: ordinary /project/UUID and /page/UUID URL, NEVER the
  query flag manual=1 for an Open action. The web page tries Android intent
  launch once, preserving its clickable fallback (Chrome may require a tap).
  Preview-only links may use manual=1 if explicitly named Preview.
  Prefer verified HTTPS Android App Links once actually implemented.
- Recovery: link to signed update and troubleshooting remains in the
  SUMI-designed landing page, not copied into every short notification.
- Private links: plain HTTP destinations only to 100.64.0.0/10 tailnet,
  never include credentials or provider tokens.
- Reliability: use stable idempotency request ID, respect quiet hours unless
  genuine urgency, avoid spam, and remember provider acceptance does not
  prove handset display or consent.

Example:
Title: SUMI · Project ready
Body: Diagrams, Whiteboard & Live System Maps
Action: Open in SUMI ↗
Click: the same verified private SUMI launch URL as the action

Limitations: Android/ntfy owns notification typography and chrome. The
sender can omit the duplicated image and supply clear actions but cannot
force a particular OS notification layout or bypass external-app security.
