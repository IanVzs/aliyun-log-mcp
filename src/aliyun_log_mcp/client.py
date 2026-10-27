"""阿里云 SLS (Simple Log Service) 客户端封装。

底层用官方 ``aliyun-log-python-sdk``（同步 IO），所有方法用 ``asyncio.to_thread``
桥接成异步，避免阻塞 FastMCP 事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aliyun.log import LogClient
    from aliyun.log.logexception import LogException
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "aliyun-log-python-sdk 未安装；请 `pip install aliyun-log-python-sdk` 或 `uv sync`"
    ) from e

from .config import SLSCredentials

logger = logging.getLogger("aliyun_log_mcp.client")


class SLSError(RuntimeError):
    """统一抛给上层的错误。"""

    def __init__(self, message: str, *, error_code: str = "", request_id: str = "", http_code: int = 0):
        super().__init__(message)
        self.error_code = error_code
        self.request_id = request_id
        self.http_code = http_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "error_code": self.error_code,
            "request_id": self.request_id,
            "http_code": self.http_code,
        }


class SLSClient:
    """对 ``aliyun.log.LogClient`` 的薄包装：异步桥 + 错误归一 + 默认 project/logstore 注入。"""

    def __init__(self, creds: SLSCredentials, default_project: str = "", default_logstore: str = ""):
        if not creds.configured:
            raise RuntimeError(
                "SLS 凭证未配置：请在 .env 设置 ALIYUN_LOG_ENDPOINT / "
                "ALIYUN_LOG_ACCESS_KEY_ID / ALIYUN_LOG_ACCESS_KEY_SECRET"
            )
        self._client = LogClient(
            endpoint=creds.endpoint,
            accessKeyId=creds.access_key_id,
            accessKey=creds.access_key_secret,
            securityToken=creds.security_token,
        )
        self.default_project = default_project
        self.default_logstore = default_logstore

    def resolve(self, project: str = "", logstore: str = "") -> tuple[str, str]:
        p = project or self.default_project
        ls = logstore or self.default_logstore
        if not p:
            raise SLSError("project 未指定且没有默认 ALIYUN_LOG_DEFAULT_PROJECT")
        return p, ls

    # ── 内部：把同步调用扔进线程池 + 异常归一 ───────────────────────────────
    async def _run(self, fn, *args, **kwargs) -> Any:
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except LogException as e:
            raise SLSError(
                str(getattr(e, "get_error_message", lambda: e)() or e),
                error_code=str(getattr(e, "get_error_code", lambda: "")() or ""),
                request_id=str(getattr(e, "get_request_id", lambda: "")() or ""),
                http_code=int(getattr(e, "get_http_code", lambda: 0)() or 0),
            ) from e
        except Exception as e:  # 网络/SDK 内部错误
            raise SLSError(f"SLS call failed: {e.__class__.__name__}: {e}") from e

    # ── 元数据 ─────────────────────────────────────────────────────────────
    async def list_projects(self, size: int = 100, offset: int = 0) -> dict[str, Any]:
        resp = await self._run(self._client.list_project, size=size, offset=offset)
        return _resp_body(resp)

    async def list_logstores(self, project: str, size: int = 100, offset: int = 0) -> dict[str, Any]:
        resp = await self._run(self._client.list_logstore, project, size=size, offset=offset)
        return _resp_body(resp)

    async def get_logstore(self, project: str, logstore: str) -> dict[str, Any]:
        resp = await self._run(self._client.get_logstore, project, logstore)
        return _resp_body(resp)

    async def get_index_config(self, project: str, logstore: str) -> dict[str, Any]:
        resp = await self._run(self._client.get_index_config, project, logstore)
        return _resp_body(resp)

    async def list_topics(self, project: str, logstore: str, token: str = "", line: int = 100) -> dict[str, Any]:
        resp = await self._run(self._client.list_topics, project, logstore, token=token, line=line)
        return _resp_body(resp)

    # ── 查询 ────────────────────────────────────────────────────────────────
    async def get_logs(
        self,
        project: str,
        logstore: str,
        from_time: int,
        to_time: int,
        query: str = "",
        topic: str = "",
        line: int = 100,
        offset: int = 0,
        reverse: bool = True,
        power_sql: bool = False,
    ) -> dict[str, Any]:
        """SLS 主查询：query 同时支持 Lucene 与 SQL（带 `|` SELECT）。"""
        from aliyun.log import GetLogsRequest

        req = GetLogsRequest(
            project=project,
            logstore=logstore,
            fromTime=from_time,
            toTime=to_time,
            topic=topic or "",
            query=query or "",
            line=line,
            offset=offset,
            reverse=reverse,
            power_sql=power_sql,
        )
        resp = await self._run(self._client.get_logs, req)
        logs = [l.get_contents() for l in getattr(resp, "get_logs", lambda: [])()]
        return {
            "count": getattr(resp, "get_count", lambda: len(logs))(),
            "progress": getattr(resp, "get_progress", lambda: "Complete")(),
            "processed_rows": getattr(resp, "get_processed_rows", lambda: None)(),
            "elapsed_ms": getattr(resp, "get_elapsed_milli", lambda: None)(),
            "logs": logs,
        }

    async def get_histograms(
        self,
        project: str,
        logstore: str,
        from_time: int,
        to_time: int,
        query: str = "",
        topic: str = "",
    ) -> dict[str, Any]:
        from aliyun.log import GetHistogramsRequest

        req = GetHistogramsRequest(
            project=project,
            logstore=logstore,
            fromTime=from_time,
            toTime=to_time,
            topic=topic or "",
            query=query or "",
        )
        resp = await self._run(self._client.get_histograms, req)
        histograms = [
            {
                "from": getattr(h, "get_from_time", lambda: None)(),
                "to": getattr(h, "get_to_time", lambda: None)(),
                "count": getattr(h, "get_count", lambda: None)(),
                "progress": getattr(h, "get_progress", lambda: None)(),
            }
            for h in getattr(resp, "get_histograms", lambda: [])()
        ]
        return {
            "total_count": getattr(resp, "get_total_count", lambda: None)(),
            "progress": getattr(resp, "get_progress", lambda: None)(),
            "histograms": histograms,
        }

    async def get_context_logs(
        self,
        project: str,
        logstore: str,
        pack_id: str,
        pack_meta: str,
        back_lines: int = 10,
        forward_lines: int = 10,
    ) -> dict[str, Any]:
        """根据 log 自带 ``__pack_id__`` / ``__pack_meta__`` 取上下文日志，排查 bug 神器。"""
        resp = await self._run(
            self._client.get_context_logs,
            project,
            logstore,
            pack_id,
            pack_meta,
            back_lines,
            forward_lines,
        )
        logs = [l.get_contents() for l in getattr(resp, "get_logs", lambda: [])()]
        return {
            "back_lines": getattr(resp, "get_back_lines", lambda: None)(),
            "forward_lines": getattr(resp, "get_forward_lines", lambda: None)(),
            "total_lines": getattr(resp, "get_total_lines", lambda: None)(),
            "logs": logs,
        }

    # ── 告警 ────────────────────────────────────────────────────────────────
    async def list_alerts(self, project: str, size: int = 100, offset: int = 0) -> dict[str, Any]:
        resp = await self._run(self._client.list_alert, project, size=size, offset=offset)
        return _resp_body(resp)

    async def get_alert(self, project: str, alert_name: str) -> dict[str, Any]:
        resp = await self._run(self._client.get_alert, project, alert_name)
        return _resp_body(resp)


def _resp_body(resp: Any) -> Any:
    """阿里云 SDK 多数响应有 ``body`` 字段或 ``get_body()`` 方法，这里统一拍扁。"""
    if resp is None:
        return None
    if hasattr(resp, "get_body"):
        try:
            return resp.get_body()
        except Exception:
            pass
    if hasattr(resp, "body"):
        return resp.body
    if isinstance(resp, dict):
        return resp
    return repr(resp)


_CLIENT: SLSClient | None = None


def init_client(creds: SLSCredentials, default_project: str = "", default_logstore: str = "") -> SLSClient:
    global _CLIENT
    _CLIENT = SLSClient(creds, default_project, default_logstore)
    return _CLIENT


def get_client() -> SLSClient:
    if _CLIENT is None:
        raise RuntimeError("SLSClient not initialized — 检查 .env 中的 ALIYUN_LOG_* 是否配齐")
    return _CLIENT
