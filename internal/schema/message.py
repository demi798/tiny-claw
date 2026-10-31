"""微型 OS 的通信协议"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    """消息角色，与大模型沟通的基石"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Usage:
    """单次大模型 API 调用的 Token 消耗。"""

    prompt_tokens: int = 0  # 输入的 Token 数量
    completion_tokens: int = 0  # 产生的 Token 数量

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class ToolCall:
    """模型请求调用某个具体工具"""

    id: str
    name: str
    arguments: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class Message:
    """上下文中传递的单条消息"""

    role: Role
    content: str = ""

    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    # 若为大模型 (Assistant) 回复，可存放本次调用的 Token 消耗
    usage: Usage | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": self.role.value,
            "content": self.content,
        }

        if self.tool_calls:
            data["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]

        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id

        if self.usage is not None:
            data["usage"] = self.usage.to_dict()

        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ToolResult:
    tool_call_id: str
    output: str
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "output": self.output,
            "is_error": self.is_error,
        }


@dataclass
class ToolDefinition:
    """描述大模型可调用工具的元信息，供模型理解工具有什么用"""

    name: str
    description: str
    input_schema: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
