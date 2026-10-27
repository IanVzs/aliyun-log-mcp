"""bug 排查导向的高阶组合工具：基于 sls_sql_query 二次封装，让 AI 更短路径定位问题。

这些工具都是**对 SQL 查询的命名封装**——避免 AI 每次都要现搓 SQL；
但也保留 ``query`` 可注入自定义过滤前缀，给灵活度。
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
        name="sls_error_summary",
        description=(
            "按 level 维度汇总错误数量，给出错误率总览。\n"
            "适用于：开局先看一眼 ERROR/WARN 在这段时间窗的量级，再决定下钻方向。\n\n"
            "**调用前先 sls_get_index 确认采集模式**：\n"
            "  - 结构化采集（有 level 字段索引）：默认参数即可；不同业务字段名可能是 "
            "'log_level' / 'severity' / 'log.level'，按需改 level_field\n"
            "  - **k8s 容器 stdout 采集**（只有 content 全文索引）：默认参数会得到 0 或全是噪声，"
            "请传 `level_field='content'` + `extra_filter='ERROR or WARN'`，"
            "得到的是『整行去重计数』，仅作粗略量级参考"
        ),
    )
    async def error_summary(
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        level_field: str = "level",
        extra_filter: str = "",
    ) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            filter_ = extra_filter or "*"
            sql = (
                f'{filter_} | SELECT "{level_field}" AS level, COUNT(*) AS c '
                f'GROUP BY "{level_field}" ORDER BY c DESC LIMIT 20'
            )
            data = await c.get_logs(p, ls, ft, tt, query=sql, line=100, reverse=False)
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "sql": sql,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)

    @mcp.tool(
        name="sls_top_errors",
        description=(
            "找出最频繁的错误消息 Top N。按 message_field（默认 'message'）聚合 + 计数。\n"
            "用于：知道了有 ERROR 后，定位哪类错误最值得先修。\n\n"
            "**容器 stdout 采集场景**：把 `message_field` 改为 'content'、`level_field` 改为 'content'、"
            "`level_value=''`（避免误过滤），并在 extra_filter 不为空时考虑 'ERROR' 等关键词。"
            "纯整行去重会得到许多『几乎一样但带不同时间戳/请求 ID 的行』，"
            "结果只作粗略归类参考；要精准建议先在 SLS 控制台对关键字段建索引。"
        ),
    )
    async def top_errors(
        n: int = 10,
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        message_field: str = "message",
        level_field: str = "level",
        level_value: str = "ERROR",
    ) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            filter_ = f'{level_field}:{level_value}' if level_value else "*"
            sql = (
                f'{filter_} | SELECT "{message_field}" AS msg, COUNT(*) AS c '
                f'GROUP BY "{message_field}" ORDER BY c DESC LIMIT {max(1, min(n, 100))}'
            )
            data = await c.get_logs(p, ls, ft, tt, query=sql, line=100, reverse=False)
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "sql": sql,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)

    @mcp.tool(
        name="sls_trace_request",
        description=(
            "按 trace_id / request_id 找全链路日志。\n"
            "id_field 默认 'trace_id'，不同框架可能是 'request_id' / 'x-request-id' / 'traceId'。\n"
            "结果按 __time__ 升序，便于追踪请求从入口到出口的全过程。"
        ),
    )
    async def trace_request(
        trace_id: str,
        id_field: str = "trace_id",
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        lines: int = 200,
    ) -> dict[str, Any]:
        if not trace_id:
            return {"ok": False, "error": "trace_id 必填"}
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            cap = min(lines, get_settings().guard.max_lines_per_query)
            query = f'{id_field}:"{trace_id}"'
            data = await c.get_logs(p, ls, ft, tt, query=query, line=cap, reverse=False)
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "query": query,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)

    @mcp.tool(
        name="sls_qps_timeline",
        description=(
            "按分钟 / 小时 桶统计请求量时序，看流量曲线。\n"
            "interval：'minute' | 'hour'。"
            "extra_filter 可注入业务过滤，如 'api:/v1/order and status:>=500'。"
        ),
    )
    async def qps_timeline(
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        interval: str = "minute",
        extra_filter: str = "",
    ) -> dict[str, Any]:
        if interval not in ("second", "minute", "hour", "day"):
            return {"ok": False, "error": "interval 必须是 second/minute/hour/day 之一"}
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            filter_ = extra_filter or "*"
            sql = (
                f"{filter_} | SELECT date_trunc('{interval}', __time__) AS t, "
                "COUNT(*) AS c GROUP BY t ORDER BY t LIMIT 1000"
            )
            data = await c.get_logs(p, ls, ft, tt, query=sql, line=1000, reverse=False)
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "interval": interval,
                "sql": sql,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)

    @mcp.tool(
        name="sls_latency_percentiles",
        description=(
            "对某个数值字段（默认 'latency_ms'）计算 P50/P90/P95/P99，用于性能瓶颈定位。\n"
            "extra_filter 可加业务过滤，如 'api:/v1/order'。\n"
            "返回单行；接口必须在 SLS 索引中把 latency 字段建为 long/double。"
        ),
    )
    async def latency_percentiles(
        from_time: str = "",
        to_time: str = "",
        project: str = "",
        logstore: str = "",
        latency_field: str = "latency_ms",
        extra_filter: str = "",
    ) -> dict[str, Any]:
        try:
            c = client.get_client()
            p, ls = c.resolve(project=project, logstore=logstore)
            if not ls:
                return {"ok": False, "error": "logstore 未指定且没有默认 ALIYUN_LOG_DEFAULT_LOGSTORE"}
            ft, tt = _window(from_time or None, to_time or None)
            filter_ = extra_filter or "*"
            f = latency_field
            sql = (
                f"{filter_} | SELECT "
                f"approx_percentile(\"{f}\", 0.50) AS p50, "
                f"approx_percentile(\"{f}\", 0.90) AS p90, "
                f"approx_percentile(\"{f}\", 0.95) AS p95, "
                f"approx_percentile(\"{f}\", 0.99) AS p99, "
                f"MAX(\"{f}\") AS max, AVG(\"{f}\") AS avg, COUNT(*) AS c"
            )
            data = await c.get_logs(p, ls, ft, tt, query=sql, line=10, reverse=False)
            return {
                "ok": True,
                "project": p,
                "logstore": ls,
                "from_local": fmt_local(ft),
                "to_local": fmt_local(tt),
                "sql": sql,
                "result": data,
            }
        except (SLSError, TimeParseError) as e:
            return _err(e)
