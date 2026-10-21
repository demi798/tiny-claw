# 接口定义
from __future__ import annotations
from abc import ABC, abstractmethod
from internal.schema import Message, ToolDefinition

class LLMProvider(ABC):
    """与大模型通信的统一契约。"""

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        available_tools: list[ToolDefinition] | None,
    ) -> Message | None:
        """根据对话历史发起一次推理。

        available_tools 为 None 或空列表时表示不挂载工具（慢思考阶段）。
        失败时抛出异常；无有效回复时可返回 None。
        """
        raise NotImplementedError
