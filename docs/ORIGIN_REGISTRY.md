# Authoritative origin registry

Philipedia is the sole persistent store for Loom origin references. Mirai uses
the authenticated HTTP adapter whenever `LOOM_ORIGIN_REGISTRY_URL` is set and
never falls back to its local SQLite database if Philipedia is unreachable.
Constructing the backend does not contact Philipedia; register and resolve calls
use a three-second timeout and fail closed.

The registry does not discover a ChatGPT conversation ID. Native ChatGPT mobile
MCP calls do not provide an authoritative per-chat ID, so callers must retain a
stable `origin_ref` and explicitly register the canonical conversation URL when
it is known.

## Production deployment

Use Philipedia's actual Tailscale IPv4 address for `HOST`; never use
`0.0.0.0`. Install the reviewed checkout in the normal Loom plugin location,
then create the database directory, independent server credential file, and
non-secret service environment:

```sh
HOST=100.x.y.z
install -d -m 700 ~/.config/tabby ~/.local/share/tabby
umask 077
openssl rand -base64 48 > ~/.config/tabby/origin-registry-server.token
cat > ~/.config/tabby/origin-registry-server.env <<EOF
LOOM_ORIGIN_REGISTRY_HOST=$HOST
LOOM_ORIGIN_REGISTRY_PORT=8771
LOOM_ORIGIN_REGISTRY_DB=$HOME/.local/share/tabby/origin-registry.sqlite3
EOF
chmod 600 ~/.config/tabby/origin-registry-server.env
install -m 644 systemd/loom-origin-registry.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now loom-origin-registry.service
```

The example token is never placed in an environment variable, command line, URL,
or Git. Transfer it once over an authenticated channel into a separate mode-0600
file on Mirai, then configure the Mirai Loom backend environment:

```sh
install -m 600 /secure/staging/origin-registry.token \
  ~/.config/tabby/origin-registry-client.token
cat >> ~/.config/tabby/loom.env <<EOF
LOOM_ORIGIN_REGISTRY_URL=http://100.x.y.z:8771
LOOM_ORIGIN_REGISTRY_TOKEN_FILE=$HOME/.config/tabby/origin-registry-client.token
EOF
```

Restrict port 8771 to the Mirai Tailscale identity with the tailnet ACL/firewall.
Confirm the service binds only the chosen address with `ss -ltn` and inspect
`systemctl --user status loom-origin-registry.service`. Do not put the bearer
value in a health-check command or log.

## One-time migration from Mirai

Perform the cutover in a maintenance window so no registration can land between
the backup and switching Mirai to remote mode. Substitute the actual legacy path
if `LOOM_ORIGIN_REGISTRY_DB` previously overrode it.

1. Stop the Mirai Loom backend to freeze registry writes. Do not modify its
   checkout.
2. On Mirai, create a consistent SQLite backup and verify it before transfer:

   ```sh
   umask 077
   sqlite3 ~/.local/share/tabby/origin-registry.sqlite3 \
     ".backup '$HOME/origin-registry.migration.sqlite3'"
   test "$(sqlite3 ~/origin-registry.migration.sqlite3 'PRAGMA integrity_check')" = ok
   sqlite3 ~/origin-registry.migration.sqlite3 \
     'SELECT (SELECT count(*) FROM origins), (SELECT count(*) FROM operations);'
   sha256sum ~/origin-registry.migration.sqlite3
   ```

3. Copy that backup to a mode-0700 staging directory on Philipedia over SSH.
   Re-run `sha256sum` there and compare it with Mirai's value. Verify
   `PRAGMA integrity_check` again.
4. With the Philipedia registry service stopped, install the verified backup
   atomically as `~/.local/share/tabby/origin-registry.sqlite3`, set mode 0600,
   start the service, and resolve representative references through an
   authenticated client. Compare the `origins` and `operations` counts recorded
   before transfer.
5. Configure the two Mirai client variables shown above, restart its backend,
   and verify register plus resolve through the normal Loom API. If Philipedia
   is offline, that API must report an unavailable registry; a new local SQLite
   file is a failed cutover, not a recovery path.
6. Retain the verified migration backup until the remote cutover has been
   observed working. Keep the old Mirai database read-only as rollback evidence;
   never run both databases as writable authorities.

The service exposes only `POST /v1/register` and `POST /v1/resolve`. Both require
`Authorization: Bearer ...`, JSON bodies no larger than 16 KiB, and exact
request fields. It intentionally suppresses per-request logging because origin
references and validation messages may be sensitive.
