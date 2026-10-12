# Philipedia ChatGPT router

This is a standalone, fail-closed browser router for a **dedicated** ChatGPT
profile on Philipedia. It does not use Mirai, Zen IPC, an existing bot profile,
Chrome DevTools/CDP, plaintext account credentials, or a "current tab" alias.
It never sends a chat message. Project changes are accepted only after the
browser URL independently proves the requested conversation ID and a project
ID learned by selecting the exact project in a separate browser page.

## Install and configure

Node 22+ is required. From this directory:

```sh
npm ci
npx playwright install chromium
install -d -m 700 "$HOME/.local/state/loom-chatgpt-router/profile" "$HOME/.config/loom/philipedia-router"
cp config/projects.example.json "$HOME/.config/loom/philipedia-router/projects.json"
openssl rand -hex 32 > "$HOME/.config/loom/philipedia-router/token"
chmod 600 "$HOME/.config/loom/philipedia-router/token"
```

Review every project name in `projects.json`. All six names must be present and
unique. The service resolves the stable `g-p-...` ID from ChatGPT's actual route;
IDs must never be entered or guessed in configuration.

## One-time sign-in

Stop the router before opening the profile; Chromium persistent profiles must
have a single owner. In a private graphical session on Philipedia (for example,
a local console or an administrator-controlled SSH display), run:

```sh
export LOOM_ROUTER_PROFILE="$HOME/.local/state/loom-chatgpt-router/profile"
npm run auth
```

Sign in only inside the opened `https://chatgpt.com/` window. Do not type a
password, session token, cookie, or MFA code into the shell, environment file,
logs, or repository. Press Enter in the shell after the site visibly shows the
dedicated signed-in account. Never copy another browser's profile or cookies.

Create `~/.config/loom/philipedia-router.env` with absolute paths:

```sh
LOOM_ROUTER_PROFILE=/home/USER/.local/state/loom-chatgpt-router/profile
LOOM_ROUTER_LEDGER_FILE=/home/USER/.local/state/loom-chatgpt-router/routes.json
LOOM_ROUTER_TOKEN_FILE=/home/USER/.config/loom/philipedia-router/token
LOOM_ROUTER_PROJECTS_FILE=/home/USER/.config/loom/philipedia-router/projects.json
LOOM_ROUTER_HOST=127.0.0.1
LOOM_ROUTER_PORT=4319
LOOM_ROUTER_HEADLESS=true
```

Load it and run `npm start`. `GET /healthz` is unauthenticated and reports only
process health. `GET /v1/status` requires `Authorization: Bearer …` and reports
whether the dedicated profile has positive logged-in UI evidence. A missing or
uncertain identity is reported as not authenticated.

The user-systemd unit requires adjusting `WorkingDirectory` to your reviewed deployment location. The provided user-systemd and Compose files are optional reviewable scaffolds;
they are not installed or enabled by this repository. Adjust the systemd
`WorkingDirectory` to the reviewed checkout location. Compose uses host
networking but the application still binds only `127.0.0.1`. The application
rejects `0.0.0.0` and non-loopback addresses. Remote access must use an
authenticated Tailscale/SSH secure tunnel to the loopback port. There is no CDP
listener.

## API and MCP

All routes except `/healthz` require the bearer token.

* `GET /v1/status`
* `POST /v1/origin/resolve` — `{ "origin_messages": ["exact message"] }`
* `POST /v1/route` — `{ "url": "https://chatgpt.com/c/…", "lifecycle": "working" }`
* `POST /mcp` — stateless HTTP Streamable MCP endpoint exposing
  `loom_router_status`, `loom_origin_resolve`, and `loom_chat_route`

The route accepts either `url` or `origin_messages` (1–3 exact last
user messages). For a manual chat not open on Philipedia, the dedicated profile
checks recent browser-visible sidebar conversation links and verifies the exact
message suffix; it refuses absent, duplicate, or too-many candidates instead of
guessing. This is bounded and may still require a URL if the chat is not
visible in the sidebar.

The router persists verified lifecycle state in a mode-0600 ledger file.
`done` is refused unless the same conversation was previously recorded in
verified `review`, still loads from that Review project, and a different
reviewer plus non-empty evidence are supplied. The router does not claim the
evidence has been independently audited by itself.

Origin resolution compares the exact normalized suffix of the last 1–3 user
messages across bounded conversation candidates visible to the dedicated
profile. Zero matches, multiple matches, too many candidates, redirects,
authentication uncertainty, and inconsistent IDs are hard failures. Passing a
canonical URL is the preferred and strongest identity path.

After local status and disposable-chat tests succeed, refresh the private
ChatGPT App's MCP tool catalog or add this reviewed MCP endpoint via a secure
tunnel. That account action is outside this module. Do not enable routing until
the client visibly lists all three tools and a disposable chat proves the same
conversation ID before and after a move.

## Verification state

`npm test` uses mock browser/page snapshots and does not claim site
compatibility. Live integration remains deliberately blocked until the one-time
dedicated-profile sign-in and ChatGPT App tool refresh occur. Test a disposable
conversation through lifecycle moves and independently compare its URL IDs
before treating the selector set as production-ready; ChatGPT UI has no public
project API and can change.
