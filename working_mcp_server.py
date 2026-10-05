from __future__ import annotations
from mcp.server import MCPServer
from tabby.ipc import send_command

mcp = MCPServer("tabby-working")

@mcp.tool()
def working_list() -> dict:
    """List persistent Tabby Working tasks and their current status."""
    return send_command({"command":"work-list"})

@mcp.tool()
def working_pin_current(title: str = "") -> dict:
    """Pin the current Tabby ChatGPT conversation into Working. ChatGPT's own chat title is preferred."""
    return send_command({"command":"work-pin-current","title":title})

@mcp.tool()
def working_update(task_id: str, title: str = "", progress: float = -1, summary: str = "") -> dict:
    """Update a Working task. This tool intentionally cannot delete tasks."""
    payload={"command":"work-update","task_id":task_id}
    if title: payload["title"]=title
    if 0 <= progress <= 1: payload["progress"]=float(progress)
    if summary: payload["summary"]=summary
    return send_command(payload)

@mcp.tool()
def working_complete(task_id: str, summary: str = "") -> dict:
    """Mark a Working task complete. Completed tasks remain pinned until the user deletes them."""
    return send_command({"command":"work-complete","task_id":task_id,"summary":summary})

@mcp.tool()
def working_open(task_id: str) -> dict:
    """Open a Working conversation in Tabby for a follow-up."""
    return send_command({"command":"work-open","task_id":task_id})

@mcp.tool()
def working_resume_voice(task_id: str) -> dict:
    """Resume ChatGPT Voice in an existing Working conversation."""
    return send_command({"command":"work-voice","task_id":task_id})

if __name__ == "__main__":
    mcp.run()
