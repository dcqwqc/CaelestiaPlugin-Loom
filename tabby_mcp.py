#!/usr/bin/env python3
"""Backward-compatible Tabby MCP entrypoint. New clients should run loom_mcp.py."""
from loom_mcp import main, serve_stdio, handle_rpc, tool_list, call_tool

if __name__ == "__main__":
    main()
