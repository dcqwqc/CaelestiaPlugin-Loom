from __future__ import annotations
import json, os, socket, threading
from pathlib import Path
from typing import Any, Callable

MAX_PAYLOAD = 128 * 1024
MAX_REPLY = 128 * 1024

def socket_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "tabby.sock"

class IPCServer:
    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]]):
        self.handler = handler
        self.path = socket_path()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try: self.path.unlink()
        except FileNotFoundError: pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.path)); os.chmod(self.path, 0o600); s.listen(12); s.settimeout(.25)
        self._server = s; self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="tabby-ipc", daemon=True); self._thread.start()

    def _serve(self):
        assert self._server is not None
        while not self._stop.is_set():
            try: conn, _ = self._server.accept()
            except socket.timeout: continue
            except OSError: break
            with conn:
                try:
                    conn.settimeout(2.0)
                    raw = conn.recv(MAX_PAYLOAD + 1)
                    if not raw or len(raw) > MAX_PAYLOAD: raise ValueError("invalid payload")
                    req = json.loads(raw.decode())
                    if not isinstance(req, dict): raise ValueError("invalid request")
                    result = self.handler(req)
                    conn.sendall(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode()[:MAX_REPLY])
                except Exception as e:
                    try: conn.sendall(json.dumps({"ok":False,"error":str(e)}).encode())
                    except OSError: pass

    def stop(self):
        self._stop.set()
        if self._server:
            try: self._server.close()
            except OSError: pass
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=1)
        try: self.path.unlink()
        except FileNotFoundError: pass

def send_command(command: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); client.settimeout(timeout)
    try:
        client.connect(str(socket_path()))
        client.sendall(json.dumps(command, separators=(",", ":"), ensure_ascii=False).encode())
        raw = client.recv(MAX_REPLY)
        return json.loads(raw.decode()) if raw else {"ok":False,"error":"empty reply"}
    except Exception as e:
        return {"ok":False,"error":str(e)}
    finally:
        client.close()
