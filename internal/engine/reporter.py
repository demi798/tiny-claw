"""Agent 引擎向外界输出信息的统一契约。

使得引擎可以无缝切换终端 (CLI)、飞书、钉钉甚至 WebUI 等不同展现层。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Reporter(ABC):
    """引擎事件的对外输出规范。"""

    @abstractmethod
    async def on_thinking(self) -> None:
        """模型开始慢思考 (Reasoning) 时调用。"""
        ...

    @abstractmethod
    async def on_tool_call(self, tool_name: str, args: str) -> None:
        """模型决定调用工具时调用。"""
        ...

    @abstractmethod
    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        *,
        is_error: bool,
    ) -> None:
        """工具执行完毕并返回结果时调用。"""
        ...

    @abstractmethod
    async def on_message(self, content: str) -> None:
        """模型宣告任务完成，向用户输出最终纯文本回答时调用。"""
        ...
