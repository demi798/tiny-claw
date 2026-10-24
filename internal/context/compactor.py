"""上下文压缩器：监控并压缩消息体积，降低大模型上下文 OOM 风险。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from typing import Any

from internal.schema.message import Message, Role

logger = logging.getLogger(__name__)

# 短期记忆区内单条工具结果的最大保留长度（头尾各一半）
_WORKING_MEMORY_TOOL_KEEP = 1000
_WORKING_MEMORY_TOOL_HEAD = 500
_WORKING_MEMORY_TOOL_TAIL = 500


@dataclass(slots=True)
class Compactor:
    """负责监控和压缩上下文内存，防止大模型发生 OOM。"""

    max_chars: int  # 触发压缩的最大字符数阈值（水位线）
    retain_last_msgs: int  # Working Memory 保护区：最近的 N 条消息

    def compact(self, msgs: list[Message]) -> list[Message]:
        """若总长度超标，对远期历史全量掩码，对短期保护区做超长局部截断。

        未超水位线时原样返回（常见正常路径）。
        返回的消息为拷贝，不会污染调用方持有的原始 Message。
        """
        current_length = self.estimate_length(msgs)
        if current_length < self.max_chars:
            return msgs

        logger.warning(
            "[Compactor] ⚠️ 内存告警：当前上下文长度 (%d 字符) 超过阈值 (%d)，触发压缩清理...",
            current_length,
            self.max_chars,
        )

        msg_count = len(msgs)
        protect_start_index = max(0, msg_count - self.retain_last_msgs)

        compacted: list[Message] = []
        for i, msg in enumerate(msgs):
            # 1. 系统提示词绝对不能动
            if msg.role == Role.SYSTEM:
                compacted.append(msg)
                continue

            # 拷贝后再改 content，避免并发下污染底层 Session 历史
            new_msg = msg
            is_in_working_memory = i >= protect_start_index

            # 【核心驾驭逻辑】双重降级防线
            if msg.role == Role.USER and msg.tool_call_id:
                # 工具返回结果 (Observation / ToolResult)
                if not is_in_working_memory:
                    # 第一道防线：远期历史 → 全量掩码
                    if len(msg.content) > 200:
                        new_msg = replace(
                            msg,
                            content=(
                                f"...[为了节省内存，早期的工具输出已被系统强制清理。"
                                f"原始长度: {len(msg.content)} 字节]..."
                            ),
                        )
                else:
                    # 第二道防线：短期记忆 → 头尾截断防 OOM
                    if len(msg.content) > _WORKING_MEMORY_TOOL_KEEP:
                        head = msg.content[:_WORKING_MEMORY_TOOL_HEAD]
                        tail = msg.content[-_WORKING_MEMORY_TOOL_TAIL:]
                        omitted = len(msg.content) - _WORKING_MEMORY_TOOL_KEEP
                        new_msg = replace(
                            msg,
                            content=(
                                f"{head}\n\n"
                                f"...[内容过长，中间 {omitted} 字节已被系统截断]...\n\n"
                                f"{tail}"
                            ),
                        )
            elif msg.role == Role.ASSISTANT and msg.content:
                # 大模型冗长推理（Thinking Trace）
                if not is_in_working_memory and len(msg.content) > 200:
                    new_msg = replace(
                        msg,
                        content="...[早期的推理思考过程已折叠]...",
                    )

            # 绝不改动 tool_calls：那是模型行动证据，维系逻辑链的关键
            compacted.append(new_msg)

        new_length = self.estimate_length(compacted)
        logger.info(
            "[Compactor] ✅ 压缩完成。上下文长度从 %d 降至 %d 字符。",
            current_length,
            new_length,
        )
        return compacted

    def estimate_length(self, msgs: list[Message]) -> int:
        """粗略计算当前上下文的总字符长度。"""
        length = 0
        for msg in msgs:
            length += len(msg.content)
            for tc in msg.tool_calls:
                length += len(tc.name) + _arguments_len(tc.arguments)
        return length


def _arguments_len(arguments: Any) -> int:
    """估算工具参数占用的字符长度。"""
    if isinstance(arguments, (bytes, bytearray)):
        return len(arguments)
    if isinstance(arguments, str):
        return len(arguments)
    try:
        return len(json.dumps(arguments, ensure_ascii=False))
    except TypeError:
        return len(str(arguments))
