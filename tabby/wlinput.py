"""Per-workspace input/capture broker for a nested Hyprland agent desktop.

Same newline-JSON protocol as ``tabby.xinput`` so the service treats both
backends alike. Input goes through the *nested* compositor's own
zwlr_virtual_pointer_v1 / virtual keyboard (wtype), i.e. its own seat: the
user's cursor and keyboard focus in the host Hyprland are never touched.
Coordinates are the nested output's logical pixels.

Environment: XDG_RUNTIME_DIR (the workspace's private runtime dir),
WAYLAND_DISPLAY (the nested socket) and HYPRLAND_INSTANCE_SIGNATURE (the
nested instance, for hyprctl).
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
from typing import Any

from tabby.xinput import Broker, parse_combo

BUTTONS = {1: 0x110, 2: 0x112, 3: 0x111}  # BTN_LEFT, BTN_MIDDLE, BTN_RIGHT
WTYPE_MODS = {"Control_L": "ctrl", "Shift_L": "shift", "Alt_L": "alt", "Super_L": "logo"}


class Wayland:
    """Just enough of the Wayland wire protocol for virtual input (stdlib only)."""

    def __init__(self, display: str):
        path = display if display.startswith("/") else os.path.join(os.environ["XDG_RUNTIME_DIR"], display)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.next_id = 2
        self.globals: dict[str, tuple[int, int]] = {}
        self.registry = self.new_id()
        self.send(1, 1, struct.pack("<I", self.registry))  # wl_display.get_registry
        self.roundtrip()

    def new_id(self) -> int:
        i = self.next_id
        self.next_id += 1
        return i

    def send(self, obj: int, opcode: int, payload: bytes = b"") -> None:
        self.s.sendall(struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload)

    @staticmethod
    def string(value: str) -> bytes:
        b = value.encode() + b"\0"
        return struct.pack("<I", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)

    def roundtrip(self, timeout: float = 3.0) -> None:
        cb = self.new_id()
        self.send(1, 0, struct.pack("<I", cb))  # wl_display.sync
        self.s.settimeout(timeout)
        buf = b""
        while True:
            chunk = self.s.recv(65536)
            if not chunk:
                raise RuntimeError("nested compositor closed the connection")
            buf += chunk
            while len(buf) >= 8:
                obj, size_op = struct.unpack("<II", buf[:8])
                size, opcode = size_op >> 16, size_op & 0xFFFF
                if len(buf) < size:
                    break
                body, buf = buf[8:size], buf[size:]
                if obj == self.registry and opcode == 0:  # wl_registry.global
                    name, ln = struct.unpack("<II", body[:8])
                    iface = body[8:8 + ln - 1].decode()
                    off = 8 + ((ln + 3) // 4) * 4
                    self.globals[iface] = (name, struct.unpack("<I", body[off:off + 4])[0])
                elif obj == 1 and opcode == 0:  # wl_display.error
                    raise RuntimeError("nested compositor reported a protocol error")
                elif obj == cb:
                    return

    def bind(self, iface: str, version: int) -> int:
        name, have = self.globals[iface]
        new = self.new_id()
        self.send(self.registry, 0, struct.pack("<I", name) + self.string(iface)
                  + struct.pack("<II", min(version, have), new))
        return new


class NestedSession:
    def __init__(self) -> None:
        self.wl = Wayland(os.environ["WAYLAND_DISPLAY"])
        manager = self.wl.bind("zwlr_virtual_pointer_manager_v1", 2)
        self.vp = self.wl.new_id()
        self.wl.send(manager, 0, struct.pack("<II", 0, self.vp))  # create_virtual_pointer(seat=null)
        self.wl.roundtrip()
        self.refresh_size()
        self.x, self.y = self.width // 2, self.height // 2

    # -- nested compositor queries
    def hyprctl(self, *args: str) -> Any:
        out = subprocess.run(["hyprctl", "-j", *args], capture_output=True, text=True, timeout=3).stdout
        return json.loads(out or "null")

    def refresh_size(self) -> None:
        mon = (self.hyprctl("monitors") or [{}])[0]
        self.scale = float(mon.get("scale") or 1.0)
        w, h = float(mon.get("width") or 1280), float(mon.get("height") or 800)
        if int(mon.get("transform") or 0) % 2:
            w, h = h, w
        self.width, self.height = round(w / self.scale), round(h / self.scale)
        self.output = str(mon.get("name") or "")

    @staticmethod
    def now() -> int:
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def frame(self) -> None:
        self.wl.send(self.vp, 4)

    # -- pointer
    def move(self, x: float, y: float) -> tuple[int, int]:
        self.x = max(0, min(self.width - 1, int(x)))
        self.y = max(0, min(self.height - 1, int(y)))
        self.wl.send(self.vp, 1, struct.pack("<IIIII", self.now(), self.x, self.y, self.width, self.height))
        self.frame()
        return self.x, self.y

    def button(self, button: int, down: bool) -> None:
        self.wl.send(self.vp, 2, struct.pack("<III", self.now(), BUTTONS.get(button, 0x110), 1 if down else 0))
        self.frame()

    def click(self, button: int = 1, count: int = 1) -> None:
        # A real hand never presses and releases with zero motion. Canvas apps
        # (JS Paint, drawing tools) ignore such a click or, worse, keep their
        # "pressed" state and draw along later moves. Hold briefly with a
        # 1 px wiggle, as a physical mouse does.
        x, y = self.x, self.y
        nx = x + 1 if x + 1 < self.width else x - 1
        for i in range(max(1, min(3, count))):
            self.button(button, True)
            time.sleep(0.025)
            self.move(nx, y)
            time.sleep(0.015)
            self.move(x, y)
            time.sleep(0.015)
            self.button(button, False)
            if i + 1 < count:
                time.sleep(0.06)
        self.wl.roundtrip()

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        for axis, amount in ((0, dy), (1, dx)):
            for _ in range(min(50, abs(int(amount)))):
                sign = 1 if amount > 0 else -1
                self.wl.send(self.vp, 5, struct.pack("<I", 0))  # axis_source(wheel)
                self.wl.send(self.vp, 7, struct.pack("<IIii", self.now(), axis, sign * 15 * 256, sign))
                self.frame()
                time.sleep(0.012)
        self.wl.roundtrip()

    # -- keyboard (wtype: the nested compositor's virtual-keyboard protocol)
    def wtype(self, *args: str) -> None:
        subprocess.run(["wtype", *args], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def combo(self, combo: str) -> None:
        mods, key = parse_combo(combo)
        args: list[str] = ["-s", "60"]  # let the client see the uploaded keymap first
        for m in mods:
            args += ["-M", WTYPE_MODS[m]]
        args += ["-k", key]
        for m in reversed(mods):
            args += ["-m", WTYPE_MODS[m]]
        self.wtype(*args)

    def type_text(self, text: str, delay_ms: int = 8) -> int:
        # wtype uploads a keymap and starts typing at once; without the
        # leading pause the first character is dropped by some clients.
        # wtype types "\n" as a literal character, not a key press: send Return.
        args: list[str] = ["-s", "120", "-d", str(max(0, delay_ms))]
        for i, part in enumerate(text.replace("\r\n", "\n").split("\n")):
            if i:
                args += ["-k", "Return"]
            if part.startswith("-"):  # an argument starting with "-" would read as an option
                args += ["-k", "minus"]
                part = part[1:]
            if part:
                args.append(part)
        self.wtype(*args)
        return len(text)

    # -- windows and capture
    def windows(self) -> list[dict[str, Any]]:
        active = (self.hyprctl("activewindow") or {}).get("address")
        return [{"id": c.get("address"), "title": str(c.get("title", ""))[:200], "class": c.get("class", ""),
                 "x": c["at"][0], "y": c["at"][1], "width": c["size"][0], "height": c["size"][1],
                 "focused": c.get("address") == active}
                for c in (self.hyprctl("clients") or []) if c.get("mapped", True)]

    def focus_window(self, wid: str) -> None:
        subprocess.run(["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "address:{wid}" }})'],
                       capture_output=True, timeout=3)

    def capture(self, path: str, preview_path: str = "", preview_width: int = 360, max_width: int = 0,
                jpeg_path: str = "", jpeg_width: int = 0) -> dict[str, Any]:
        from PIL import Image
        self.refresh_size()
        out: dict[str, Any] = {"width": self.width, "height": self.height}
        if jpeg_path and not path and not preview_path:
            # Fast path for live frames: let grim encode and scale.
            factor = min(1.0, (jpeg_width or self.width) / (self.width * self.scale))
            tmp = jpeg_path + ".tmp.jpg"
            subprocess.run(["grim", "-t", "jpeg", "-q", "80", "-s", f"{factor:.4f}", tmp], check=True, timeout=5)
            os.replace(tmp, jpeg_path)
            out["jpeg"] = jpeg_path
            return out
        raw = subprocess.run(["grim", "-t", "ppm", "-"], capture_output=True, check=True, timeout=5).stdout
        import io
        frame = Image.open(io.BytesIO(raw)).convert("RGB")
        if frame.size != (self.width, self.height):  # physical -> logical, so pixels == click coordinates
            frame = frame.resize((self.width, self.height))
        if path:
            full = frame
            if max_width and frame.width > max_width:
                full = frame.resize((max_width, round(frame.height * max_width / frame.width)))
            full.save(path, "PNG")
            out.update(path=path, imageWidth=full.width, imageHeight=full.height)
        if jpeg_path:
            live = frame if not jpeg_width or frame.width <= jpeg_width else frame.resize(
                (jpeg_width, round(frame.height * jpeg_width / frame.width)))
            live.save(jpeg_path + ".tmp.jpg", "JPEG", quality=80)
            os.replace(jpeg_path + ".tmp.jpg", jpeg_path)
            out["jpeg"] = jpeg_path
        if preview_path:
            small = frame.resize((preview_width, max(1, round(frame.height * preview_width / frame.width))))
            small.save(preview_path + ".tmp.png", "PNG")
            os.replace(preview_path + ".tmp.png", preview_path)
            out["preview"] = preview_path
        return out


class NestedBroker(Broker):
    """Reuses Broker's BiDi session handling; replaces the X session."""

    def __init__(self, session: NestedSession):
        self.x = session  # type: ignore[assignment]
        self.browser = None


def _dispatch(broker: NestedBroker, req: dict[str, Any]) -> dict[str, Any]:
    s: NestedSession = broker.x  # type: ignore[assignment]
    op = req.get("op")
    if op == "hello":
        s.refresh_size()
        return {"width": s.width, "height": s.height, "pointer": [s.x, s.y], "backend": "nested"}
    if op == "pointer":
        return {"pointer": [s.x, s.y]}
    if op == "move":
        return {"pointer": list(s.move(req["x"], req["y"]))}
    if op == "click":
        s.click(int(req.get("button", 1)), int(req.get("count", 1)))
        return {"pointer": [s.x, s.y]}
    if op == "button":
        s.button(int(req.get("button", 1)), bool(req.get("down")))
        return {}
    if op == "scroll":
        s.scroll(int(req.get("dx", 0)), int(req.get("dy", 0)))
        return {}
    if op == "combo":
        s.combo(str(req["keys"]))
        return {}
    if op == "type":
        return {"typed": s.type_text(str(req.get("text", "")), int(req.get("delay_ms", 8)))}
    if op == "windows":
        return {"windows": s.windows()}
    if op == "fit":
        return {"fitted": 0}  # the nested compositor tiles its own windows
    if op == "focus":
        s.focus_window(str(req["window"]))
        return {}
    if op == "capture":
        return s.capture(str(req.get("path") or ""), str(req.get("preview") or ""),
                         int(req.get("preview_width", 360)), int(req.get("max_width", 0)),
                         str(req.get("jpeg") or ""), int(req.get("jpeg_width", 0)))
    if op == "bidi-connect":
        return broker.connect_bidi(int(req["port"]), float(req.get("op_timeout", 25)))
    if op == "bidi-navigate":
        res = broker.bidi().navigate(str(req["url"]), timeout=float(req.get("op_timeout", 30)))
        return {"url": res.get("url", req["url"])}
    if op == "bidi-eval":
        return {"value": broker.bidi().evaluate_json(str(req["expression"]), timeout=float(req.get("op_timeout", 10)))}
    raise ValueError(f"unknown op {op!r}")


def serve(sock_path: str) -> int:
    try:
        broker = NestedBroker(NestedSession())
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
        sys.stdout.flush()
        return 2
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        srv.bind(sock_path)
    except OSError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": f"cannot listen on {sock_path}: {exc}"}) + "\n")
        sys.stdout.flush()
        return 2
    finally:
        os.umask(old)
    srv.listen(2)
    s = broker.x
    sys.stdout.write(json.dumps({"ok": True, "ready": True, "width": s.width, "height": s.height}) + "\n")
    sys.stdout.flush()
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    while True:
        conn, _ = srv.accept()
        with conn, conn.makefile("rw", encoding="utf-8", newline="\n") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    result = {"ok": True, **_dispatch(broker, json.loads(line))}
                except Exception as exc:
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                try:
                    fh.write(json.dumps(result) + "\n")
                    fh.flush()
                except OSError:
                    break


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "serve":
        raise SystemExit(serve(sys.argv[2]))
    print("usage: python3 -m tabby.wlinput serve <socket>", file=sys.stderr)
    raise SystemExit(2)
