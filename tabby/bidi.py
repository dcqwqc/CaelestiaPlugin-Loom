"""Minimal WebDriver BiDi client (stdlib only) for Loom agent browser workspaces.

Only what Loom needs: open a session on a private Firefox's loopback remote
port, find the top-level browsing context, navigate, and evaluate scripts that
return JSON strings.  Input is *not* sent through BiDi: Loom moves the agent's
real X pointer inside the private display so the overlay shows true positions.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
from typing import Any


class BidiError(RuntimeError):
    pass


class WebSocket:
    """Client-side RFC 6455 text frames; enough for a loopback BiDi endpoint."""

    def __init__(self, host: str, port: int, path: str, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BidiError("websocket handshake closed")
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise BidiError(f"websocket handshake failed: {status.decode(errors='replace')}")
        self._buf = head.split(b"\r\n\r\n", 1)[1]
        self._send_lock = threading.Lock()

    def send(self, text: str) -> None:
        data = text.encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self._send_lock:
            self.sock.sendall(bytes(header) + mask + masked)

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise BidiError("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self) -> str:
        parts: list[bytes] = []
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            if b2 & 0x80:
                mask = self._read(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            else:
                payload = self._read(n)
            if opcode == 0x8:
                raise BidiError("websocket closed by browser")
            if opcode == 0x9:  # ping -> pong
                with self._send_lock:
                    mask = os.urandom(4)
                    self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask +
                                      bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
                continue
            if opcode in (0x1, 0x0, 0x2):
                parts.append(payload)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class BidiSession:
    def __init__(self, port: int, timeout: float = 15.0):
        self.port = port
        self.ws = WebSocket("127.0.0.1", port, "/session", timeout=timeout)
        self._id = 0
        self._lock = threading.Lock()
        self.context = ""
        try:
            self.command("session.new", {"capabilities": {}}, timeout=timeout)
        except Exception:
            self.ws.close()  # never leak a half-open connection on retry
            raise

    def command(self, method: str, params: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
        with self._lock:
            self._id += 1
            mid = self._id
            self.ws.sock.settimeout(timeout)
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    msg = json.loads(self.ws.recv())
                except socket.timeout as exc:
                    raise BidiError(f"{method} timed out") from exc
                if msg.get("id") != mid:
                    continue  # events / stale replies
                if msg.get("type") == "error":
                    raise BidiError(f"{method}: {msg.get('error')}: {msg.get('message', '')}"[:400])
                return msg.get("result") or {}
            raise BidiError(f"{method} timed out")

    def top_context(self) -> str:
        tree = self.command("browsingContext.getTree", {"maxDepth": 0})
        contexts = tree.get("contexts") or []
        if not contexts:
            raise BidiError("browser has no open window")
        self.context = contexts[0]["context"]
        return self.context

    def navigate(self, url: str, timeout: float = 30.0) -> dict[str, Any]:
        ctx = self.context or self.top_context()
        return self.command("browsingContext.navigate", {"context": ctx, "url": url, "wait": "complete"},
                            timeout=timeout)

    def evaluate_json(self, expression: str, timeout: float = 10.0) -> Any:
        """Evaluate an expression that returns a JSON string; return the parsed value."""
        ctx = self.context or self.top_context()
        res = self.command("script.evaluate", {"expression": expression, "target": {"context": ctx},
                                               "awaitPromise": True, "resultOwnership": "none"},
                           timeout=timeout)
        if res.get("type") == "exception":
            details = res.get("exceptionDetails") or {}
            raise BidiError(f"page script failed: {details.get('text', 'exception')}"[:300])
        value = (res.get("result") or {}).get("value")
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except ValueError:
            return value

    def close(self) -> None:
        try:
            self.command("session.end", {}, timeout=2)
        except Exception:
            pass
        self.ws.close()


# Page-side helpers. Coordinates are returned in X screen pixels so the agent's
# real pointer (and the overlay) land exactly on the element.
LOCATE_JS = r"""
(() => {
  const sel = %(selector)s, wanted = %(text)s;
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  let el = null;
  if (sel) { el = [...document.querySelectorAll(sel)].find(visible) || null; }
  else if (wanted) {
    const q = wanted.trim().toLowerCase();
    const cands = [...document.querySelectorAll('a,button,input,textarea,select,summary,label,[role=button],[role=link],[role=menuitem],[role=tab],[role=checkbox],[role=option],[contenteditable=true]')];
    const label = el => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.title || '').trim().toLowerCase();
    el = cands.find(e => visible(e) && label(e) === q) || cands.find(e => visible(e) && label(e).includes(q)) || null;
  }
  if (!el) return JSON.stringify({found: false});
  el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
  const r = el.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const top = document.elementFromPoint(cx, cy);
  return JSON.stringify({found: true, tag: el.tagName.toLowerCase(),
    cx: Math.round((window.mozInnerScreenX - window.screenX + cx) * dpr),
    cy: Math.round((window.mozInnerScreenY - window.screenY + cy) * dpr),
    label: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80),
    x: Math.round((window.mozInnerScreenX + cx) * dpr), y: Math.round((window.mozInnerScreenY + cy) * dpr),
    obscured: !!top && top !== el && !el.contains(top)});
})()
"""

STATE_JS = r"""
(() => {
  const a = document.activeElement;
  const out = {url: location.href, title: document.title, readyState: document.readyState,
    focused: a && a !== document.body ? {tag: a.tagName.toLowerCase(), id: a.id || '',
      name: a.getAttribute('name') || '', label: (a.getAttribute('aria-label') || a.placeholder || '').slice(0, 80)} : null};
  if (%(include_text)s) out.text = (document.body ? document.body.innerText : '').slice(0, %(limit)d);
  return JSON.stringify(out);
})()
"""

READ_JS = r"""
(() => {
  const els = [...document.querySelectorAll(%(selector)s)].slice(0, 20);
  return JSON.stringify(els.map(e => ({tag: e.tagName.toLowerCase(),
    text: (e.innerText || e.value || '').slice(0, 2000), href: e.href || undefined})));
})()
"""


def locate_expression(selector: str = "", text: str = "") -> str:
    return LOCATE_JS % {"selector": json.dumps(selector or ""), "text": json.dumps(text or "")}


def state_expression(include_text: bool = False, limit: int = 4000) -> str:
    return STATE_JS % {"include_text": "true" if include_text else "false", "limit": max(0, min(20000, limit))}


def read_expression(selector: str) -> str:
    return READ_JS % {"selector": json.dumps(selector)}
