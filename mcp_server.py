from __future__ import annotations
from typing import Literal
from mcp.server import MCPServer
from tabby.ipc import send_command

mcp = MCPServer("tabby-companion")

@mcp.tool()
def tabby_wake() -> dict:
    """Summon Tabby in a fresh ChatGPT Voice chat."""
    return send_command({"command":"wake"})

@mcp.tool()
def tabby_close() -> dict:
    """Close Tabby and end any active Voice session."""
    return send_command({"command":"close"})

@mcp.tool()
def tabby_send_text(text: str) -> dict:
    """Send text into Tabby's current ChatGPT conversation."""
    return send_command({"command":"send-text","text":text})

@mcp.tool()
def companion_set_state(state: Literal["idle","wake","listening","thinking","tool","speaking","approval","success","error"]) -> dict:
    return send_command({"command":"state","value":state})

@mcp.tool()
def whiteboard_show() -> dict:
    return send_command({"command":"show"})

@mcp.tool()
def whiteboard_hide() -> dict:
    return send_command({"command":"hide"})

@mcp.tool()
def whiteboard_clear() -> dict:
    return send_command({"command":"clear"})

@mcp.tool()
def whiteboard_write(text: str, title: str = "") -> dict:
    return send_command({"command":"text","text":text,"title":title})

@mcp.tool()
def whiteboard_progress(value: float, label: str = "") -> dict:
    return send_command({"command":"progress","value":max(0.0,min(1.0,float(value))),"label":label})

@mcp.tool()
def whiteboard_choice(label: str, options: list[str]) -> dict:
    return send_command({"command":"choice","label":label,"options":options[:6]})

@mcp.tool()
def whiteboard_shape(kind: Literal["line","arrow","rect","circle"], x: float, y: float, w: float, h: float, label: str = "") -> dict:
    return send_command({"command":"shape","kind":kind,"x":x,"y":y,"w":w,"h":h,"label":label})

if __name__ == "__main__":
    mcp.run()
