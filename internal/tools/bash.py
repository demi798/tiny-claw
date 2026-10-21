"""在工作区内执行 bash 命令的工具。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from internal.schema import ToolDefinition
from internal.tools.registry import BaseTool

_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 8000


@dataclass(slots=True)
class _BashArgs:
    """内部反序列化结构。"""

    command: str


class BashTool(BaseTool):
    """在指定工作区执行 bash 命令（支持管道、&& 等 Shell 语法）。"""

    def __init__(self, work_dir: str) -> None:
        self._work_dir = Path(work_dir).resolve()

    def name(self) -> str:
        return "bash"

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name(),
            description=(
                "在当前工作区执行任意的 bash 命令。支持链式命令(如 &&)。"
                "返回标准输出(stdout)和标准错误(stderr)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 bash 命令，例如: ls -la 或 pytest ./...",
                    },
                },
                "required": ["command"],
            },
        )

    async def execute(self, args: bytes) -> str:
        # 1. 延迟解析 JSON
        try:
            payload = json.loads(args.decode("utf-8"))
            input_args = _BashArgs(command=str(payload["command"]))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as err:
            raise ValueError(f"参数解析失败: {err}") from err

        if not input_args.command.strip():
            raise ValueError("参数解析失败: command 不能为空")

        # 2. 时间预算 + 绑定工作区；用 bash -c 支持复杂 Shell 语法
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            input_args.command,
            cwd=str(self._work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        timed_out = False
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout, _ = await proc.communicate()

        output = (stdout or b"").decode("utf-8", errors="replace")

        # 3. 超时：把已有输出 + 警告回传给模型（不抛异常）
        if timed_out:
            return (
                f"{output}\n"
                f"[警告: 命令执行超时({int(_TIMEOUT_SECONDS)}s)，已被系统强制终止。"
                f"如果是启动常驻服务，请尝试将其转入后台。]"
            )

        # 4. 命令失败：错误原样拼进字符串返回，交给模型自纠错（不抛异常）
        if proc.returncode not in (0, None):
            return f"执行报错: exit code {proc.returncode}\n输出:\n{output}"

        if not output:
            return "命令执行成功，无终端输出。"

        # 5. 长度截断，防止撑爆 Context
        encoded = output.encode("utf-8")
        if len(encoded) > _MAX_OUTPUT_BYTES:
            head = encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            return (
                f"{head}\n\n"
                f"...[终端输出过长，已截断至前 {_MAX_OUTPUT_BYTES} 字节]..."
            )

        return output
