"""Agent 引擎：微型 OS 的核心驱动（ReAct 主循环）。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from internal.context.composer import PromptComposer
from internal.context.compactor import Compactor
from internal.context.recovery import RecoveryManager
from internal.engine.reminder import ReminderInjector
from internal.engine.reporter import Reporter
from internal.engine.session import Session
from internal.provider.interface import LLMProvider
from internal.schema.message import Message, Role, ToolCall, ToolDefinition, ToolResult
from internal.tools.registry import Registry

logger = logging.getLogger(__name__)

# 仅这些工具可安全并行；其余（含 bash / write_file / edit_file）一律串行
_READ_ONLY_TOOLS = frozenset({"read_file"})

# Reporter 展示用截断长度，避免飞书等渠道消息过长被截断
_REPORTER_OUTPUT_LIMIT = 200

# 短期工作记忆条数：给压缩器留足判断空间
_WORKING_MEMORY_LIMIT = 20

# 子智能体最多轮次，防止自己卡死
_MAX_SUB_TURNS = 10

# 探路者 System Prompt：严厉要求必须用工具、禁止空想
_SUBAGENT_SYSTEM_PROMPT = """你是一个专门负责深度探索的探路者 (Explorer Subagent)。
你的任务是根据主架构师的指令，在当前工作区内仔细阅读代码、查阅日志，搜集足够的信息。

【核心纪律】
1. 你必须、且只能依靠内置工具（如 bash 的 find/grep，或 read_file）去寻找答案。绝对不允许凭空捏造或猜测！
2. 如果你没有找到确切的答案，你必须继续使用工具深入搜索。
3. 当且仅当你找到了确切的线索后，停止调用工具，直接输出一段纯文本作为你的终极汇报。主架构师会根据你的汇报来做下一步决策。
"""


class AgentEngine:
    """驱动 Agent 生命周期：可选慢思考，再行动并执行工具。

    工作区边界跟随 Session，不再由 Engine 持有 work_dir。
    """

    def __init__(
        self,
        provider: LLMProvider,
        registry: Registry,
        enable_thinking: bool = False,
        plan_mode: bool = False,
        # 人工审批闸门：告知模型勿口头拒绝，交由 Middleware + 人类裁决
        approval_gate: bool = False,
        compactor: Compactor | None = None,
        recovery: RecoveryManager | None = None,
        # 【新增】提醒注入器：连续相同失败时强力打断
        injector: ReminderInjector | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self.enable_thinking = enable_thinking
        self.plan_mode = plan_mode
        self.approval_gate = approval_gate
        self.compactor = compactor or Compactor(max_chars=20000, retain_last_msgs=6)
        self.recovery = recovery or RecoveryManager()
        self.injector = injector or ReminderInjector()

    async def run(
        self,
        session: Session,
        reporter: Reporter | None = None,
    ) -> None:
        """基于 Session 运行 ReAct 循环，直到模型不再请求工具。

        调用方须事先把本轮用户输入 append 进 session。
        Compact 只作用于本轮发给模型的临时上下文；写入 Session 的永远是全量真实数据。
        Thinking 与 Action 合并为单条 Assistant 写入 Session，避免智谱 1214。
        工具结果写入后，经 ReminderInjector 探测死循环，必要时追加打断消息。
        """
        logger.info(
            "唤醒会话 [%s]，锁定工作区: %s，enable_thinking=%s，plan_mode=%s，approval_gate=%s",
            session.id,
            session.work_dir,
            self.enable_thinking,
            self.plan_mode,
            self.approval_gate,
        )

        composer = PromptComposer(
            session.work_dir,
            plan_mode=self.plan_mode,
            approval_gate=self.approval_gate,
        )
        system_msg = composer.build()

        turn = 0
        while True:
            turn += 1
            logger.info("Turn %d 开始 session=%s", turn, session.id)

            tools = self._registry.get_available_tools()

            working_memory = session.get_working_memory(_WORKING_MEMORY_LIMIT)
            context_history: list[Message] = [system_msg, *working_memory]
            compacted_context = self.compactor.compact(context_history)

            # 本轮 Thinking 正文（不单独写入 Session）
            current_turn_thinking = ""

            # ================= Phase 1: Thinking =================
            if self.enable_thinking:
                current_turn_thinking = await self._run_thinking(
                    compacted_context,
                    reporter,
                )

            # ================= Phase 2: Action =================
            action = await self._run_action(compacted_context, tools, reporter)

            # 【1214 修复】Thinking + Action 合并为合法的单条 Assistant
            merged_content = "\n".join(
                part for part in (current_turn_thinking.strip(), action.content.strip()) if part
            )
            final_assistant = Message(
                role=Role.ASSISTANT,
                content=merged_content,
                tool_calls=list(action.tool_calls),
            )
            session.append(final_assistant)

            if action.content and reporter is not None:
                await reporter.on_message(action.content)

            if not action.tool_calls:
                logger.info("模型未请求工具，本轮会话挂起")
                break

            # ================= 执行工具并注入自愈模板 =================
            await self._run_observations(session, action.tool_calls, reporter)

    async def run_sub(
        self,
        task_prompt: str,
        read_only_registry: Registry,
        reporter: Reporter | Any | None = None,
    ) -> str:
        """专为 Subagent 拉起的一次性受限循环：不依赖外部 Session，打完就跑。

        Reporter 透传自主线程，工具名加 [Subagent] 前缀便于终端区分轨迹。
        强制关闭慢思考，仅使用传入的只读工具注册表。
        """
        # reporter 可能来自 tools 包的 Any；尽量按 Reporter 使用
        typed_reporter: Reporter | None = (
            reporter if isinstance(reporter, Reporter) else None
        )

        context_history: list[Message] = [
            Message(role=Role.SYSTEM, content=_SUBAGENT_SYSTEM_PROMPT),
            Message(role=Role.USER, content=task_prompt),
        ]

        turn_count = 0
        while True:
            turn_count += 1
            if turn_count > _MAX_SUB_TURNS:
                raise RuntimeError(
                    f"子智能体探索过于深入，超过 {_MAX_SUB_TURNS} 轮被强制召回，"
                    "请主 Agent 给它更明确的指令"
                )

            # 【驾驭底线】：仅获取传入的只读工具注册表
            available_tools = read_only_registry.get_available_tools()
            compacted_context = self.compactor.compact(context_history)

            # 子任务急速响应：强制关闭慢思考，直接预测行动
            try:
                action = await self._generate(
                    compacted_context,
                    tools=available_tools,
                    stage="Subagent-Action",
                )
            except Exception as err:
                raise RuntimeError("子智能体推理失败") from err

            context_history.append(action)

            # 【核心退出条件】：不再调用工具 → 纯文本汇报返回上层
            if not action.tool_calls:
                return action.content

            # 执行只读工具（与 Go 一致：并发）；结果仅写入本地 contextHistory
            observation_msgs = await self._execute_sub_tools(
                action.tool_calls,
                read_only_registry,
                typed_reporter,
            )
            context_history.extend(observation_msgs)

    async def _execute_sub_tools(
        self,
        tool_calls: list[ToolCall],
        registry: Registry,
        reporter: Reporter | None,
    ) -> list[Message]:
        """子智能体工具执行：并发跑只读工具，Reporter 带 [Subagent] 前缀。"""
        pairs: list[tuple[Message, ToolResult] | None] = [None] * len(tool_calls)

        async def _run_one(idx: int, call: ToolCall) -> None:
            pairs[idx] = await self._execute_one_tool(
                idx,
                call,
                reporter,
                registry=registry,
                tool_name_prefix="[Subagent] ",
            )

        await asyncio.gather(
            *(_run_one(i, call) for i, call in enumerate(tool_calls))
        )
        if any(item is None for item in pairs):
            raise RuntimeError("子智能体并行工具执行后观察结果不完整")
        return [msg for msg, _ in pairs if msg is not None]

    async def _run_thinking(
        self,
        compacted_context: list[Message],
        reporter: Reporter | None,
    ) -> str:
        """慢思考：不挂载工具；正文只返回给本轮合并，不单独 append Session。"""
        logger.info("Phase 1 慢思考（不挂载工具）")
        if reporter is not None:
            await reporter.on_thinking()

        think = await self._generate(compacted_context, tools=None, stage="Thinking")
        if think.content:
            # 仅追加到本轮临时上下文，供 Action 使用
            compacted_context.append(think)
            return think.content
        return ""

    async def _run_action(
        self,
        compacted_context: list[Message],
        tools: list[ToolDefinition],
        reporter: Reporter | None,
    ) -> Message:
        """行动：挂载工具定义；不在此处写 Session（由 run 合并后写入）。"""
        logger.info("Phase 2 行动（恢复工具）")
        action = await self._generate(compacted_context, tools=tools, stage="Action")
        # 供本轮后续逻辑使用；Session 由 run 统一合并写入
        compacted_context.append(action)
        return action

    async def _run_observations(
        self,
        session: Session,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> None:
        """执行工具调用，失败时注入 Recovery 指南，再写入 Session，并做死循环探测。"""
        can_parallel = all(call.name in _READ_ONLY_TOOLS for call in tool_calls)
        if can_parallel:
            logger.info("模型请求并发调用 %d 个只读工具", len(tool_calls))
            pairs = await self._execute_tools_parallel(tool_calls, reporter)
        else:
            logger.info("模型请求串行调用 %d 个工具（含写操作）", len(tool_calls))
            pairs = await self._execute_tools_sequential(tool_calls, reporter)

        observation_msgs = [msg for msg, _ in pairs]

        # 1. 先将普通的工具执行结果存入 Session
        logger.info("工具执行完毕，观察结果写入 Session")
        session.append(*observation_msgs)

        # 2. 【核心防线】：进入下一轮前进行死循环探测
        # 简化策略：取本轮第一个工具的原始结果供 Reminder 分析
        last_tool_call = tool_calls[0]
        last_tool_result = pairs[0][1]
        reminder_msg = self.injector.check_and_inject(last_tool_call, last_tool_result)
        if reminder_msg is not None:
            # 触发干预时，将严厉提醒作为 User 消息追加到 Session 末尾
            session.append(reminder_msg)

    async def _execute_tools_parallel(
        self,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> list[tuple[Message, ToolResult]]:
        """并行执行工具，结果按原始 tool_calls 顺序返回。"""
        pairs: list[tuple[Message, ToolResult] | None] = [None] * len(tool_calls)

        async def _run_one(idx: int, call: ToolCall) -> None:
            pairs[idx] = await self._execute_one_tool(idx, call, reporter)

        await asyncio.gather(
            *(_run_one(i, call) for i, call in enumerate(tool_calls))
        )
        if any(item is None for item in pairs):
            raise RuntimeError("并行工具执行后观察结果不完整")
        return [item for item in pairs if item is not None]

    async def _execute_tools_sequential(
        self,
        tool_calls: list[ToolCall],
        reporter: Reporter | None,
    ) -> list[tuple[Message, ToolResult]]:
        """串行执行工具（含写操作时走此路径）。"""
        pairs: list[tuple[Message, ToolResult]] = []
        for idx, call in enumerate(tool_calls):
            pairs.append(await self._execute_one_tool(idx, call, reporter))
        return pairs

    async def _execute_one_tool(
        self,
        idx: int,
        call: ToolCall,
        reporter: Reporter | None,
        *,
        registry: Registry | None = None,
        tool_name_prefix: str = "",
    ) -> tuple[Message, ToolResult]:
        """执行单个工具；失败时经 RecoveryManager 注入救援指南。

        返回 (Observation 消息, 原始 ToolResult)，原始结果供 Reminder 分析 IsError。
        registry 默认用引擎主注册表；子智能体可传入只读注册表。
        tool_name_prefix 用于 Reporter 展示（如 "[Subagent] "）。
        """
        active_registry = registry if registry is not None else self._registry
        display_name = f"{tool_name_prefix}{call.name}"
        args_text = _format_arguments(call.arguments)
        logger.info("  -> [Task-%d] 执行工具: %s args=%s", idx, display_name, args_text)

        if reporter is not None:
            await reporter.on_tool_call(display_name, args_text)

        result = await active_registry.execute(call)

        # 【核心拦截与注入】错误结果交给 Recovery 增强后再进上下文
        final_output = result.output
        if result.is_error:
            final_output = self.recovery.analyze_and_inject(call.name, result.output)
            logger.warning("  -> [Task-%d] ❌ 注入救援指南: %s", idx, final_output)
        else:
            logger.info(
                "  -> [Task-%d] ✅ 工具成功 %s，返回 %d 字节",
                idx,
                display_name,
                len(result.output),
            )

        if reporter is not None:
            await reporter.on_tool_result(
                display_name,
                _truncate_for_reporter(final_output),
                is_error=result.is_error,
            )

        observation = Message(
            role=Role.USER,
            content=final_output,
            tool_call_id=call.id,
        )
        return observation, result

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
