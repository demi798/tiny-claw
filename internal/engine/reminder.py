"""运行时 Reminder：监控连续失败并打断模型局部执念。"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from internal.schema.message import Message, Role, ToolCall, ToolResult

logger = logging.getLogger(__name__)

# 同一指纹连续失败达到该阈值时，注入强力打断
_FAILURE_THRESHOLD = 3


class ReminderInjector:
    """在运行时监控上下文，并在模型陷入执念时动态注入强力打断信息。"""

    def __init__(self) -> None:
        # 连续失败的工具调用指纹 → 次数（指纹 = ToolName + Arguments 的 Hash）
        self._consecutive_failures: dict[str, int] = {}

    def check_and_inject(
        self,
        last_tool_call: ToolCall,
        last_result: ToolResult,
    ) -> Message | None:
        """分析本轮执行结果，决定是否在 Context 尾部追加 Reminder。

        返回的 Message 作为最新用户输入，强制大模型优先阅读；无需打断时返回 None。
        """
        fingerprint = _generate_fingerprint(
            last_tool_call.name,
            last_tool_call.arguments,
        )

        # 工具执行成功：该路径已走通，清空所有失败计数器
        if not last_result.is_error:
            self._consecutive_failures = {}
            return None

        # 执行失败：累加该特征的失败次数
        self._consecutive_failures[fingerprint] = (
            self._consecutive_failures.get(fingerprint, 0) + 1
        )
        fail_count = self._consecutive_failures[fingerprint]

        logger.info(
            "[Reminder] 监控到工具 %s 执行失败，该参数特征连续失败次数: %d",
            last_tool_call.name,
            fail_count,
        )

        # 【驾驭底线】：触发死循环打断机制！
        # 阈值为 3：连续 3 次在同一处跌倒，必须强行打断局部执念。
        if fail_count >= _FAILURE_THRESHOLD:
            logger.warning("[Reminder] ⚠️ 触发死循环干预！注入强力修正指令。")

            nudge_msg = (
                f"[SYSTEM REMINDER 警告] \n"
                f"你似乎陷入了死循环。你刚刚连续 {fail_count} 次使用相同的参数"
                f"调用了 '{last_tool_call.name}' 工具，并且都失败了。\n"
                f"请立即停止这种无效的重试！你的注意力被当前的报错过度吸引了。\n"
                f"你需要：\n"
                f"1. 停止猜测参数。跳出当前的局部思维。\n"
                f"2. 彻底改变你的策略。\n"
                f"3. 如果你确实无法通过系统工具解决当前问题，请直接结束任务并向用户"
                f"说明你需要什么人工帮助，而不是继续盲目消耗 API 资源尝试。"
            )

            # 【核心】必须是 Role.USER，以保证下一次 API 请求时近因效应权重最高
            return Message(role=Role.USER, content=nudge_msg)

        return None


def new_reminder_injector() -> ReminderInjector:
    """构造 ReminderInjector"""
    return ReminderInjector()


def _generate_fingerprint(tool_name: str, args: Any) -> str:
    """生成工具调用唯一指纹，用于判断大模型是否在重复相同动作。"""
    hasher = hashlib.md5()
    hasher.update(tool_name.encode("utf-8"))
    hasher.update(_args_to_bytes(args))
    return hasher.hexdigest()


def _args_to_bytes(arguments: Any) -> bytes:
    """把 ToolCall.arguments 规范成字节，便于指纹哈希。"""
    if isinstance(arguments, (bytes, bytearray)):
        return bytes(arguments)
    if isinstance(arguments, str):
        return arguments.encode("utf-8")
    if arguments is None:
        return b""
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode("utf-8")
