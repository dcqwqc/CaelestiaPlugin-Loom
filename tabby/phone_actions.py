"""One-use Loom mobile decision tokens, accessible only through Tailscale Serve.

Treat the token as a bearer secret; never use this to bypass OS/service approval gates.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from tabby.notifications import NotificationStore


def prepare(item, store: NotificationStore, base_url: str):
    if not base_url.startswith('https://') or urlsplit(base_url).query or urlsplit(base_url).fragment:
        raise ValueError('Callback must have an HTTPS base URL')
    if item['kind'] not in {'choice', 'approval'} or not 2 <= len(item['options']) <= 3:
        raise ValueError('Phone supports 2-3 distinct choices')
    now = int(time.time())
    expiry = min(int(item['expires_at'] or now + 3600), now + 3600)
    result = []
    with store._db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS phone_actions (
            token_hash TEXT PRIMARY KEY, notification_id TEXT NOT NULL,
            answer TEXT NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER)''')
        for answer in item['options']:
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO phone_actions VALUES (?,?,?,?,NULL)',
                       (hashlib.sha256(token.encode()).hexdigest(), item['id'], answer, expiry))
            result.append({'action': 'http', 'label': answer, 'url':
                           base_url.rstrip('/') + '/answer/' + token, 'method': 'POST', 'clear': True})
    return result


def choose(token: str, store: NotificationStore):
    if len(token) < 35 or len(token) > 100 or not all(c.isalnum() or c in '-_' for c in token):
        raise ValueError('invalid action')
    now = int(time.time())
    with store._db() as db:
        action = db.execute('SELECT * FROM phone_actions WHERE token_hash=?',
                            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not action or action['used_at'] is not None or action['expires_at'] < now:
            raise ValueError('Action missing, used or expired')
        store._expire(db, now)
        row = db.execute('SELECT * FROM notifications WHERE id=?', (action['notification_id'],)).fetchone()
        if not row or row['status'] not in ('pending', 'read') or row['kind'] not in ('approval','choice'):
            raise ValueError('Decision no longer actionable')
        if action['answer'] not in json.loads(row['options']):
            raise ValueError('Invalid option')
        db.execute("UPDATE notifications SET status='answered',response=?,updated_at=? WHERE id=?",
                   (action['answer'], now, row['id']))
        # Revoke this action and all of its alternative buttons in one transaction.
        db.execute('UPDATE phone_actions SET used_at=? WHERE notification_id=?', (now, row['id']))
        return store._public(db.execute('SELECT * FROM notifications WHERE id=?', (row['id'],)).fetchone())


class Handler(BaseHTTPRequestHandler):
    server_version = 'LoomPhone'
    sys_version = ''
    def log_message(self, fmt, *args):
        # Never log the path: it contains a bearer token.
        pass
    def respond(self, code, message):
        data = message.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        self.respond(405, 'POST only')
    def do_POST(self):
        # Requests reach this loopback listener through Tailscale Serve,
        # whose HTTPS hostname is accessible only inside the tailnet.
        if not self.path.startswith('/answer/') or '?' in self.path:
            self.respond(404, 'Not found')
            return
        if int(self.headers.get('Content-Length', '0') or '0') > 2048:
            self.respond(413, 'Too large')
            return
        try:
            store = NotificationStore()
            item = choose(self.path[len('/answer/'):], store)
        except ValueError:
            self.respond(409, 'Decision expired or already handled')
            return
        except Exception:
            self.respond(503, 'Decision service unavailable')
            return
        self.respond(200, 'Loom recorded: ' + item['response'])
        try:
            from loom_mcp import _ipc
            _ipc({'command':'notification-refresh'})
        except Exception:
            pass
        try:
            from tabby.notifications import deliver
            acknowledgement = store.create(title='You chose ' + item['response'],
                body='Your selection was recorded.', kind='status',
                request_id='phone-choice-ack-' + item['id'])
            if acknowledgement['created']:
                deliver(acknowledgement, store)
        except Exception:
            pass


def serve(host='127.0.0.1', port=8767):
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == '__main__':
    serve(os.environ.get('LOOM_PHONE_LISTEN', '127.0.0.1'), int(os.environ.get('LOOM_PHONE_PORT', '8767')))
