"""对外导出 schema 数据模型，便于 `from internal.schema import ...`。"""

from internal.schema.message import (
    Message,
    Role,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

__all__ = [
    "Message",
    "Role",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
]
