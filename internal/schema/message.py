"""微型 OS 的通信协议"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

class Role(str, Enum):
    """消息角色，与大模型沟通的基石"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    
@dataclass
class ToolCall:
    """模型请求调用某个具体工具"""
    id: str
    name: str
    arguments: Any
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments
        }
        
@dataclass
class Message:
    """上下文中传递的单条消息"""
    role: Role
    content: str = ""
    
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    
    def to_dict(self) -> dict:
        data = {
            "role": self.role.value,
            "content": self.content
        }
        
        if self.tool_calls:
            data["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]

        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        
        return data
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
    
@dataclass
class ToolResult:
    tool_call_id: str
    output: str
    is_error: bool = False
    
    def to_dict(self):
        return {
            "tool_call_id": self.tool_call_id,
            "output": self.output,
            "is_error": self.is_error
        }
        
@dataclass
class ToolDefinition:
    """描述大模型可调用工具的元信息，供模型理解工具有什么用"""
    name: str
    description: str
    input_schema: Any
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema
        }