"""核心查询工具：search_logs / histogram / sql_query / get_context。

时间参数统一接受：
- unix 秒整数
- ISO8601 字符串：``2026-05-22T18:00:00+08:00`` / ``2026-05-22 18:00``
- 相对偏移：``-15m`` / ``-1h`` / ``-2d`` / ``now``
- 留空 / 不传：自动套用 .env 中 ``DEFAULT_TIME_WINDOW_MINUTES``
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import client
from ..client import SLSError
from ..config import get_settings
from ..timefmt import TimeParseError, fmt_local, resolve_window


def _err(e: Exception) -> dict[str, Any]:
    if isinstance(e, SLSError):
        return e.to_dict()
    return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}


def _clamp_lines(lines: int) -> int:
    cap = get_settings().guard.max_lines_per_query
    if lines <= 0:
        return cap
    return min(lines, cap)


def _window(from_time: str | int | None, to_time: str | int | None) -> tuple[int, int]:
    g = get_settings().guard
    return resolve_window(
        from_time,
        to_time,
        default_window_minutes=g.default_time_window_minutes,
        max_range_hours=g.max_time_range_hours,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="sls_search_logs",
        description=(
            "SLS 原始日志查询（推荐入口）。query 同时支持 Lucene 与 SQL：\n"
            "  - 纯过滤：`level: ERROR and host: web*`\n"
            "  - 含分析：`status:>=500 | SELECT status, COUNT(*) AS c GROUP BY status ORDER BY c DESC LIMIT 10`\n"
            "  - 空 query：拉全量（建议配合更小时间窗）\n\n"
            "时间窗：from_time / to_time 支持 ISO8601、unix 秒、`-15m`/`-1h`/`now`；"
            "都留空则取最近 DEFAULT_TIME_WINDOW_MINUTES 分钟。\n"
            "lines 受 MAX_LINES_PER_QUERY 限制；reverse=True 按时间倒序便于看最新。"
        ),
    )
    async def search_logs(
        query: str = "",
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        topic: str = "",
        lines: int = 100,
        offset: int = 0,
        reverse: bool = True,
        power_sql: bool = False,
    ) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            data = await c.get_logs(
                p, ls, ft, tt,
                query=query, topic=topic, line=_clamp_lines(lines),
                offset=offset, reverse=reverse, power_sql=power_sql,
            )
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from": ft,
                "to": tt,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "query": query,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)

    @mcp.tool(
        name="sls_histogram",
        description=(
            "时间分桶直方图（不返回原始日志、只返回 N 个桶的命中数）。"
            "排查抖动 / 突增 / 漏数据时**先调它**找到异常时段，再用 sls_search_logs 抓那段窗口的明细。"
        ),
    )
    async def histogram(
        query: str = "",
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        topic: str = "",
    ) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            data = await c.get_histograms(p, ls, ft, tt, query=query, topic=topic)
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from": ft,
                "to": tt,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "query": query,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)

    @mcp.tool(
        name="sls_sql_query",
        description=(
            "SLS SQL 分析语法直通入口。query 必须含 `|`，例如：\n"
            "  `* | SELECT date_trunc('minute', __time__) AS m, COUNT(*) AS c GROUP BY m ORDER BY m`\n"
            "  `level:ERROR | SELECT class, COUNT(*) AS c GROUP BY class ORDER BY c DESC LIMIT 20`\n"
            "power_sql=True 用 SQL 独享版（增量按量计费，慎用）。"
        ),
    )
    async def sql_query(
        query: str,
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        lines: int = 100,
        offset: int = 0,
        power_sql: bool = False,
    ) -> dict[str, Any]:
        if "|" not in query:
            return {"ok": False, "error": "sql_query 的 query 必须包含 `|`，例：`* | SELECT count(*)`"}
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            data = await c.get_logs(
                p, ls, ft, tt,
                query=query, line=_clamp_lines(lines), offset=offset, reverse=False,
                power_sql=power_sql,
            )
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from": ft,
                "to": tt,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "query": query,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)

    @mcp.tool(
        name="sls_get_context",
        description=(
            "取某条日志的上下文（前 N 条 / 后 N 条），排查 bug 必备。\n"
            "用法：先 sls_search_logs 拿到目标日志，从其 contents 中取 pack_id / pack_meta：\n"
            "  - 原生 Logtail 采集：字段名 `__pack_id__` / `__pack_meta__` 都存在\n"
            "  - **k8s 容器 stdout 采集**：通常只有 `__tag__:__pack_id__`，**没有 pack_meta**——\n"
            "    这种场景本工具不可用，改用 `sls_search_logs` 时间窗 ±30s 的方式："
            "    `query='_pod_name_:\"<pod>\" and _container_name_:\"<container>\"'`，"
            "    `from_time` 取目标日志时间 -30s，`to_time` 取 +30s，按时间升序看上下文。\n"
            "back_lines / forward_lines 各最大 100。"
        ),
    )
    async def get_context(
        pack_id: str,
        pack_meta: str,
        back_lines: int = 10,
        forward_lines: int = 10,
        project: str = "",
        logstore: str = "",
    ) -> dict[str, Any]:
        if not pack_id or not pack_meta:
            return {"ok": False, "error": "pack_id 与 pack_meta 都必填，来自目标日志的 contents"}
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            data = await c.get_context_logs(
                p, ls, pack_id, pack_meta,
                back_lines=min(back_lines, 100),
                forward_lines=min(forward_lines, 100),
            )
            return {"ok": True, "project": p, "logstore": ls, "result": data}
        except SLSError as e:
            return _err(e)
