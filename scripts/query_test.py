r"""查询路径冒烟：get_logs / get_histograms / sql_query / get_context。

**只打印结构摘要**（字段名 / 类型 / 长度 / 计数），不打印日志正文，
避免把含 token / PII 的内容回显到终端或聊天。

跑法:  uv run python scripts\query_test.py [logstore]
默认 logstore: 命令行第 1 个参数，未给则用 ALIYUN_LOG_DEFAULT_LOGSTORE，
仍为空则用 'aos-lvl'（看上去是体量较小的指标流）。
"""

from __future__ import annotations

import asyncio
import sys
import time


def _kv_shape(record: dict) -> dict[str, str]:
    """记录某条日志各字段的『类型 + 长度』摘要，不返回 value 本身。"""
    out = {}
    for k, v in record.items():
        if v is None:
            out[k] = "null"
        elif isinstance(v, bool):
            out[k] = "bool"
        elif isinstance(v, (int, float)):
            out[k] = f"num({v if abs(v) < 1e6 else 'large'})"
        else:
            s = str(v)
            out[k] = f"str(len={len(s)})"
    return out


async def main() -> int:
    from aliyun_log_mcp.server import build_server
    from aliyun_log_mcp.config import get_settings
    from aliyun_log_mcp import client as client_mod
    from aliyun_log_mcp.client import SLSError

    build_server()
    c = client_mod.get_client()
    s = get_settings()

    project = s.defaults.project
    logstore = sys.argv[1] if len(sys.argv) > 1 else (s.defaults.logstore or "aos-lvl")
    if not project:
        print("ALIYUN_LOG_DEFAULT_PROJECT 未配置，退出")
        return 1

    print(f"project  = {project}")
    print(f"logstore = {logstore}")

    now = int(time.time())
    ft = now - 5 * 60  # 最近 5 分钟，量小

    # ── 1) get_logs(query='*', lines=3) ─────────────────────────
    print(f"\n== sls_search_logs(query='*', lines=3, from=-5m) ==")
    try:
        r = await c.get_logs(project, logstore, ft, now, query="", line=3, reverse=True)
        print(f"  count        = {r.get('count')}")
        print(f"  progress     = {r.get('progress')}")
        print(f"  processed    = {r.get('processed_rows')}")
        print(f"  elapsed_ms   = {r.get('elapsed_ms')}")
        logs = r.get("logs", [])
        print(f"  returned     = {len(logs)} log(s)")
        if logs:
            shape = _kv_shape(logs[0])
            print(f"  first log fields ({len(shape)}):")
            for k, t in list(shape.items())[:30]:
                print(f"    {k!r:30s} {t}")
    except SLSError as e:
        print(f"  ERROR {e.error_code}: {e}")

    # ── 2) get_histograms ───────────────────────────────────────
    print(f"\n== sls_histogram(query='*', from=-30m) ==")
    try:
        ft2 = now - 30 * 60
        r = await c.get_histograms(project, logstore, ft2, now, query="")
        print(f"  total_count = {r.get('total_count')}")
        print(f"  progress    = {r.get('progress')}")
        buckets = r.get("histograms", [])
        print(f"  buckets     = {len(buckets)}")
        non_zero = [b for b in buckets if (b.get("count") or 0) > 0]
        print(f"  non_zero    = {len(non_zero)}")
        if buckets:
            sample = buckets[: min(5, len(buckets))]
            for b in sample:
                fmt = time.strftime("%H:%M", time.localtime(b.get("from") or 0))
                print(f"    [{fmt}] count={b.get('count')} progress={b.get('progress')}")
    except SLSError as e:
        print(f"  ERROR {e.error_code}: {e}")

    # ── 3) SQL: count by minute ─────────────────────────────────
    print(f"\n== sls_sql_query (count by minute, last 10m) ==")
    try:
        sql = "* | SELECT date_trunc('minute', __time__) AS m, COUNT(*) AS c GROUP BY m ORDER BY m"
        ft3 = now - 10 * 60
        r = await c.get_logs(project, logstore, ft3, now, query=sql, line=100, reverse=False)
        rows = r.get("logs", [])
        print(f"  rows = {len(rows)}")
        for row in rows[:10]:
            print(f"    {row}")
    except SLSError as e:
        print(f"  ERROR {e.error_code}: {e}")

    # ── 4) get_context (only if we got a log with __pack_id__) ───
    print(f"\n== sls_get_context (if previous log carried __pack_id__) ==")
    try:
        r = await c.get_logs(project, logstore, ft, now, query="", line=1, reverse=True)
        logs = r.get("logs", [])
        if not logs:
            print("  skip: no log to anchor on")
        else:
            l0 = logs[0]
            pid = (
                l0.get("__pack_id__")
                or l0.get("__tag__:__pack_id__")
                or l0.get("__tag__:__pack-id__")
            )
            pmeta = (
                l0.get("__pack_meta__")
                or l0.get("__tag__:__pack_meta__")
            )
            if not pid:
                print("  skip: log has no __pack_id__ (logstore not packed)")
            elif not pmeta:
                # 容器采集场景：常常只有 __tag__:__pack_id__ 没有 pack_meta，
                # SLS get_context_logs 仍可工作但要求把 pack_meta 显式传空 ""
                print(f"  pack_id present but no pack_meta; trying with empty meta...")
                try:
                    ctx = await c.get_context_logs(project, logstore, pid, "", back_lines=3, forward_lines=3)
                    print(f"  total_lines   = {ctx.get('total_lines')}; returned = {len(ctx.get('logs', []))}")
                except SLSError as e:
                    print(f"  context with empty meta failed ({e.error_code}): {e}")
            else:
                ctx = await c.get_context_logs(project, logstore, pid, pmeta, back_lines=3, forward_lines=3)
                print(f"  back_lines    = {ctx.get('back_lines')}")
                print(f"  forward_lines = {ctx.get('forward_lines')}")
                print(f"  total_lines   = {ctx.get('total_lines')}")
                print(f"  returned      = {len(ctx.get('logs', []))} log(s)")
    except SLSError as e:
        print(f"  ERROR {e.error_code}: {e}")

    print("\nQUERY TEST DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
