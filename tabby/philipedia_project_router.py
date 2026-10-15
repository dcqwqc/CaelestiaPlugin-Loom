"""Fail-closed Philipedia-only ChatGPT routing; no Mirai desktop dependency."""
from __future__ import annotations

import json
import stat
import urllib.error
import urllib.request
from pathlib import Path

_BASE = "http://127.0.0.1:4319"
_TOKEN = Path.home() / ".config/loom/philipedia-router/token"


def _call(endpoint: str, payload: dict | None = None) -> dict:
    try:
        info = _TOKEN.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise RuntimeError("project router token permissions are unsafe")
        token = _TOKEN.read_text().strip()
        if len(token) < 32:
            raise RuntimeError("project router token is not configured")
        request = urllib.request.Request(
            _BASE + endpoint,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.loads(response.read(65537))
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read(65537))
            reason = body.get("error") or body.get("message") or "request rejected"
        except (ValueError, AttributeError):
            reason = "request rejected"
        raise RuntimeError(f"project router: {reason}") from error
    except (OSError, ValueError) as error:
        raise RuntimeError("Philipedia project router unavailable") from error
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("project router returned an invalid response")
    return result


def invoke(name: str, args: dict) -> dict:
    try:
        if not isinstance(args, dict):
            raise RuntimeError("invalid routing arguments")
        if name == "loom_router_status":
            result = _call("/v1/status")
        else:
            status = _call("/v1/status")
            if not status.get("loggedIn"):
                raise RuntimeError("Philipedia router is online but its dedicated ChatGPT browser is not signed in")
            if name == "loom_origin_resolve":
                result = _call("/v1/origin/resolve", {"origin_messages": args.get("origin_messages")})
            elif name == "loom_chat_route":
                if args.get("origin_ref") and not args.get("url"):
                    raise RuntimeError("origin_ref alone is insufficient for Philipedia routing; supply the canonical ChatGPT URL")
                keys = ("url", "origin_messages", "lifecycle", "reviewer", "evidence")
                result = _call("/v1/route", {key: args[key] for key in keys if key in args})
            else:
                raise RuntimeError("unknown project routing tool")
        is_error = False
    except RuntimeError as error:
        result, is_error = {"ok": False, "error": str(error)}, True
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "structuredContent": result, "isError": is_error}
