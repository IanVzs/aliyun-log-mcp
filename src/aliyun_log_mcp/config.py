"""集中式配置加载（同 db_mcp / ctit-mcp 风格）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None and raw != "" else default
    except ValueError:
        return default


@dataclass(slots=True)
class SLSCredentials:
    endpoint: str = "cn-hangzhou.log.aliyuncs.com"
    access_key_id: str = ""
    access_key_secret: str = ""
    security_token: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.access_key_id and self.access_key_secret)


@dataclass(slots=True)
class Defaults:
    project: str = ""
    logstore: str = ""


@dataclass(slots=True)
class Guard:
    """查询安全护栏：所有 SLS 查询入口共用。"""

    default_time_window_minutes: int = 15
    max_lines_per_query: int = 500
    max_time_range_hours: int = 24
    http_timeout: int = 30


@dataclass(slots=True)
class ServerConfig:
    mode: Literal["stdio", "sse", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 3050


@dataclass(slots=True)
class Settings:
    creds: SLSCredentials = field(default_factory=SLSCredentials)
    defaults: Defaults = field(default_factory=Defaults)
    guard: Guard = field(default_factory=Guard)
    server: ServerConfig = field(default_factory=ServerConfig)


def _find_env_file(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_file() else None
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",  # mcps/aliyun-log-mcp/.env
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def load(envfile: str | None = None) -> Settings:
    env_path = _find_env_file(envfile)
    if env_path is not None:
        load_dotenv(env_path, override=False)
    return Settings(
        creds=SLSCredentials(
            endpoint=os.getenv("ALIYUN_LOG_ENDPOINT", "cn-hangzhou.log.aliyuncs.com").strip(),
            access_key_id=os.getenv("ALIYUN_LOG_ACCESS_KEY_ID", "").strip(),
            access_key_secret=os.getenv("ALIYUN_LOG_ACCESS_KEY_SECRET", "").strip(),
            security_token=(os.getenv("ALIYUN_LOG_SECURITY_TOKEN") or "").strip() or None,
        ),
        defaults=Defaults(
            project=os.getenv("ALIYUN_LOG_DEFAULT_PROJECT", "").strip(),
            logstore=os.getenv("ALIYUN_LOG_DEFAULT_LOGSTORE", "").strip(),
        ),
        guard=Guard(
            default_time_window_minutes=_int("DEFAULT_TIME_WINDOW_MINUTES", 15),
            max_lines_per_query=_int("MAX_LINES_PER_QUERY", 500),
            max_time_range_hours=_int("MAX_TIME_RANGE_HOURS", 24),
            http_timeout=_int("HTTP_TIMEOUT", 30),
        ),
        server=ServerConfig(
            mode=os.getenv("SERVER_MODE", "stdio"),  # type: ignore[arg-type]
            host=os.getenv("SERVER_HOST", "127.0.0.1"),
            port=_int("SERVER_PORT", 3050),
        ),
    )


_SETTINGS: Settings | None = None


def init_settings(envfile: str | None = None) -> Settings:
    global _SETTINGS
    _SETTINGS = load(envfile)
    return _SETTINGS


@lru_cache(maxsize=1)
def _default() -> Settings:
    return load(None)


def get_settings() -> Settings:
    return _SETTINGS if _SETTINGS is not None else _default()
