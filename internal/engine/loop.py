"""Agent 引擎：微型 OS 的核心驱动（ReAct 主循环）。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from internal.context.composer import PromptComposer
from internal.context.compactor import Compactor
from internal.engine.reporter import Reporter
from internal.engine.session import Session
from internal.provider.interface import LLMProvider
from internal.schema.message import Message, Role, ToolCall, ToolDefinition
from internal.tools.registry import Registry

logger = logging.getLogger(__name__)

# 仅这些工具可安全并行；其余（含 bash / write_file / edit_file）一律串行
_READ_ONLY_TOOLS = frozenset({"read_file"})

# Reporter 展示用截断长度，避免飞书等渠道消息过长被截断
_REPORTER_OUTPUT_LIMIT = 200

# 短期工作记忆条数：工具结果可能很长，6–10 条通常足以维系连贯对话
_WORKING_MEMORY_LIMIT = 20


class AgentEngine:
    """驱动 Agent 生命周期：可选慢思考，再行动并执行工具。

    工作区边界跟随 Session，不再由 Engine 持有 work_dir。
    """

    def __init__(
        self,
        provider: LLMProvider,
        registry: Registry,
        *,
        enable_thinking: bool = False,
        # 默认水位线 3000 字符，保护最近 6 条
        compactor: Compactor | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self.enable_thinking = enable_thinking
        self.compactor = compactor or Compactor(max_chars=3000, retain_last_msgs=6)

    async def run(
        self,
        session: Session,
        reporter: Reporter | None = None,
    ) -> None:
        """基于 Session 运行 ReAct 循环，直到模型不再请求工具。

        调用方须事先把本轮用户输入 append 进 session。
        Compact 只作用于本轮发给模型的临时上下文；写入 Session 的永远是全量真实数据。
        """
        logger.info(
            "唤醒会话 [%s]，锁定工作区: %s，enable_thinking=%s",
            session.id,
            session.work_dir,
            self.enable_thinking,
        )

        # 根据当前 Session 的工作区，动态组装最新的 System Prompt
        composer = PromptComposer(session.work_dir)
        system_msg = composer.build()

        turn = 0
        while True:
            turn += 1
            logger.info("Turn %d 开始 session=%s", turn, session.id)

            tools = self._registry.get_available_tools()

            # 1. 从 Session 提取近期 Working Memory（给压缩器留足判断空间）
            working_memory = session.get_working_memory(_WORKING_MEMORY_LIMIT)
            context_history: list[Message] = [system_msg, *working_memory]

            # 2. 【核心注入点】向 Provider 发起推理前过一遍压缩器
            # 无论你带出了多少上下文，如果字符总数超标，早期日志将被掩码化，超大日志将被掐头去尾
            compacted_context = self.compactor.compact(context_history)

            # 3. 后续 Generate 全面使用被保护过的 compacted_context
            # Phase 1：慢思考（不挂载工具）
            if self.enable_thinking:
                await self._run_thinking(session, compacted_context, reporter)

            # Phase 2：行动（必须继续用 compacted_context，不能退回未压缩的 context_history）
            action = await self._run_action(session, compacted_context, tools, reporter)
            if not action.tool_calls:
                # 无工具调用：本轮任务完成，挂起等待人类下一条指令
                logger.info("模型未请求工具，本轮会话挂起")
                break

            # 4. 执行工具，Observation 全量持久化进 Session（不受 Compact 影响）
            await self._run_observations(session, action.tool_calls, reporter)

    async def _run_thinking(
        self,
        session: Session,
        compacted_context: list[Message],
        reporter: Reporter | None,
    ) -> None:
        """慢思考：available_tools=None，迫使模型只输出规划文本。"""
        logger.info("Phase 1 慢思考（不挂载工具）")
        if reporter is not None:
            await reporter.on_thinking()

        think = await self._generate(compacted_context, tools=None, stage="Thinking")
        if think.content:
            # 持久化到 Session，并追加到本轮临时上下文供 Action 使用
            session.append(think)
            compacted_context.append(think)

    async def _run_action(
        self,
        session: Session,
        compacted_context: list[Message],
        tools: list[ToolDefinition],
        reporter: Reporter | None,
    ) -> Message:
        """行动：挂载工具定义，获取回复或工具调用。"""
        logger.info("Phase 2 行动（恢复工具）")
        action = await self._generate(compacted_context, tools=tools, stage="Action")

        #【驾驭精髓】：注意，写入 Session（硬盘/全量内存）的永远是全量的真实响应，不受 Compact 影响！ 
        # Compact 只作用于本轮发给大模型的那个临时 Context。
        # 持久化行动响应
        session.append(action)
        compacted_context.append(action)

        if action.content and reporter is not None:
            await reporter.on_message(action.content)
        return action

    async def _run_observations(
        self,
        session: Session,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> None:
        """执行工具调用，把观察结果持久化到 Session（必须带 tool_call_id）。

        本批全是只读工具时并行；一旦包含写操作（或 bash）则整批串行。
        """
        can_parallel = all(call.name in _READ_ONLY_TOOLS for call in tool_calls)
        if can_parallel:
            logger.info("模型请求并发调用 %d 个只读工具", len(tool_calls))
            observation_msgs = await self._execute_tools_parallel(tool_calls, reporter)
        else:
            logger.info("模型请求串行调用 %d 个工具（含写操作）", len(tool_calls))
            observation_msgs = await self._execute_tools_sequential(tool_calls, reporter)

        logger.info("工具执行完毕，观察结果写入 Session")
        session.append(*observation_msgs)

    async def _execute_tools_parallel(
        self,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> list[Message]:
        """并行执行工具，结果按原始 tool_calls 顺序返回。"""
        observation_msgs: list[Message | None] = [None] * len(tool_calls)

        async def _run_one(idx: int, call: ToolCall) -> None:
            observation_msgs[idx] = await self._execute_one_tool(idx, call, reporter)

        await asyncio.gather(
            *(_run_one(i, call) for i, call in enumerate(tool_calls))
        )
        if any(msg is None for msg in observation_msgs):
            raise RuntimeError("并行工具执行后观察结果不完整")
        return [msg for msg in observation_msgs if msg is not None]

    async def _execute_tools_sequential(
        self,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> list[Message]:
        """串行执行工具（含写操作时走此路径）。"""
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
    """截断工具输出，供 Reporter 展示（不影响写入 Session 的完整结果）。"""
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
