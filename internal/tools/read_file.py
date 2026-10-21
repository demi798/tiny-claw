"""读取本地文件内容的工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from internal.schema import ToolDefinition
from internal.tools.registry import BaseTool

# 防止超大文件撑爆模型上下文
_MAX_CONTENT_BYTES = 8000

@dataclass(slots=True)
class _ReadFileArgs:
    """内部反序列化结构。"""

    path: str

class ReadFileTool(BaseTool):
    """读取工作区内指定文件的内容。

    work_dir 由引擎注入，限制工具只能在该目录及其子目录下操作。
    """

    def __init__(self, work_dir: str) -> None:
        self._work_dir = Path(work_dir).resolve()

    def name(self) -> str:
        return "read_file"

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name(),
            description="读取指定路径的文件内容。请提供相对工作区的路径。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径，如 cmd/claw/main.py",
                    },
                },
                "required": ["path"],
            },
        )

    async def execute(self, args: bytes) -> str:
        # 1. 延迟解析：把大模型给出的 JSON 转成强类型参数
        try:
            payload = json.loads(args.decode("utf-8"))
            input_args = _ReadFileArgs(path=str(payload["path"]))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as err:
            # 抛出异常后由 Registry 捕获并回传给模型
            raise ValueError(f"参数解析失败: {err}") from err

        if not input_args.path:
            raise ValueError("参数解析失败: path 不能为空")

        # 2. 拼接绝对路径
        # 注意：生产环境应做路径穿越检测，防止 ../../etc/passwd
        full_path = (self._work_dir / input_args.path).resolve()

        # 3. 物理 IO
        try:
            content = full_path.read_bytes()
        except OSError as err:
            raise OSError(f"打开或读取文件失败: {err}") from err

        # 4. 长度截断保护，避免超大文件撑爆 Context
        if len(content) > _MAX_CONTENT_BYTES:
            head = content[:_MAX_CONTENT_BYTES].decode("utf-8", errors="replace")
            return (
                f"{head}\n\n"
                f"...[由于内容过长，已被系统截断至前 {_MAX_CONTENT_BYTES} 字节]..."
            )

        return content.decode("utf-8", errors="replace")
