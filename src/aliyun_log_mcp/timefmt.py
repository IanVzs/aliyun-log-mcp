"""时间解析：把用户友好的时间字符串/相对偏移统一成 SLS 用的 unix 秒。

支持的写法：
- ``""``  / None     —— 使用调用方提供的默认值
- 纯整数字符串       —— 视为 unix 秒（>1e12 视为毫秒，自动 /1000）
- ISO8601            —— ``2026-05-22T18:00:00+08:00`` / ``2026-05-22 18:00``
- 相对偏移（基于 now）—— ``-15m`` / ``-1h`` / ``-2d`` / ``-30s``
- ``now``            —— 当前时间
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

_REL_RE = re.compile(r"^-(\d+)\s*([smhd])$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class TimeParseError(ValueError):
    pass


def parse_time(value: str | int | float | None, *, default: int | None = None) -> int:
    """解析时间到 unix 秒。空值 → default（仍为 None 时报错）。"""
    if value in (None, ""):
        if default is None:
            raise TimeParseError("time is required")
        return int(default)

    if isinstance(value, (int, float)):
        ts = float(value)
        return int(ts / 1000) if ts > 1e12 else int(ts)

    s = str(value).strip()

    if s.lower() == "now":
        return int(time.time())

    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        ts = float(s)
        if ts > 1e12:
            ts /= 1000
        return int(ts)

    m = _REL_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        return int(time.time()) - n * _UNIT_SECONDS[unit]

    s_norm = s.replace("Z", "+00:00").replace("/", "-")
    if "T" not in s_norm and " " in s_norm:
        s_norm = s_norm.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s_norm)
    except ValueError as e:
        raise TimeParseError(f"无法解析时间 {value!r}：{e}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fmt_local(ts: int | float) -> str:
    """unix 秒 → 本地时间可读串，便于在工具结果里回显。"""
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


def resolve_window(
    from_time: str | int | None,
    to_time: str | int | None,
    *,
    default_window_minutes: int,
    max_range_hours: int,
) -> tuple[int, int]:
    """计算 [from_ts, to_ts] 闭区间秒；统一应用默认窗口与最大跨度护栏。"""
    now = int(time.time())
    to_ts = parse_time(to_time, default=now)
    from_ts = parse_time(from_time, default=to_ts - default_window_minutes * 60)
    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts
    span_h = (to_ts - from_ts) / 3600
    if span_h > max_range_hours:
        raise TimeParseError(
            f"时间跨度 {span_h:.1f}h 超出上限 {max_range_hours}h，请缩小范围或分批查询"
        )
    return from_ts, to_ts
