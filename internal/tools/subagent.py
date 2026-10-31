"""子智能体工具：主 Agent 可派出只读探索型 Subagent，并回收精炼摘要。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from internal.schema import ToolDefinition
from internal.tools.registry import BaseTool, Registry

logger = logging.getLogger(__name__)


class AgentRunner(Protocol):
    """打破循环依赖的抽象接口。

    SubagentTool 在 tools 包，完整 AgentEngine 在 engine 包。
    通过本接口由外部注入「拉起子引擎」的能力。
    """

    async def run_sub(
        self,
        task_prompt: str,
        read_only_registry: Registry,
        reporter: Any | None = None,
    ) -> str:
        """启动一次性匿名子智能体任务，返回其最终纯文本总结。"""
        ...


@dataclass(slots=True)
class _SubagentArgs:
    """内部反序列化结构。"""

    task_prompt: str


class SubagentTool(BaseTool):
    """派出深度探索（Exploration）子智能体的工具。"""

    def __init__(
        self,
        runner: AgentRunner,
        read_only_registry: Registry,
        reporter: Any | None = None,
    ) -> None:
        self._runner = runner
        # 子智能体专属、受限的只读注册表
        self._read_only_registry = read_only_registry
        # 暂用 Any 规避包循环依赖，底层按需断言为 Reporter
        self._reporter = reporter

    def name(self) -> str:
        return "spawn_subagent"

    def definition(self) -> ToolDefinition:
        """向主 Agent 暴露本工具能力。"""
        return ToolDefinition(
            name=self.name(),
            description=(
                "派出一个专门用于深度探索（Exploration）的子智能体。"
                "当你需要阅读大量代码、跨文件查找逻辑时请调用此工具。"
                "它在探索完毕后，会给你返回一份极度精炼的摘要报告。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_prompt": {
                        "type": "string",
                        "description": "给子智能体下达的明确指令。",
                    },
                },
                "required": ["task_prompt"],
            },
        )

    async def execute(self, args: bytes) -> str:
        """解析 task_prompt，拉起只读子智能体并返回探索报告。

        子循环物理隔离：仅注入 read_only_registry，子智能体只能读、不能搞破坏。
        子智能体失败时返回错误文案，不中断主循环协议。
        """
        try:
            payload = json.loads(args.decode("utf-8"))
            input_args = _SubagentArgs(task_prompt=str(payload["task_prompt"]))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as err:
            raise ValueError(f"解析参数失败: {err}") from err

        if not input_args.task_prompt.strip():
            raise ValueError("解析参数失败: task_prompt 不能为空")

        logger.info(
            "[Subagent] 🚀 主 Agent 发起委派！正在拉起探路者: [%s]...",
            input_args.task_prompt,
        )

        # 【核心降维打击】：拉起完全物理隔离的子循环，仅提供只读注册表
        try:
            summary = await self._runner.run_sub(
                input_args.task_prompt,
                self._read_only_registry,
                self._reporter,
            )
        except Exception as err:
            # 把失败写成 Observation 文本返回，而不是抛给 Registry
            return f"子智能体执行失败: {err}"

        logger.info("[Subagent] ✅ 子智能体任务结束。报告返回给主干...")

        # 几万字探索化作轻量 Summary，像一次普通 API 调用交还主 Agent
        return f"【子智能体探索报告】:\n{summary}"


def new_subagent_tool(
    runner: AgentRunner,
    read_only_registry: Registry,
    reporter: Any | None = None,
) -> SubagentTool:
    """构造 SubagentTool。

    已废弃：请直接使用 ``SubagentTool(...)``。
    """
    import warnings

    warnings.warn(
        "new_subagent_tool() 已废弃，请改用 SubagentTool(...)",
        DeprecationWarning,
        stacklevel=2,
    )
    return SubagentTool(runner, read_only_registry, reporter)
