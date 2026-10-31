"""会话与会话管理：维护多轮人机交互的历史与短期工作记忆。"""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime

from internal.schema.message import Message, Role

logger = logging.getLogger(__name__)

# 窗口不以普通 User 开头时插入的占位提示（防止智谱 1214）
_SYNTHETIC_USER_CONTINUE = (
    "（系统提示：早期对话上下文已压缩。"
    "请根据近期工具执行结果，继续完成用户任务。）"
)


def _is_plain_user(msg: Message) -> bool:
    """普通用户指令（非工具观察结果）。"""
    return msg.role == Role.USER and not msg.tool_call_id


def _is_tool_result(msg: Message) -> bool:
    """工具观察：内部用 Role.USER + tool_call_id 表示。"""
    return msg.role == Role.USER and bool(msg.tool_call_id)


def _pack_turns(history: list[Message]) -> list[list[Message]]:
    """将扁平历史打包为完整回合，避免按裸条数切开 tool 调用链。

    回合形态：
    - 单条普通 User；
    - 单条纯文本 Assistant；
    - Assistant(tool_calls) + 紧随且 id 匹配的全部 ToolResult。
    无法配对的孤儿 ToolResult 单独丢弃（不进入任何回合）。
    """
    turns: list[list[Message]] = []
    i = 0
    n = len(history)

    while i < n:
        msg = history[i]

        if _is_tool_result(msg):
            # 孤儿工具结果：前面没有对应的 Assistant.tool_calls
            logger.warning(
                "[Session] 打包回合时丢弃孤儿 ToolResult tool_call_id=%s",
                msg.tool_call_id,
            )
            i += 1
            continue

        if _is_plain_user(msg):
            turns.append([msg])
            i += 1
            continue

        if msg.role == Role.ASSISTANT:
            group = [msg]
            i += 1
            if msg.tool_calls:
                expected = {tc.id for tc in msg.tool_calls if tc.id}
                matched: set[str] = set()
                while i < n and _is_tool_result(history[i]):
                    tid = history[i].tool_call_id or ""
                    if tid in expected and tid not in matched:
                        group.append(history[i])
                        matched.add(tid)
                        i += 1
                        # 该 Assistant 所需结果已齐，结束本组
                        if matched == expected:
                            break
                    else:
                        # 不属于当前 Assistant 的 tool，留给后续逻辑处理
                        break
            turns.append(group)
            continue

        # 其它角色（极少出现在 history）：单独成组，避免丢失
        turns.append([msg])
        i += 1

    return turns


def _flatten_turns(turns: list[list[Message]]) -> list[Message]:
    flat: list[Message] = []
    for turn in turns:
        flat.extend(turn)
    return flat


@dataclass
class Session:
    """一次持续的人机交互过程，负责维护该会话的完整历史。"""

    id: str
    work_dir: str  # 该会话绑定的物理工作区
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    # 【新增】该 Session 累计消耗的资源（供外部 Tracker 累加账单）
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_cny: float = 0.0
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

    def record_usage(self, prompt: int, completion: int, cost: float) -> None:
        """累加本会话的 Token 与费用账单（供外部 Tracker 调用）。"""
        with self._lock:
            self.total_prompt_tokens += prompt
            self.total_completion_tokens += completion
            self.total_cost_cny += cost

    def clear(self) -> None:
        """清空会话历史（演示入口避免旧拒绝话术污染）。"""
        with self._lock:
            self._history.clear()
            self.updated_at = datetime.now()

    def get_working_memory(self, limit: int) -> list[Message]:
        """截取短期工作记忆，供本轮 API 调用。

        规则（防止智谱 1214）：
        1. 按「完整回合」从尾部裁剪，不按裸条数切开 tool 链；
        2. 窗口首条必须是普通 User；若裁剪后不是，尽量再向前吞回合，
           仍不行则插入合成 User 占位；
        3. 返回深拷贝，不污染 Session 全量历史。
        """
        with self._lock:
            history = copy.deepcopy(self._history)
            total = len(history)
            if total == 0:
                return []
            if limit <= 0 or total <= limit:
                return _ensure_plain_user_prefix(history)

            turns = _pack_turns(history)
            if not turns:
                return []

            # 从最新回合往前累加，直到达到条数上限
            kept_rev: list[list[Message]] = []
            msg_count = 0
            for turn in reversed(turns):
                turn_len = len(turn)
                if kept_rev and msg_count + turn_len > limit:
                    break
                kept_rev.append(turn)
                msg_count += turn_len

            kept = list(reversed(kept_rev))

            # 若不以普通 User 开头：仅尝试再吞「紧邻的上一条普通 User 回合」
            # （允许略超 limit）；更早的历史不再回溯，避免把窗口撑回全量。
            # 仍缺 User 时由 _ensure_plain_user_prefix 插入合成占位。
            if kept and not _is_plain_user(kept[0][0]):
                oldest_idx = len(turns) - len(kept)
                if oldest_idx > 0:
                    prev = turns[oldest_idx - 1]
                    if len(prev) == 1 and _is_plain_user(prev[0]):
                        kept.insert(0, prev)
                        logger.info(
                            "[Session] 工作记忆补齐紧邻 User，窗口约 %d 条",
                            sum(len(t) for t in kept),
                        )

            res = _flatten_turns(kept)
            res = _ensure_plain_user_prefix(res)

            if len(res) < total:
                logger.info(
                    "[Session] 工作记忆裁剪：全量 %d 条 → 窗口 %d 条（limit=%d）",
                    total,
                    len(res),
                    limit,
                )
            return res


def _ensure_plain_user_prefix(msgs: list[Message]) -> list[Message]:
    """保证列表以普通 User 开头；必要时插入合成 User。"""
    if not msgs:
        return msgs

    # 去掉开头的孤儿 ToolResult（防御）
    while msgs and _is_tool_result(msgs[0]):
        logger.warning(
            "[Session] 丢弃窗口首部孤儿 ToolResult tool_call_id=%s",
            msgs[0].tool_call_id,
        )
        msgs = msgs[1:]

    if not msgs:
        return msgs

    if _is_plain_user(msgs[0]):
        return msgs

    logger.warning(
        "[Session] 工作记忆不以 User 开头（首条 role=%s），插入合成 User 以防 1214",
        msgs[0].role,
    )
    return [
        Message(role=Role.USER, content=_SYNTHETIC_USER_CONTINUE),
        *msgs,
    ]


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


def create_session_manager() -> SessionManager:
    """显式工厂：入口应调用本函数并注入，避免测试与多入口互相串扰。"""
    return SessionManager()


# 兼容旧调用的默认实例；新代码请改用 create_session_manager() 并注入
global_session_mgr = SessionManager()
