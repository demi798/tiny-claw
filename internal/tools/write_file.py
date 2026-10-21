"""创建或覆盖写入本地文件的工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from internal.schema import ToolDefinition
from internal.tools.registry import BaseTool


@dataclass(slots=True)
class _WriteFileArgs:
    """内部反序列化结构。"""

    path: str
    content: str


class WriteFileTool(BaseTool):
    """在工作区内创建或覆盖写入文件。

    work_dir 由引擎注入，限制工具只能在该目录及其子目录下操作。
    """

    def __init__(self, work_dir: str) -> None:
        self._work_dir = Path(work_dir).resolve()

    def name(self) -> str:
        return "write_file"

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name(),
            description=(
                "创建或覆盖写入一个文件。如果目录不存在会自动创建。"
                "请提供相对于工作区的相对路径。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径，如 src/main.py",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        )

    async def execute(self, args: bytes) -> str:
        # 1. 延迟解析 JSON 参数
        try:
            payload = json.loads(args.decode("utf-8"))
            input_args = _WriteFileArgs(
                path=str(payload["path"]),
                content=str(payload["content"]),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as err:
            raise ValueError(f"参数解析失败: {err}") from err

        if not input_args.path:
            raise ValueError("参数解析失败: path 不能为空")

        # 2. 限制在 work_dir 下，防止修改系统级文件
        full_path = (self._work_dir / input_args.path).resolve()

        # 3. 自动创建缺失的父目录，再写入（权限 0o644）
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(input_args.content, encoding="utf-8")
            full_path.chmod(0o644)
        except OSError as err:
            raise OSError(f"创建目录或写入文件失败: {err}") from err

        return f"成功将内容写入到文件: {input_args.path}"
