"""会话与会话管理：维护多轮人机交互的历史与短期工作记忆。"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import datetime

from internal.schema.message import Message, Role


@dataclass
class Session:
    """一次持续的人机交互过程，负责维护该会话的完整历史。"""

    id: str
    work_dir: str  # 该会话绑定的物理工作区
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    _history: list[Message] = field(default_factory=list, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def append(self, *msgs: Message) -> None:
        """线程安全地向 Session 中追加消息。"""
        with self._lock:
            self._history.extend(msgs)
            self.updated_at = datetime.now()

            # 【持久化预留点】：工业级实现中可在此将 history 以 JSONL
            # Append 到 work_dir/.claw/sessions/{id}.jsonl。
            # self.save_to_disk()

    def get_working_memory(self, limit: int) -> list[Message]:
        """从后往前截取最近 N 条消息，形成 Agent 的短期工作记忆。

        不全量返回历史；并对「孤儿」工具响应做防线处理，避免截断后
        首条是 ToolResult 却缺少对应 ToolCall，导致大模型 API 400。
        """
        with self._lock:
            total = len(self._history)
            if total <= limit or limit <= 0:
                # 历史总量不超过限制或不设限：全量深拷贝返回
                return copy.deepcopy(self._history)

            # 截取最近的 limit 条
            res = copy.deepcopy(self._history[total - limit :])

            # 【驾驭防线】：若切片首条是带 tool_call_id 的用户消息（工具观察），
            # 说明发起该调用的 ToolCall 已被截断抛弃，必须丢弃孤儿响应。
            while res and res[0].role == Role.USER and res[0].tool_call_id:
                res.pop(0)

            return res


class SessionManager:
    """全局会话管理：用于多用户 / 多终端隔离。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def get_or_create(self, session_id: str, work_dir: str) -> Session:
        """获取或创建一个会话。"""
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
                
            sess = Session(id=session_id, work_dir=work_dir)
            self._sessions[session_id] = sess
            return sess


# 模块级单例
global_session_mgr = SessionManager()
