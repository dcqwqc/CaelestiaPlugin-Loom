"""Compatibility MCP entrypoint; see loom_mcp.py."""
from loom_mcp import serve_stdio
if __name__ == "__main__":
    serve_stdio()
