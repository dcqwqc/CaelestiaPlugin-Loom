"""Authenticated internal HTTP service for the authoritative origin registry."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tabby.origin_registry import OriginRegistry, _private_token

MAX_JSON_BODY = 16 * 1024
SOCKET_TIMEOUT = 5.0


class OriginRegistryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, registry, token):
        self.registry = registry
        self.token = token
        super().__init__(address, OriginRegistryHandler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(SOCKET_TIMEOUT)
        return sock, address


class OriginRegistryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        # Paths and validation messages can contain origin identifiers.
        return

    def _reply(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status, code, message):
        self._reply(status, {"ok": False, "error": {"code": code, "message": message}})

    def _authorized(self):
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix):] if header.startswith(prefix) else ""
        return hmac.compare_digest(supplied.encode("utf-8", "surrogatepass"),
                                   self.server.token.encode("utf-8"))

    def do_POST(self):
        if not self._authorized():
            self.close_connection = True
            self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", "authentication required")
            return
        if self.path not in {"/v1/register", "/v1/resolve"}:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "endpoint not found")
            return
        if self.headers.get_content_type() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "content_type", "application/json is required")
            return
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._error(HTTPStatus.LENGTH_REQUIRED, "content_length", "a valid Content-Length is required")
            return
        if length > MAX_JSON_BODY:
            self.close_connection = True
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large", "request body exceeds 16 KiB")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "request body must be valid JSON")
            return
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "request body must be an object")
            return
        try:
            if self.path == "/v1/register":
                self._validate_fields(payload, {"origin_ref", "origin_url", "operation_key", "source"},
                                      {"origin_ref", "operation_key"})
                origin = self.server.registry.register(
                    payload["origin_ref"], origin_url=payload.get("origin_url", ""),
                    operation_key=payload["operation_key"], source=payload.get("source", "explicit"))
            else:
                self._validate_fields(payload, {"origin_ref"}, {"origin_ref"})
                origin = self.server.registry.resolve(payload["origin_ref"])
        except ValueError as exc:
            self._error(HTTPStatus.CONFLICT, "invalid_or_conflicting_request", str(exc))
            return
        self._reply(HTTPStatus.OK, {"ok": True, "origin": origin})

    @staticmethod
    def _validate_fields(payload, allowed, required):
        if set(payload) - allowed or not required.issubset(payload):
            raise ValueError("request fields are invalid")
        if any(not isinstance(value, str) for value in payload.values()):
            raise ValueError("request fields must be strings")

    def do_GET(self):
        if not self._authorized():
            self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", "authentication required")
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "POST is required")

    do_DELETE = do_GET
    do_HEAD = do_GET
    do_PATCH = do_GET
    do_PUT = do_GET


def create_server(host, port, *, db_path, token_file):
    if host in {"", "0.0.0.0", "::"}:
        raise ValueError("--host must be a specific loopback or Tailscale address")
    try:
        socket.inet_pton(socket.AF_INET, host)
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET6, host)
        except OSError as exc:
            raise ValueError("--host must be an explicit IP address") from exc
    return OriginRegistryHTTPServer((host, port), OriginRegistry(db_path), _private_token(token_file))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the internal Loom origin registry")
    parser.add_argument("--host", required=True, help="specific loopback or Tailscale IP")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args(argv)
    db_path = os.environ.get("LOOM_ORIGIN_REGISTRY_DB", "").strip()
    if not db_path:
        parser.error("LOOM_ORIGIN_REGISTRY_DB must name the Philipedia SQLite database")
    server = create_server(args.host, args.port, db_path=db_path, token_file=args.token_file)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
