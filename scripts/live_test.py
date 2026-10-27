"""实际调用阿里云 SLS 只读 API 验证 AK / endpoint / 权限是否通。

只跑以下安全的只读接口:
  1. list_projects     - 看 AK 能看到哪些 project
  2. list_logstores    - 看默认 project 下有哪些 logstore (如果默认 project 已配)
  3. get_logstore      - 取第一个 logstore 的元数据 (TTL/shard)
  4. get_index_config  - 看索引配置 (字段名)

跑法: uv run python scripts/live_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback


async def main() -> int:
    from aliyun_log_mcp.server import build_server  # 触发 init_client
    from aliyun_log_mcp.config import get_settings
    from aliyun_log_mcp import client as client_mod
    from aliyun_log_mcp.client import SLSError

    build_server()
    c = client_mod.get_client()
    s = get_settings()

    print(f"endpoint        = {s.creds.endpoint}")
    print(f"default project = {s.defaults.project or '(none)'}")
    print()

    # ── 1) list_projects ─────────────────────────────────────────
    print("== sls_list_projects (size=10) ==")
    try:
        data = await c.list_projects(size=10)
        projects = []
        if isinstance(data, dict):
            projects = data.get("projects") or data.get("Projects") or []
        print(f"  total visible (this page): {len(projects)}")
        for p in projects[:10]:
            name = p.get("projectName") or p.get("ProjectName") or p
            region = p.get("region") or p.get("Region") or ""
            print(f"    - {name}   region={region}")
    except SLSError as e:
        print(f"  SKIP (no project-level perm): {e.error_code}: {e}")

    # ── 2) list_logstores in default project ─────────────────────
    if not s.defaults.project:
        print("\n(no default project, skipping list_logstores)")
        return 0

    print(f"\n== sls_list_logstores(project={s.defaults.project!r}) ==")
    first_logstore = None
    try:
        data = await c.list_logstores(s.defaults.project, size=20)
        if isinstance(data, dict):
            stores = data.get("logstores") or data.get("Logstores") or []
        else:
            stores = []
        print(f"  total in this page: {len(stores)}")
        for ls in stores[:20]:
            name = ls if isinstance(ls, str) else (ls.get("logstoreName") or ls.get("LogstoreName"))
            print(f"    - {name}")
        if stores:
            first_logstore = stores[0] if isinstance(stores[0], str) else (
                stores[0].get("logstoreName") or stores[0].get("LogstoreName")
            )
    except SLSError as e:
        print(f"  SKIP (no list perm): {e.error_code}: {e}")
        # 用户可在 ALIYUN_LOG_DEFAULT_LOGSTORE 指定要测试的 logstore
        first_logstore = s.defaults.logstore or None
        if first_logstore:
            print(f"  -> using ALIYUN_LOG_DEFAULT_LOGSTORE={first_logstore} for downstream tests")

    if not first_logstore:
        print("\n(no logstores, skipping rest)")
        return 0

    # ── 3) get_logstore ──────────────────────────────────────────
    print(f"\n== sls_get_logstore(project=*, logstore={first_logstore!r}) ==")
    try:
        meta = await c.get_logstore(s.defaults.project, first_logstore)
        if isinstance(meta, dict):
            for k in ("logstoreName", "ttl", "shardCount", "autoSplit", "maxSplitShard", "appendMeta", "enable_tracking"):
                if k in meta:
                    print(f"    {k}: {meta[k]}")
            other = [k for k in meta if k not in {"logstoreName", "ttl", "shardCount"}]
            if other:
                print(f"    (other keys: {other[:8]}...)")
        else:
            print(f"    raw: {str(meta)[:300]}")
    except SLSError as e:
        print(f"  ERROR {e.error_code} {e.http_code}: {e}")
        return 1

    # ── 4) get_index_config ──────────────────────────────────────
    print(f"\n== sls_get_index({first_logstore!r}) ==")
    try:
        idx = await c.get_index_config(s.defaults.project, first_logstore)
        if isinstance(idx, dict):
            keys = idx.get("keys") or idx.get("Keys") or {}
            print(f"    indexed keys: {len(keys)}")
            for fname in list(keys)[:10]:
                ftype = keys[fname].get("type") if isinstance(keys[fname], dict) else "?"
                print(f"      - {fname}  type={ftype}")
            if "line" in idx:
                print(f"    full-text index: {bool(idx['line'])}")
        else:
            print(f"    raw: {str(idx)[:300]}")
    except SLSError as e:
        print(f"  ERROR {e.error_code} {e.http_code}: {e}")
        return 1

    print("\nLIVE TEST PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
