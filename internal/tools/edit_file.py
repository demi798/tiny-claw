"""对现有文件做局部字符串替换的工具（含四级模糊匹配）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from internal.schema import ToolDefinition
from internal.tools.registry import BaseTool


@dataclass(slots=True)
class _EditFileArgs:
    """内部反序列化结构。"""

    path: str
    old_text: str
    new_text: str


class EditFileTool(BaseTool):
    """在工作区内对文件做局部替换，比重写整文件更安全。"""

    def __init__(self, work_dir: str) -> None:
        self._work_dir = Path(work_dir).resolve()

    def name(self) -> str:
        return "edit_file"

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name(),
            description=(
                "对现有文件进行局部的字符串替换。这比重写整个文件更安全、更快速。"
                "请提供足够的 old_text 上下文以确保匹配的唯一性。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要修改的文件路径",
                    },
                    "old_text": {
                        "type": "string",
                        "description": (
                            "文件中原有的文本。必须包含足够的上下文"
                            "（建议上下各多包含几行），以确保在文件中的唯一性。"
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": "要替换成的新文本",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        )

    async def execute(self, args: bytes) -> str:
        try:
            payload = json.loads(args.decode("utf-8"))
            input_args = _EditFileArgs(
                path=str(payload["path"]),
                old_text=str(payload["old_text"]),
                new_text=str(payload["new_text"]),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as err:
            raise ValueError(f"参数解析失败: {err}") from err

        if not input_args.path:
            raise ValueError("参数解析失败: path 不能为空")

        full_path = (self._work_dir / input_args.path).resolve()

        # 1. 读取原文件
        try:
            original_content = full_path.read_text(encoding="utf-8")
        except OSError as err:
            raise OSError(f"读取文件失败，请确认路径是否正确: {err}") from err

        # 2. 四级模糊替换；具体失败原因原样抛出，供模型自纠
        new_content = fuzzy_replace(
            original_content,
            input_args.old_text,
            input_args.new_text,
        )

        # 3. 写回磁盘
        try:
            full_path.write_text(new_content, encoding="utf-8")
            full_path.chmod(0o644)
        except OSError as err:
            raise OSError(f"写回文件失败: {err}") from err

        return f"✅ 成功修改文件: {input_args.path}"


def fuzzy_replace(original_content: str, old_text: str, new_text: str) -> str:
    """四级容错降级替换：精确 → 换行归一 → Trim → 逐行去缩进。"""
    # L1: 精确匹配
    count = original_content.count(old_text)
    if count == 1:
        return original_content.replace(old_text, new_text, 1)
    if count > 1:
        raise ValueError(
            f"old_text 匹配到了 {count} 处，请提供更多的上下文代码以确保唯一性"
        )

    # L2: 换行符归一化（\r\n → \n）
    normalized_content = original_content.replace("\r\n", "\n")
    normalized_old = old_text.replace("\r\n", "\n")

    count = normalized_content.count(normalized_old)
    if count == 1:
        return normalized_content.replace(normalized_old, new_text, 1)

    # L3: Trim Space（忽略首尾空行/空格）
    trimmed_old = normalized_old.strip()
    if trimmed_old:
        count = normalized_content.count(trimmed_old)
        if count == 1:
            # L3/L4 触发时，若 new_text 缩进不完整，格式可能不完美，但优于直接失败死循环
            return normalized_content.replace(trimmed_old, new_text, 1)

    # L4: 逐行去缩进滑动窗口匹配
    return line_by_line_replace(normalized_content, normalized_old, new_text)


def line_by_line_replace(content: str, old_text: str, new_text: str) -> str:
    """按行切割，去除首尾空白后做滑动窗口匹配。"""
    content_lines = content.split("\n")
    old_lines = [line.strip() for line in old_text.strip().split("\n")]

    if not old_lines or len(content_lines) < len(old_lines):
        raise ValueError("找不到该代码片段")

    match_count = 0
    match_start = -1
    match_end = -1

    window = len(old_lines)
    for i in range(len(content_lines) - window + 1):
        if all(
            content_lines[i + j].strip() == old_lines[j] for j in range(window)
        ):
            match_count += 1
            match_start = i
            match_end = i + window

    if match_count == 0:
        raise ValueError(
            "在文件中未找到 old_text，请大模型先调用 read_file 仔细确认文件内容和缩进"
        )
    if match_count > 1:
        raise ValueError(
            f"模糊匹配到了 {match_count} 处相似代码，请提供更多上下行代码以精确定位"
        )

    # 将匹配到的原始行范围整体替换为 new_text
    new_lines = [
        *content_lines[:match_start],
        new_text,
        *content_lines[match_end:],
    ]
    return "\n".join(new_lines)
