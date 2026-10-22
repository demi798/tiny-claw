"""
从本地 SKILL.md 加载并解析 Agent 技能模板。
遍历 .claw/skills/ 目录，寻找各个子目录下的 SKILL.md，并解析其 YAML 前言（Frontmatter）
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(slots=True)
class Skill:
    """从 SKILL.md 中解析出的标准化技能结构。"""

    name: str
    description: str
    body: str  # Markdown 正文指令


class SkillLoader:
    """负责从本地文件系统中加载并解析符合规范的技能模板。"""

    def __init__(self, work_dir: str) -> None:
        self._work_dir = Path(work_dir)

    def load_all(self) -> str:
        """扫描 .claw/skills，解析所有 SKILL.md，格式化为可注入 Context 的字符串。

        目录不存在时静默返回空字符串。
        """
        skill_base_dir = self._work_dir / ".claw" / "skills"
        if not skill_base_dir.is_dir():
            return ""

        parts: list[str] = [
            "\n### 可用专业技能 (Agent Skills)\n",
            "以下是你拥有的标准化外挂技能，请在符合 description 描述的场景下"
            "严格遵循其正文指令：\n\n",
        ]

        try:
            # 递归查找名为 SKILL.md 的文件
            for skill_path in sorted(skill_base_dir.rglob("SKILL.md")):
                if not skill_path.is_file():
                    continue
                try:
                    content = skill_path.read_text(encoding="utf-8")
                except OSError:
                    continue

                skill = parse_skill_md(content)
                parts.append(f"#### 技能名称: {skill.name}\n")
                parts.append(f"**触发条件**: {skill.description}\n\n")
                parts.append("**执行指南**:\n")
                parts.append(skill.body)
                parts.append("\n\n---\n")
        except OSError:
            return ""

        result = "".join(parts)
        # 仅有标题、未解析到任何技能时视为无效，静默返回
        if len(result) < 100:
            return ""
        return result


def parse_skill_md(content: str) -> Skill:
    """极简解析带有 YAML Frontmatter 的 Markdown 内容。"""
    skill = Skill(
        name="Unknown Skill",
        description="No description provided.",
        body=content,  # 默认将全量内容作为 body
    )

    # Frontmatter 以 --- 包裹
    if not (content.startswith("---\n") or content.startswith("---\r\n")):
        return skill

    parts = content.split("---", 2)
    if len(parts) != 3:
        return skill

    frontmatter = parts[1]
    skill.body = parts[2].strip()

    for line in frontmatter.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            skill.name = line.removeprefix("name:").strip()
        elif line.startswith("description:"):
            skill.description = line.removeprefix("description:").strip()

    return skill
