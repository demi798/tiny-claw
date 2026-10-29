"""轻量链路追踪：用 contextvars 模拟 Go context.Context 挂载 Span。"""

from __future__ import annotations

import json
import threading
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import Any


# 当前协程/任务中的活跃 Span
_current_span: ContextVar[Span | None] = ContextVar(
    "tiny_claw_trace_span",
    default=None,
)


class Span:
    """链路追踪中的一个时间跨度与操作节点。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.start_time: datetime = datetime.now()
        self.end_time: datetime | None = None
        self.duration_ms: int = 0
        # 元数据（如 Token 消耗、执行的命令）
        self.attributes: dict[str, Any] = {}
        # 子跨度
        self.children: list[Span] = []
        self._mu = threading.Lock()
        # start_span 写入 ContextVar 时的 token，end_span 时复位父级
        self._ctx_token: Token[Span | None] | None = None

    def end_span(self) -> None:
        """结束跨度，计算耗时，并将 ContextVar 恢复为父 Span。"""
        self.end_time = datetime.now()
        self.duration_ms = int(
            (self.end_time - self.start_time).total_seconds() * 1000
        )
        if self._ctx_token is not None:
            _current_span.reset(self._ctx_token)
            self._ctx_token = None

    def add_attribute(self, key: str, value: Any) -> None:
        """为当前 Span 记录关键元数据。"""
        with self._mu:
            self.attributes[key] = value

    def _append_child(self, child: Span) -> None:
        """线程安全地挂载子 Span。"""
        with self._mu:
            self.children.append(child)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的字典（不含锁 / ContextVar token）。"""
        with self._mu:
            children_snapshot = list(self.children)
            attrs_snapshot = dict(self.attributes)

        payload: dict[str, Any] = {
            "name": self.name,
            "start_time": self.start_time.isoformat(timespec="milliseconds"),
            "end_time": (
                self.end_time.isoformat(timespec="milliseconds")
                if self.end_time is not None
                else None
            ),
            "duration_ms": self.duration_ms,
        }
        if attrs_snapshot:
            payload["attributes"] = attrs_snapshot
        if children_snapshot:
            payload["children"] = [c.to_dict() for c in children_snapshot]
        return payload


def start_span(name: str) -> Span:
    """开启新 Span，挂到当前父 Span 下，并写入 ContextVar（级联上下文）。

    用 ContextVar 代替显式传 ctx；调用方在子调用中 ``get_current_span()``
    或继续 ``start_span`` 即可自动绑定父子关系。结束后务必 ``span.end_span()``。
    """
    span = Span(name)

    parent = _current_span.get()
    if parent is not None:
        parent._append_child(span)

    # 将当前 Span 设为最新父节点
    span._ctx_token = _current_span.set(span)
    return span


def get_current_span() -> Span | None:
    """读取当前 ContextVar 中的 Span（无则返回 None）。"""
    return _current_span.get()


def export_trace_to_file(
    root_span: Span,
    work_dir: str,
    session_id: str,
) -> Path:
    """根 Span 结束后，序列化并写入 ``{work_dir}/.claw/traces/``。"""
    trace_dir = Path(work_dir) / ".claw" / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)

    filename = trace_dir / f"trace_{session_id}_{int(datetime.now().timestamp())}.json"
    data = json.dumps(
        root_span.to_dict(),
        ensure_ascii=False,
        indent=2,
    )
    filename.write_text(data, encoding="utf-8")
    return filename
