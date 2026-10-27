"""MCP 工具集。register_all(mcp) 一次性注册到 FastMCP server。"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import alert, diagnose, meta, query


def register_all(mcp: FastMCP) -> None:
    meta.register(mcp)
    query.register(mcp)
    diagnose.register(mcp)
    alert.register(mcp)
