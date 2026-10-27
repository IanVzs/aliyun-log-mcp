"""aliyun-log-mcp 冒烟测试。

不发起 SLS 请求，只验证:
  1. 配置加载
  2. SLS 客户端能初始化(不连网)
  3. FastMCP server 能 build, 所有工具和 prompt 都注册
  4. 打印工具/prompt 清单, 用作回归基线

跑法: uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback

EXPECTED_TOOLS = {
    # meta
    "sls_list_projects",
    "sls_list_logstores",
    "sls_get_logstore",
    "sls_get_index",
    "sls_list_topics",
    # query
    "sls_search_logs",
    "sls_histogram",
    "sls_sql_query",
    "sls_get_context",
    # diagnose
    "sls_error_summary",
    "sls_top_errors",
    "sls_trace_request",
    "sls_qps_timeline",
    "sls_latency_percentiles",
    # alert
    "sls_list_alerts",
    "sls_get_alert",
}

EXPECTED_PROMPTS = {
    "sls-bug-investigation",
    "sls-performance-analysis",
    "sls-quickstart",
}


async def main() -> int:
    from aliyun_log_mcp.server import build_server
    from aliyun_log_mcp.config import get_settings

    print("== step 1: build_server ==")
    try:
        mcp = build_server()
    except Exception:
        traceback.print_exc()
        return 1

    s = get_settings()
    print(f"endpoint            = {s.creds.endpoint}")
    print(f"creds configured    = {s.creds.configured}")
    print(f"default project     = {s.defaults.project or '(none)'}")
    print(f"default logstore    = {s.defaults.logstore or '(none)'}")
    print(f"guard.window_min    = {s.guard.default_time_window_minutes}")
    print(f"guard.max_lines     = {s.guard.max_lines_per_query}")
    print(f"guard.max_range_h   = {s.guard.max_time_range_hours}")

    print("\n== step 2: list registered tools ==")
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    print(f"registered: {len(tool_names)} tools")
    for t in sorted(tool_names):
        marker = "+" if t in EXPECTED_TOOLS else "?"
        print(f"  {marker} {t}")

    missing = EXPECTED_TOOLS - tool_names
    extra = tool_names - EXPECTED_TOOLS
    if missing:
        print(f"!! MISSING tools: {sorted(missing)}")
    if extra:
        print(f"?? EXTRA tools (unexpected): {sorted(extra)}")

    print("\n== step 3: list registered prompts ==")
    prompts = await mcp.list_prompts()
    prompt_names = {p.name for p in prompts}
    for p in sorted(prompt_names):
        marker = "+" if p in EXPECTED_PROMPTS else "?"
        print(f"  {marker} {p}")
    p_missing = EXPECTED_PROMPTS - prompt_names
    if p_missing:
        print(f"!! MISSING prompts: {sorted(p_missing)}")

    print("\n== step 4: check tool schemas have description ==")
    no_desc = [t.name for t in tools if not (t.description and t.description.strip())]
    if no_desc:
        print(f"!! tools without description: {no_desc}")
    else:
        print("  all tools have descriptions")

    print("\n== step 5: sanity-check time parser ==")
    from aliyun_log_mcp.timefmt import parse_time, resolve_window
    cases = ["-15m", "-1h", "-2d", "now", "1716364800"]
    for c in cases:
        try:
            ts = parse_time(c)
            print(f"  parse_time({c!r}) -> {ts}")
        except Exception as e:
            print(f"  parse_time({c!r}) FAILED: {e}")
    try:
        ft, tt = resolve_window("-1h", "now", default_window_minutes=15, max_range_hours=24)
        print(f"  resolve_window('-1h','now') -> from={ft} to={tt} span_h={(tt-ft)/3600:.2f}")
    except Exception as e:
        print(f"  resolve_window FAILED: {e}")

    if missing or p_missing or no_desc:
        return 2
    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
