"""元数据类工具：项目 / Logstore / 索引 / Topic 列表查询。

所有工具命名前缀 ``sls_``，避免与其它 MCP 冲突。
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
        name="sls_list_projects",
        description="列出当前 AK 可见的全部 SLS Project（分页：size 默认 100，最大 500）。",
    )
    async def list_projects(size: int = 100, offset: int = 0) -> dict[str, Any]:
        try:
            data = await client.get_client().list_projects(size=min(size, 500), offset=offset)
            return {"ok": True, "data": data}
        except SLSError as e:
            return _err(e)

    @mcp.tool(
        name="sls_list_logstores",
        description="列出某个 Project 下所有 Logstore。project 留空则用 ALIYUN_LOG_DEFAULT_PROJECT。",
    )
    async def list_logstores(project: str = "", size: int = 100, offset: int = 0) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, _ = c.resolve(project=project)
            data = await c.list_logstores(p, size=min(size, 500), offset=offset)
            return {"ok": True, "project": p, "data": data}
        except SLSError as e:
            return _err(e)

    @mcp.tool(
        name="sls_get_logstore",
        description=(
            "查看 Logstore 元数据：shard 数 / TTL / 计费模式 / 是否启用 Web Tracking 等。"
            "排查 bug 前先用它确认日志是否落到目标 logstore、TTL 够不够回溯。"
        ),
    )
    async def get_logstore(project: str = "", logstore: str = "") -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            data = await c.get_logstore(p, ls)
            return {"ok": True, "project": p, "logstore": ls, "data": data}
        except SLSError as e:
            return _err(e)

    @mcp.tool(
        name="sls_get_index",
        description=(
            "查看索引配置：哪些字段建了索引、是否启用全文索引、token 分隔符等。\n"
            "**所有 SLS 查询的前置必读步骤**。看完后判断采集模式：\n"
            "  - `keys` 非空 → **字段索引模式**：可 `field:value` 过滤、可 GROUP BY 字段\n"
            "  - `keys` 为空 + `line: true` → **纯全文索引模式**（k8s 容器 stdout 常见）："
            "只能 `*关键字*` 全文检索；SQL 聚合只能 GROUP BY 内置字段（__source__/__topic__/_pod_name_ 等内置 tag）\n"
            "  - 两个都没有 → 该 logstore 没启用任何索引，查询会全表扫，慢且贵"
        ),
    )
    async def get_index(project: str = "", logstore: str = "") -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            data = await c.get_index_config(p, ls)
            return {"ok": True, "project": p, "logstore": ls, "data": data}
        except SLSError as e:
            return _err(e)

    @mcp.tool(
        name="sls_list_topics",
        description="列出 Logstore 下出现过的 topic，常用于按业务模块拆分日志后的导航。",
    )
    async def list_topics(project: str = "", logstore: str = "", token: str = "", line: int = 100) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            data = await c.list_topics(p, ls, token=token, line=line)
            return {"ok": True, "project": p, "logstore": ls, "data": data}
        except SLSError as e:
            return _err(e)
