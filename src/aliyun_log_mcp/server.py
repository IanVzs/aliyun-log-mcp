"""aliyun-log-mcp 入口：FastMCP + 工具 + Prompt 注册 + 三种传输模式。"""

from __future__ import annotations

import argparse
import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import client
from .config import get_settings, init_settings
from .tools import register_all

logger = logging.getLogger("aliyun_log_mcp")


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def _register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="sls-bug-investigation",
        description="标准 bug 排查链路：探测字段结构 → 错误总量 → 时序定位 → top 错误 → 抽样 → 上下文。",
    )
    def bug_investigation(
        logstore: str,
        symptom: str,
        time_window: str = "-1h",
        project: str = "",
    ) -> str:
        return (
            f"## SLS Bug 排查\n\n"
            f"现象：{symptom}\n"
            f"时间窗：{time_window} ~ now\n"
            f"project=`{project or '<.env default>'}`，logstore=`{logstore}` (必填)\n\n"
            "请严格按下面顺序调本 MCP 工具，**每步先汇报结果再决定下一步**：\n\n"
            f"### Step 0  探测日志结构（一次性，决定后续走哪条路径）\n"
            f"  `sls_get_index(logstore='{logstore}')` 看是否有字段索引；\n"
            f"  `sls_search_logs(logstore='{logstore}', query='*', from_time='-5m', lines=2)` "
            "看 1~2 条样本的字段名。**两种模式分支**：\n"
            "    - **结构化采集**（看到 `level` / `message` / `trace_id` 等字段）→ 走 Step 1A\n"
            "    - **容器 stdout 采集**（字段是 `content` / `_pod_name_` / `_container_name_`，"
            "level 信息在 content 文本里）→ 走 Step 1B\n\n"
            f"### Step 1A (结构化采集)\n"
            f"  1. `sls_error_summary(logstore='{logstore}', level_field='level', from_time='{time_window}')`\n"
            f"  2. `sls_histogram(logstore='{logstore}', query='level:ERROR', from_time='{time_window}')`\n"
            f"  3. `sls_top_errors(logstore='{logstore}', message_field='message', level_field='level', "
            f"level_value='ERROR', n=10, from_time='{time_window}')`\n"
            f"  4. `sls_search_logs(logstore='{logstore}', query='message:\"<关键串>\"', lines=5)` 拿原始日志；"
            "结果中找 `__pack_id__` 与 `__pack_meta__`\n"
            f"  5. `sls_get_context(logstore='{logstore}', pack_id=..., pack_meta=..., back_lines=10, forward_lines=10)`\n"
            f"  6. 若上下文有 trace_id / request_id："
            f"`sls_trace_request(logstore='{logstore}', trace_id='...', id_field='trace_id')`\n\n"
            f"### Step 1B (容器 stdout 采集，单字段 `content`)\n"
            f"  1. `sls_error_summary(logstore='{logstore}', level_field='content', extra_filter='ERROR or WARN', "
            f"from_time='{time_window}')` "
            "—— 注：实际是按整行去重，可能出现大量唯一行；接受作为粗略量级即可\n"
            f"  2. `sls_histogram(logstore='{logstore}', query='ERROR', from_time='{time_window}')` "
            "—— 全文检索 ERROR 关键字\n"
            f"  3. `sls_search_logs(logstore='{logstore}', query='ERROR', lines=20, from_time='{time_window}')` "
            "—— 直接抓含 ERROR 的原始日志；按 `_pod_name_` / `_container_name_` 分组归类\n"
            f"  4. `sls_get_context` 在容器采集下**不可用**（缺 `__pack_meta__`）；"
            "退路：选一条目标日志，记下其时间 t 与 `_pod_name_`，"
            f"调 `sls_search_logs(logstore='{logstore}', "
            f"query='_pod_name_:\"<pod>\"', "
            "from_time='<t-30s>', to_time='<t+30s>', reverse=False)` 看前后日志\n"
            f"  5. 若日志正文里有 trace_id，先 `sls_get_index` 看是否建了字段索引；"
            "没建就只能 `sls_search_logs(query='<trace_id>')` 全文搜\n\n"
            "### 结论必须包含\n"
            "  - 根因猜测（含代码/配置/外部依赖的具体定位）\n"
            "  - 复现条件\n"
            "  - 建议修复点（精确到 文件:行 / 函数 / 字段）\n\n"
            "### 约束\n"
            "  - 时间跨度从 -1h 起步，找不到再扩到 -6h；别一上来 -24h\n"
            "  - 单次 lines ≤ 100；要全量分析走 `sls_sql_query` 做服务端聚合\n"
            "  - **日志正文中常含 token / 手机号 / IDFA / IMEI / IP**——提交结论 / commit / PR 前必须脱敏\n"
        )

    @mcp.prompt(
        name="sls-performance-analysis",
        description="性能分析：QPS 时序 + 延迟分位数 + 慢请求样本（要求 latency 字段已建索引）。",
    )
    def performance_analysis(
        logstore: str,
        api: str = "",
        time_window: str = "-1h",
        latency_field: str = "latency_ms",
        project: str = "",
    ) -> str:
        filt = f"api:{api}" if api else "*"
        return (
            f"## SLS 性能分析\n\n"
            f"接口：`{api or '<全部>'}`，时间窗：{time_window} ~ now\n"
            f"project=`{project or '<.env default>'}`，logstore=`{logstore}` (必填)\n"
            f"延迟字段：`{latency_field}`\n\n"
            f"### Step 0  确认延迟字段有索引（否则分位数算不出来）\n"
            f"  `sls_get_index(logstore='{logstore}')` —— 找 `{latency_field}`，"
            f"  type 必须是 `long` 或 `double`；不是就改 `latency_field` 重试或回控制台建索引\n\n"
            f"### Step 1  流量曲线\n"
            f"  `sls_qps_timeline(logstore='{logstore}', interval='minute', "
            f"extra_filter='{filt}', from_time='{time_window}')` "
            "—— 看是否有跌零 / 突增 / 周期抖动\n\n"
            f"### Step 2  延迟分布\n"
            f"  `sls_latency_percentiles(logstore='{logstore}', latency_field='{latency_field}', "
            f"extra_filter='{filt}', from_time='{time_window}')` "
            "—— P99 远高于 P95 多半是长尾抖动；P50 也偏高就是全局退化\n\n"
            f"### Step 3  慢请求样本\n"
            f"  `sls_sql_query(logstore='{logstore}', "
            f"query='{filt} and \"{latency_field}\":>1000 "
            f"| SELECT * ORDER BY \"{latency_field}\" DESC LIMIT 10', from_time='{time_window}')` "
            "—— 看是否集中在某个用户 / 某个下游 / 某个时段\n\n"
            "### Step 4  挑 1~2 条最慢的，提取 trace_id\n"
            f"  `sls_trace_request(logstore='{logstore}', trace_id='...', id_field='trace_id')` "
            "—— 看耗时分布在链路哪段\n\n"
            "### 结论\n"
            "  瓶颈在 上游 / 下游 / DB / 缓存 / GC / 网络 / 限流，给出明确优化方向\n"
        )

    @mcp.prompt(
        name="sls-quickstart",
        description="第一次接入：列 logstore → 看 logstore 元数据 → 看索引 → 拉 2 条样本看字段。",
    )
    def quickstart(project: str = "") -> str:
        return (
            "## SLS 接入 4 步走\n\n"
            "前提：`.env` 已配 `ALIYUN_LOG_DEFAULT_PROJECT`，"
            "**所以下面工具都不用传 project；logstore 必须每次显式传**。\n\n"
            f"1. `sls_list_logstores({'project=' + project if project else ''})` "
            "—— 看默认 project 下都有哪些 logstore\n"
            "2. 选定一个 logstore（记为 `<ls>`）：\n"
            "   `sls_get_logstore(logstore='<ls>')` —— TTL 决定能往回查多久；shard 数与 autoSplit 影响查询并行度\n"
            "3. `sls_get_index(logstore='<ls>')` —— 关键判断：\n"
            "   - `keys` 非空 → 字段索引模式，可以直接按字段 `field:value` 过滤、做 GROUP BY\n"
            "   - `keys` 为空 + `line: true` → 纯全文索引，只能 `*关键字*` 全文检索，"
            "做 SQL 聚合时只能 GROUP BY 内置字段（如 `__source__`、`__topic__`）\n"
            "4. `sls_search_logs(logstore='<ls>', query='*', from_time='-5m', lines=3)` "
            "—— 拿 1~3 条原始日志看真实字段名，决定后续 prompt 走哪个分支\n\n"
            "之后建议：\n"
            "- 排查 bug 走 `sls-bug-investigation`（必填 logstore + symptom）\n"
            "- 性能分析走 `sls-performance-analysis`（必填 logstore）\n"
        )


def build_server() -> FastMCP:
    s = init_settings()

    if s.creds.configured:
        client.init_client(s.creds, s.defaults.project, s.defaults.logstore)
        logger.info(
            "SLS client ready endpoint=%s default_project=%s default_logstore=%s",
            s.creds.endpoint, s.defaults.project or "<none>", s.defaults.logstore or "<none>",
        )
    else:
        logger.warning(
            "SLS 凭证未配置，工具调用时会报错；请把 ALIYUN_LOG_ENDPOINT / "
            "ALIYUN_LOG_ACCESS_KEY_ID / ALIYUN_LOG_ACCESS_KEY_SECRET 写到 .env"
        )

    mcp = FastMCP(
        name="aliyun_log_mcp",
        instructions=(
            "阿里云 SLS (Simple Log Service) MCP，**只读**。\n\n"
            "### 使用范式（重要）\n"
            "- `ALIYUN_LOG_DEFAULT_PROJECT` 已在 .env 配好；所有工具的 `project` 参数留空即可。\n"
            "- **`logstore` 每次必须显式传**（设计如此：一个 project 下常有几十个 logstore，不能猜）。\n"
            "- 第一次进来不知道有哪些 logstore，先调 `sls_list_logstores()`，或直接走 prompt `sls-quickstart`。\n\n"
            "### 工具地图\n"
            "- 元数据：sls_list_projects / sls_list_logstores / sls_get_logstore / sls_get_index / sls_list_topics\n"
            "- 查询：sls_search_logs（Lucene 或 SQL）/ sls_histogram / sls_sql_query / sls_get_context（上下文）\n"
            "- 排查：sls_error_summary / sls_top_errors / sls_trace_request / sls_qps_timeline / sls_latency_percentiles\n"
            "- 告警：sls_list_alerts / sls_get_alert\n\n"
            "### 关键认知\n"
            "- **必先看 `sls_get_index`**：keys 非空=字段索引（可 `field:value` / GROUP BY）；"
            "keys 为空+`line:true`=纯全文索引（只能 `*关键字*` / 全文搜）。\n"
            "- **k8s 容器 stdout 采集** 通常是全文索引模式，字段是 `content/_pod_name_/_container_name_/_namespace_`；"
            "level 信息在 content 文本里，`sls_error_summary` 默认参数会失效，请传 `level_field='content'` + "
            "`extra_filter='ERROR or WARN'`，或直接 `sls_search_logs(query='ERROR')`。\n"
            "- **`sls_get_context` 在容器采集场景不可用**（缺 `__pack_meta__`），改用 `sls_search_logs` 时间窗 ±30s 替代。\n\n"
            "### 复杂任务走 prompt\n"
            "- 第一次接入：`sls-quickstart`\n"
            "- 排 bug：`sls-bug-investigation`（必填 logstore + symptom）\n"
            "- 性能分析：`sls-performance-analysis`（必填 logstore）\n\n"
            "### 护栏\n"
            "- 时间跨度最大 MAX_TIME_RANGE_HOURS（默认 24h）；单次返回最大 MAX_LINES_PER_QUERY 行（默认 500）\n"
            "- **日志正文常含 token / 手机号 / IDFA / IMEI / IP**；提交结论 / commit / PR 前必须脱敏（红线 2）"
        ),
    )
    register_all(mcp)
    _register_prompts(mcp)
    return mcp


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="aliyun_log_mcp")
    parser.add_argument("--mode", choices=["stdio", "sse", "http"], default=None)
    parser.add_argument("--envfile", default=None, help=".env 文件路径（可选）")
    args = parser.parse_args()

    if args.envfile:
        init_settings(args.envfile)

    mcp = build_server()
    s = get_settings()
    mode = args.mode or s.server.mode

    logger.info("starting aliyun_log_mcp mode=%s endpoint=%s", mode, s.creds.endpoint)
    if mode == "stdio":
        mcp.run("stdio")
    elif mode == "sse":
        mcp.settings.host = s.server.host
        mcp.settings.port = s.server.port
        mcp.run("sse")
    elif mode == "http":
        mcp.settings.host = s.server.host
        mcp.settings.port = s.server.port
        mcp.run("streamable-http")
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
