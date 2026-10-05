"""Deprecated entry point kept for old client configs: Tabby has one MCP server now.

Runs the unified server (tabby_mcp.py) over stdio.
"""
from tabby_mcp import serve_stdio

if __name__ == "__main__":
    serve_stdio()
