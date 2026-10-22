"""Agent 引擎：微型 OS 的核心驱动（ReAct 主循环）。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from internal.engine.reporter import Reporter
from internal.provider.interface import LLMProvider
from internal.schema.message import Message, Role, ToolCall, ToolDefinition
from internal.tools.registry import Registry

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are tiny-claw, an expert coding assistant. "
    "You have full access to tools in the workspace."
)

# 仅这些工具可安全并行；其余（含 bash / write_file / edit_file）一律串行
_READ_ONLY_TOOLS = frozenset({"read_file"})

# Reporter 展示用截断长度，避免飞书等渠道消息过长被截断
_REPORTER_OUTPUT_LIMIT = 200


class AgentEngine:
    """驱动 Agent 生命周期：可选慢思考，再行动并执行工具。

    work_dir 是工作区物理边界（OpenClaw 风格）。
    """

    def __init__(
        self,
        provider: LLMProvider,
        registry: Registry,
        work_dir: str,
        *,
        enable_thinking: bool = False,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self.work_dir = work_dir
        self.enable_thinking = enable_thinking

    async def run(
        self,
        user_prompt: str,
        reporter: Reporter | None = None,
    ) -> None:
        """运行 ReAct 循环，直到模型不再请求工具。失败时向上抛出异常。"""
        logger.info(
            "引擎启动 work_dir=%s enable_thinking=%s",
            self.work_dir,
            self.enable_thinking,
        )

        history = [
            Message(role=Role.SYSTEM, content=_SYSTEM_PROMPT),
            Message(role=Role.USER, content=user_prompt),
        ]

        turn = 0
        while True:
            turn += 1
            logger.info("Turn %d 开始", turn)

            tools = self._registry.get_available_tools()

            # Phase 1：不传工具，强制纯文本规划
            if self.enable_thinking:
                await self._run_thinking(history, reporter)

            # Phase 2：恢复工具，按规划行动
            action = await self._run_action(history, tools, reporter)
            if not action.tool_calls:
                logger.info("模型未请求工具，任务完成")
                break

            await self._run_observations(history, action.tool_calls, reporter)

    async def _run_thinking(
        self,
        history: list[Message],
        reporter: Reporter | None,
    ) -> None:
        """慢思考：available_tools=None，迫使模型只输出规划文本。"""
        logger.info("Phase 1 慢思考（不挂载工具）")
        if reporter is not None:
            # 【触发 Reporter】: 开始慢思考
            await reporter.on_thinking()

        think = await self._generate(history, tools=None, stage="Thinking")
        if think.content:
            history.append(think)

    async def _run_action(
        self,
        history: list[Message],
        tools: list[ToolDefinition],
        reporter: Reporter | None,
    ) -> Message:
        """行动：挂载工具定义，获取回复或工具调用。"""
        logger.info("Phase 2 行动（恢复工具）")
        action = await self._generate(history, tools=tools, stage="Action")
        history.append(action)

        if action.content and reporter is not None:
            # 【触发 Reporter】: 输出阶段性总结或最终回复
            await reporter.on_message(action.content)
        return action

    async def _run_observations(
        self,
        history: list[Message],
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> None:
        """执行工具调用，把观察结果写回上下文（必须带 tool_call_id）。

        本批全是只读工具时并行；一旦包含写操作（或 bash）则整批串行。
        """
        can_parallel = all(call.name in _READ_ONLY_TOOLS for call in tool_calls)
        if can_parallel:
            logger.info("模型请求并发调用 %d 个只读工具", len(tool_calls))
            observation_msgs = await self._execute_tools_parallel(tool_calls, reporter)
        else:
            logger.info("模型请求串行调用 %d 个工具（含写操作）", len(tool_calls))
            observation_msgs = await self._execute_tools_sequential(tool_calls, reporter)

        logger.info("工具执行完毕，开始聚合观察结果")
        history.extend(observation_msgs)

    async def _execute_tools_parallel(
        self,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> list[Message]:
        """并行执行工具，结果按原始 tool_calls 顺序返回（预分配切片思路）。"""
        # 预分配，各任务只写自己的下标，无需加锁
        observation_msgs: list[Message | None] = [None] * len(tool_calls)

        async def _run_one(idx: int, call: ToolCall) -> None:
            observation_msgs[idx] = await self._execute_one_tool(idx, call, reporter)

        await asyncio.gather(
            *(_run_one(i, call) for i, call in enumerate(tool_calls))
        )
        # gather 完成后所有槽位应已按原序填满
        if any(msg is None for msg in observation_msgs):
            raise RuntimeError("并行工具执行后观察结果不完整")
        return [msg for msg in observation_msgs if msg is not None]

    async def _execute_tools_sequential(
        self,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> list[Message]:
        """串行执行工具（含写操作时走此路径，保留原有顺序语义）。"""
        observation_msgs: list[Message] = []
        for idx, call in enumerate(tool_calls):
            observation_msgs.append(await self._execute_one_tool(idx, call, reporter))
        return observation_msgs

    async def _execute_one_tool(
        self,
        idx: int,
        call: ToolCall,
        reporter: Reporter | None,
    ) -> Message:
        """执行单个工具，并向 Reporter 汇报调用与结果（展示侧可截断）。"""
        args_text = _format_arguments(call.arguments)
        logger.info("  -> [Task-%d] 执行工具: %s args=%s", idx, call.name, args_text)

        if reporter is not None:
            # 【触发 Reporter】: 报告即将在底层执行的工具
            await reporter.on_tool_call(call.name, args_text)

        result = await self._registry.execute(call)

        if result.is_error:
            logger.warning("  -> [Task-%d] 工具失败 %s: %s", idx, call.name, result.output)
        else:
            logger.info(
                "  -> [Task-%d] 工具成功 %s，返回 %d 字节",
                idx,
                call.name,
                len(result.output),
            )

        if reporter is not None:
            # 传给大模型的 observation 仍是完整数据；人类看到的 Reporter 是缩略版
            await reporter.on_tool_result(
                call.name,
                _truncate_for_reporter(result.output),
                is_error=result.is_error,
            )

        return Message(
            role=Role.USER,
            content=result.output,
            tool_call_id=call.id,
        )

    async def _generate(
        self,
        history: list[Message],
        *,
        tools: list[ToolDefinition] | None,
        stage: str,
    ) -> Message:
        """调用 Provider，并保证返回非空 Message。"""
        try:
            message = await self._provider.generate(history, tools)
        except Exception as err:
            raise RuntimeError(f"{stage} 阶段生成失败") from err

        if message is None:
            raise RuntimeError(f"{stage} 阶段生成失败: 未返回消息")
        return message


def _truncate_for_reporter(output: str) -> str:
    """截断工具输出，供 Reporter 展示（不影响写入上下文的完整结果）。"""
    if len(output) <= _REPORTER_OUTPUT_LIMIT:
        return output
    return output[:_REPORTER_OUTPUT_LIMIT] + "... (已截断)"


def _format_arguments(arguments: Any) -> str:
    """将工具参数格式化为可读字符串。"""
    if isinstance(arguments, (bytes, bytearray)):
        return arguments.decode("utf-8", errors="replace")
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except TypeError:
        return str(arguments)
