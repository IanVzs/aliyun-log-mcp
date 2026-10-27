# aliyun-log-mcp

> 阿里云**日志服务 SLS** 的 MCP server。让 AI 直接读线上项目的日志，**辅助排查 bug / 性能瓶颈 / 代码优化**

---

## 目录

- [它解决什么](#它解决什么)
- [快速开始（3 分钟接入）](#快速开始3-分钟接入)
- [使用范式（默认 project + 显式 logstore）](#使用范式默认-project--显式-logstore)
- [使用指南：5 个真实场景](#使用指南5-个真实场景)
  - [场景 1：第一次进来，先摸清结构](#场景-1第一次进来先摸清结构)
  - [场景 2：定位线上 ERROR 突增](#场景-2定位线上-error-突增)
  - [场景 3：按 trace_id 拉全链路](#场景-3按-trace_id-拉全链路)
  - [场景 4：性能分析（延迟分位 + 慢请求样本）](#场景-4性能分析延迟分位--慢请求样本)
  - [场景 5：流量曲线 / QPS 突增定位](#场景-5流量曲线--qps-突增定位)
- [工具一览（16 个）](#工具一览16-个)
- [时间参数怎么写](#时间参数怎么写)
- [采集模式：字段索引 vs 全文索引（必读）](#采集模式字段索引-vs-全文索引必读)
- [k8s 容器采集字段速查](#k8s-容器采集字段速查)
- [Prompt 用法](#prompt-用法)
- [安全 / 合规边界](#安全--合规边界)
- [排错](#排错)
- [仓库布局](#仓库布局)
- [参考](#参考)

---

## 它解决什么

给AI线上日志，直连 SLS GetLogs / GetHistograms / GetContextLogs 等只读 API 
写 SLS SQL 现场翻文档 | 内置 `sls_error_summary` / `sls_top_errors` / `sls_qps_timeline` / `sls_latency_percentiles` 等高阶组合工具
时间窗 / 行数失控、AK 误用 | `.env` 集中管理，强制时间跨度上限 + 行数上限 + 默认窗口

**默认只读。** 这个 MCP 不暴露任何写入/删除接口（不写日志、不改 logstore、不删告警）；写操作请走阿里云控制台或独立 MCP。

---

## 快速开始（3 分钟接入）

### 1) 装依赖

```bash
cd mcps/aliyun-log-mcp
uv sync                            # 或：pip install -r requirements.txt
```

### 2) 配 `.env`

```bash
cp .env.example .env
```

最少要填这三项：

```ini
ALIYUN_LOG_ENDPOINT=ap-southeast-111.log.aliyuncs.com   # 与 project 所在 Region 一致
ALIYUN_LOG_ACCESS_KEY_ID=<RAM 子账号 AK>
ALIYUN_LOG_ACCESS_KEY_SECRET=<RAM 子账号 SK>
ALIYUN_LOG_DEFAULT_PROJECT=<你的主要 project 名>      # 强烈推荐配，省去每次传
# ALIYUN_LOG_DEFAULT_LOGSTORE 不要配，logstore 设计为每次显式传
```

#### RAM 权限（最小读权限策略）

```json
{
  "Version": "1",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["log:ListProject"],
      "Resource": "acs:log:*:*:project/*" },
    { "Effect": "Allow",
      "Action": ["log:ListLogStores", "log:GetLogStore", "log:GetIndex", "log:ListTopics"],
      "Resource": "acs:log:*:*:project/<project>/logstore/*" },
    { "Effect": "Allow",
      "Action": ["log:GetLogStoreLogs", "log:GetLogStoreHistogram", "log:GetContextLogs"],
      "Resource": "acs:log:*:*:project/<project>/logstore/*" },
    { "Effect": "Allow",
      "Action": ["log:GetAlert", "log:ListAlerts"],
      "Resource": "acs:log:*:*:project/<project>/alert/*" }
  ]
}
```

最快开通：直接挂内置 `AliyunLogReadOnlyAccess`（全 region 全 project 只读）。

### 3) 接进 Cursor

`~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "aliyun_log_mcp": {
      "command": "uv",
      "args": [
        "--directory", "mcps/aliyun-log-mcp",
        "run", "python", "-m", "aliyun_log_mcp"
      ],
      "env": {}
    }
  }
}
```

替换 `--directory` 后的路径为你本机的实际路径，重启 Cursor。

### 4) 第一次用

随便说一句：

> 用 `sls-quickstart` 看看我能访问的 logstore 有哪些。

---

## 使用范式（默认 project + 显式 logstore）

这是本 MCP **最重要的一条约定**，记下来后面会反复用到：

| 参数 | 默认行为 | 你怎么传 |
|---|---|---|
| `project` | `.env::ALIYUN_LOG_DEFAULT_PROJECT` | 一般留空即可；跨项目时显式传 |
| `logstore` | **无默认** | **每次必传**（一个 project 下常有几十个 logstore，不能猜） |
| `from_time` / `to_time` | 取最近 `DEFAULT_TIME_WINDOW_MINUTES`（默认 15 分钟） | 排查时一般显式传 `-1h` / `-30m` 等 |

> 设计原因：默认 logstore 一旦配上，AI 容易"懒"地永远查那个；强制每次显式传可以让 AI 与人都对查询目标有清晰意识。

---

## 使用指南：5 个真实场景

> 下面所有示例都假设你在 `.env` 配了 `ALIYUN_LOG_DEFAULT_PROJECT`。**直接在 Cursor 里贴这些话**就行，不用自己挑工具。

### 场景 1：第一次进来，先摸清结构

**说**：

> 用 `sls-quickstart` 跑一遍。

**它会做**：

1. `sls_list_logstores()` —— 列默认 project 下所有 logstore
2. 你（或 AI）挑一个 logstore，比如 `aos-lvl`
3. `sls_get_logstore(logstore='aos-lvl')` —— 看 TTL、shard
4. `sls_get_index(logstore='aos-lvl')` —— 判定字段索引模式还是全文索引模式
5. `sls_search_logs(logstore='aos-lvl', query='*', from_time='-5m', lines=3)` —— 拿 3 条样本看真实字段名

**为什么必要**：决定后续工具调用走"结构化分支"还是"容器全文分支"，跳过这步会大量空查。

---

### 场景 2：定位线上 ERROR 突增

**说**：

> 用 `sls-bug-investigation` 排查，logstore=`<你的应用 logstore>`，symptom="订单接口最近 30 分钟大量 500"，time_window="-30m"。

**它会做**（按你 logstore 是结构化采集还是容器采集自适应）：

| 步骤 | 结构化采集（有 `level` / `message` 字段） | 容器 stdout 采集（只有 `content` 字段） |
|---|---|---|
| 1 | `sls_error_summary(logstore=..., level_field='level')` | `sls_search_logs(logstore=..., query='ERROR', lines=20)` |
| 2 | `sls_histogram(query='level:ERROR')` 定位异常时段 | `sls_histogram(query='ERROR')` |
| 3 | `sls_top_errors(message_field='message', level_value='ERROR')` | 按 `_pod_name_` / `_container_name_` 在结果里手动归类 |
| 4 | 抽样 `sls_search_logs(query='message:"<关键串>"', lines=5)` | 抽样 `sls_search_logs(query='ERROR and _pod_name_:"<pod>"', lines=5)` |
| 5 | `sls_get_context(pack_id=..., pack_meta=...)` 看上下文 | `sls_get_context` 不可用 → 用 `sls_search_logs` 时间窗 ±30s 替代 |
| 6 | 若有 trace_id：`sls_trace_request(trace_id='...')` 全链路 | 同左（若 trace_id 在文本里就只能全文搜） |

**结论必含**：根因 + 复现条件 + 修复点（精确到 文件:行 / 函数）。

---

### 场景 3：按 trace_id 拉全链路

**说**：

> 在 logstore=`<x>`，最近 2 小时，trace_id=`abc123xyz` 的全部日志拉出来按时间排好。

**它调**：

```
sls_trace_request(
  logstore='<x>',
  trace_id='abc123xyz',
  id_field='trace_id',       # 不同框架可能是 'request_id' / 'x-request-id' / 'traceId'
  from_time='-2h',
  lines=200,
)
```

返回按 `__time__` 升序的日志列表，能直接看出请求经过哪些服务、各阶段耗时。

---

### 场景 4：性能分析（延迟分位 + 慢请求样本）

**前提**：你的 logstore 必须把 `latency_ms`（或类似字段）建为 `long` / `double` 索引。

**说**：

> 用 `sls-performance-analysis`，logstore=`<x>`，api="/v1/order"，time_window="-2h"。

**它会做**：

1. `sls_get_index` 确认 `latency_ms` 是 long/double
2. `sls_qps_timeline(interval='minute', extra_filter='api:/v1/order')` 看流量曲线
3. `sls_latency_percentiles(latency_field='latency_ms', extra_filter='api:/v1/order')` 拿 P50/P90/P95/P99
4. `sls_sql_query(query='api:/v1/order and "latency_ms":>1000 | SELECT * ORDER BY "latency_ms" DESC LIMIT 10')` 抓最慢 10 条
5. 选 1~2 条最慢的，提取 trace_id 走 `sls_trace_request`

**结论**：瓶颈在 上游 / 下游 / DB / 缓存 / GC / 网络 / 限流。

---

### 场景 5：流量曲线 / QPS 突增定位

**说**：

> logstore=`<x>` 最近 1 小时按分钟看 QPS，如果有突增/跌零定位是哪台机器/哪个 pod。

**它调**：

```
sls_qps_timeline(logstore='<x>', interval='minute', from_time='-1h')
```

发现 14:15 那分钟流量从 3700 跳到 6961 后，下钻：

```
sls_sql_query(
  logstore='<x>',
  query='* | SELECT _pod_name_, COUNT(*) AS c GROUP BY _pod_name_ ORDER BY c DESC LIMIT 10',
  from_time='2026-05-23 14:15:00',
  to_time='2026-05-23 14:16:00',
)
```

> 注意：`GROUP BY _pod_name_` 只在 `_pod_name_` 有字段索引时可用；纯全文索引模式下需要先在 SLS 控制台对 `_pod_name_` 建索引。

---

## 工具一览（16 个）

### 元数据（5）

| 工具 | 作用 |
|---|---|
| `sls_list_projects` | 列出当前 AK 可见的所有 Project |
| `sls_list_logstores` | 某 Project 下全部 Logstore |
| `sls_get_logstore` | Logstore 元数据：shard / TTL / 计费模式 |
| `sls_get_index` | **每次新接 logstore 必调的第一步**——判定字段索引 vs 全文索引 |
| `sls_list_topics` | Logstore 下出现过的 topic |

### 查询（4）

| 工具 | 作用 |
|---|---|
| `sls_search_logs` | 主查询入口，支持 Lucene 与 SQL；`topic` / `lines` / `reverse` 可调 |
| `sls_histogram` | 时间分桶直方图——**先用它定位异常时段再下钻** |
| `sls_sql_query` | SQL 分析专用入口（必须含 `\|`） |
| `sls_get_context` | 取一条日志的前后 N 条——**容器采集场景不可用**，用 `sls_search_logs ±30s` 替代 |

### 排查 / 分析（5）

| 工具 | 作用 |
|---|---|
| `sls_error_summary` | 按 level 字段汇总 ERROR/WARN 数量（容器场景需调参） |
| `sls_top_errors` | Top N 错误消息聚合 |
| `sls_trace_request` | 按 trace_id / request_id 拉全链路日志（按时间升序）|
| `sls_qps_timeline` | 按 second/minute/hour/day 桶统计 QPS 时序 |
| `sls_latency_percentiles` | 延迟 P50/P90/P95/P99 + max/avg/count |

### 告警（2）

| 工具 | 作用 |
|---|---|
| `sls_list_alerts` | 列告警规则 |
| `sls_get_alert` | 单条告警规则详情 |

---

## 时间参数怎么写

所有工具的 `from_time` / `to_time` 都接受这几种写法：

| 写法 | 含义 |
|---|---|
| `""` 或省略 | 用 `.env::DEFAULT_TIME_WINDOW_MINUTES`（默认最近 15 分钟） |
| `-15m` / `-1h` / `-2d` / `-30s` | 相对当前时间往前偏移 |
| `now` | 当前时刻 |
| `1716364800` | unix 秒（>1e12 视为毫秒，自动 /1000） |
| `2026-05-22T18:00:00+08:00` | ISO8601 |
| `2026-05-22 18:00` | 简写 ISO（不带时区 → 视为 UTC） |

跨度超过 `MAX_TIME_RANGE_HOURS`（默认 24h）会被拒，避免误打全表。

---

## 采集模式：字段索引 vs 全文索引（必读）

SLS 的所有查询能力都依赖**索引模式**，不看索引就写查询基本是浪费时间。

| 模式 | `sls_get_index` 返回 | 能力 | 典型场景 |
|---|---|---|---|
| **字段索引** | `keys` 非空（如 `{"level":{"type":"text"}, "latency_ms":{"type":"long"}}`） | `field:value` 精确过滤、`GROUP BY field`、`approx_percentile(field)` 全部可用 | 应用日志通过 SLS Logtail 解析后采集（key-value 模式） |
| **纯全文索引** | `keys` 为空 + `line: true` | 只能 `*关键字*` 全文搜；SQL 聚合只能 GROUP BY 内置字段（`__source__` / `__topic__` / 容器采集的 `_pod_name_` 等内置 tag） | **k8s 容器 stdout 采集**、Nginx 原始日志 |
| **未建索引** | 两者都没有 | 查询会全表扫，又慢又贵 | 配置错的 logstore |

**结论**：每接一个新 logstore，**第一件事**就是 `sls_get_index`。

---

## k8s 容器采集字段速查

阿里云 ACK 集群默认开的 `k8s-log-<cluster-id>` 项目下，容器 stdout 采集的 logstore 字段长这样：

```
content                    # 整行原始日志
_time_                     # 容器内打印时间
_source_                   # 来源（一般是 'stdout' 或 'stderr'）
_container_name_           # 容器名
_pod_name_                 # pod 名
_namespace_                # k8s namespace
_pod_uid_                  # pod UID
_container_ip_             # pod IP
_image_name_               # 容器镜像名
__topic__                  # SLS topic（一般为空）
__tag__:__pack_id__        # SLS 包 ID（注意带 __tag__: 前缀！）
__tag__:__hostname__       # 节点 hostname
__tag__:_pod_ip_           # pod IP（重复，来自 tag）
__tag__:acs_id             # 阿里云用户 ID
__tag__:__receive_time__   # SLS 接收时间
```

**重要差异**：

- pack 字段名是 `__tag__:__pack_id__`，**不是** `__pack_id__`；而且通常**没有** `__pack_meta__`，所以 `sls_get_context` 在此场景不可用
- `level` 不存在，要分级别看错误必须全文检索 `query='ERROR'`
- `_pod_name_` / `_container_name_` / `_namespace_` 通常作为 tag 存在，是否能 `WHERE` / `GROUP BY` 要看 `sls_get_index` 是否给它们建了字段索引

---

## Prompt 用法

三个 Prompt 是引导 AI 按固定方法论走查的"剧本"，比让 AI 自由发挥稳定得多。

| Prompt | 入参 | 用途 |
|---|---|---|
| `sls-quickstart` | `project`（可选） | 第一次接入：列 logstore → 看元数据 → 看索引 → 抓样本 |
| `sls-bug-investigation` | `logstore`（必填）、`symptom`、`time_window`、`project` | **bug 排查标准链路**，会自动按字段索引 / 全文索引分支走 |
| `sls-performance-analysis` | `logstore`（必填）、`api`、`time_window`、`latency_field`、`project` | 性能分析：流量曲线 → 分位数 → 慢请求样本 |

在 Cursor 里直接 `/` 触发 prompt 或者直接说"用 sls-bug-investigation 跑一遍 ..."。

---

## 安全 / 合规边界

1. **只读**：本 MCP 不暴露任何写/删接口；写操作请走阿里云控制台或独立 MCP。
2. **AK 最小权限**：用 RAM 子账号 + `AliyunLogReadOnlyAccess`（或上面的最小策略），**不要用主账号 AK**。
3. **时间窗 / 行数双护栏**：默认窗口 15min，单查最大 500 行，最大跨度 24h；可在 `.env` 调，但**别调到全开**。
4. **敏感字段脱敏**：从 SLS 拉回来的日志正文里可能含 token / 手机号 / 身份证 / IDFA。**写进文档 / commit message / PR 描述前必须脱敏**——AI 不会主动脱敏，你得在 prompt 里要求。
5. **审计**：阿里云 SLS 自身在控制台 Operation Center 留有 API 调用日志，所有调用均带本 MCP 配置的 AK，可追溯。

---

## 排错

| 现象 | 原因 / 排查 |
|---|---|
| `SLS 凭证未配置` | `.env` 里 `ALIYUN_LOG_ACCESS_KEY_ID/SECRET` 没填或没生效；重启 Cursor |
| `Unauthorized: denied by sts or ram, action: log:ListProject` | RAM 子账号缺权限。最小读权限策略见上方"快速开始"；最快办法挂内置 `AliyunLogReadOnlyAccess` |
| `LogException 403` | AK 没有目标 project 权限 → RAM 控制台调权限；或 endpoint 与 project Region 不匹配 |
| `时间跨度 ... 超出上限` | 缩小 `from_time`/`to_time`，或调大 `.env::MAX_TIME_RANGE_HOURS`（谨慎） |
| 查询返回 0 但控制台有数据 | 大概率字段名拼错 / 字段未建索引；先 `sls_get_index` 看真实字段名 |
| 工具不出现在 Cursor | 看 `~/.cursor/logs/` 最新 MCP 日志；多半是 `uv` 不在 PATH 或 path 没改 |
| `power_sql` 慢且贵 | 是 SQL 独享版（按量计费）；改用默认 SQL，或缩小时间窗 |
| `sls_get_context` 报 `missing query key : pack_meta` | **k8s 容器 stdout 采集**通常只有 `__tag__:__pack_id__` 而没有 `pack_meta`，SLS API 强要求 `pack_meta`，所以本工具在此场景**不可用**。退路：用 `sls_search_logs(query='_pod_name_:"<pod>" and _container_name_:"<container>"', from_time='<目标时间-30s>', to_time='<目标时间+30s>', reverse=False)` 拿前后日志 |
| `sls_error_summary` 在容器 logstore 返回 0 或全是噪声 | 字段名是 `content` 不是 `level`。传 `level_field='content'` + `extra_filter='ERROR or WARN'`，或者直接 `sls_search_logs(query='ERROR')` |
| SQL `GROUP BY _pod_name_` 报字段不存在 | 该 logstore 没给 `_pod_name_` 建字段索引；只能 SLS 控制台手动加索引，或退化为 `sls_search_logs` 后端聚合 |

---

## 仓库布局

```
aliyun-log-mcp/
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── scripts/                # 本地回归脚本
│   ├── smoke_test.py       # MCP 协议层冒烟（不联网）
│   ├── live_test.py        # SLS 只读元数据冒烟
│   └── query_test.py       # 查询路径冒烟（只输出结构摘要、不输出正文）
└── src/aliyun_log_mcp/
    ├── __init__.py
    ├── __main__.py
    ├── server.py           # FastMCP + 3 个 prompt
    ├── config.py           # 配置加载（凭证 / 默认值 / 护栏）
    ├── client.py           # 阿里云 SDK 异步桥（asyncio.to_thread）
    ├── timefmt.py          # 时间参数解析（ISO / unix / -1h / now）
    └── tools/
        ├── __init__.py
        ├── meta.py         # list/get 元数据
        ├── query.py        # search/histogram/sql/context
        ├── diagnose.py     # error_summary/top_errors/trace/qps/p99
        └── alert.py        # list/get 告警
```

回归跑法：

```bash
uv run python scripts/smoke_test.py    # MCP 协议层
uv run python scripts/live_test.py     # SLS 元数据连通性
uv run python scripts/query_test.py    # 查询路径 + 结构摘要
```

---

## 参考

- [阿里云 SLS 文档总览](https://help.aliyun.com/zh/sls/)
- [Python SDK 参考](https://help.aliyun.com/zh/sls/developer-reference/python-sdk-overview)
- [GetLogs / GetHistograms API](https://help.aliyun.com/zh/sls/developer-reference/api-getlogs)
- [GetContextLogs](https://help.aliyun.com/zh/sls/developer-reference/api-getcontextlogs)
- [SLS SQL 分析语法](https://help.aliyun.com/zh/sls/user-guide/sql-syntax)
- [RAM 权限模型](https://help.aliyun.com/zh/sls/user-guide/permission-and-authorization)

---

## License

MIT
