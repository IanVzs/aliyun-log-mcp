"""告警相关只读工具：list / get。

不暴露 create / delete / update —— 写操作放到独立 MCP 或控制台手工执行更稳。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import client
from ..client import SLSError


def _err(e: SLSError) -> dict[str, Any]:
    return e.to_dict()


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="sls_list_alerts",
        description="列出某 Project 下的告警规则（只读）。project 留空则用默认 project。",
    )
    async def list_alerts(project: str = "", size: int = 100, offset: int = 0) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, _ = c.resolve(project=project)
            data = await c.list_alerts(p, size=min(size, 500), offset=offset)
            return {"ok": True, "project": p, "data": data}
        except SLSError as e:
            return _err(e)

    @mcp.tool(
        name="sls_get_alert",
        description="查看单条告警规则的详细配置（触发条件 / 评估周期 / 通知渠道等）。",
    )
    async def get_alert(alert_name: str, project: str = "") -> dict[str, Any]:
        if not alert_name:
            return {"ok": False, "error": "alert_name 必填"}
        try:
            c = client.get_client()
            p, _ = c.resolve(project=project)
            data = await c.get_alert(p, alert_name)
            return {"ok": True, "project": p, "alert_name": alert_name, "data": data}
        except SLSError as e:
            return _err(e)
