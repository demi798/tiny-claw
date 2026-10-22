"""
Prompt Composer (组装器)：
根据工作区环境动态组装 System Prompt。
像搭积木一样，把基础内核身份、AGENTS.md 和刚才解析出的 Skills 动态拼接成最终的系统级提示词。
"""

from __future__ import annotations

from pathlib import Path

from internal.context.skill import SkillLoader
from internal.schema.message import Message, Role

# 极简内核：确立基本身份与底线纪律
_MINIMAL_CORE = """# 核心身份
你名叫 tiny-claw，一个由驾驭工程驱动的骨灰级研发助手。
你具备极简主义哲学，拒绝废话。你能通过系统提供的内置工具，创建、读取、修改和执行工作区中的代码。

# 核心纪律 (CRITICAL)
1. 如需检查文件是否存在，请使用 bash 的 ls 或 test -f，而不是对目录使用 read_file。
2. 创建新文件时，务必使用 write_file，并同时提供 path 和 content 参数。
3. 编辑文件前务必先读取现有文件，以理解上下文。
4. 无论何时你需要写代码或创建文件，都要直接使用 write_file 工具。
5. 遇到工具执行报错时，仔细阅读 stderr，尝试自己修正命令并重试。
6. 始终用中文回复，以便传达你的进展和想法。
"""


class PromptComposer:
    """负责根据工作区环境动态生成 System Prompt。"""

    def __init__(self, work_dir: str) -> None:
        self._work_dir = Path(work_dir)
        self._skill_loader = SkillLoader(work_dir)

    def build(self) -> Message:
        """组装并返回一条完整的 Role.SYSTEM 消息。"""
        parts: list[str] = [_MINIMAL_CORE]

        # 外部化状态：加载项目专属规范 (AGENTS.md)
        agents_md_path = self._work_dir / "AGENTS.md"
        try:
            agents_content = agents_md_path.read_text(encoding="utf-8")
        except OSError:
            agents_content = None

        if agents_content is not None:
            parts.append("\n# 项目专属指南 (来自 AGENTS.md)\n")
            parts.append(
                "以下是当前工作区特有的架构规范与注意事项，"
                "你的行为必须绝对符合以下要求：\n"
            )
            parts.append("```markdown\n")
            parts.append(agents_content)
            parts.append("\n```\n")

        # 动态加载技能外挂 (Skills)
        skills_content = self._skill_loader.load_all()
        if skills_content:
            parts.append(skills_content)

        return Message(role=Role.SYSTEM, content="".join(parts))
