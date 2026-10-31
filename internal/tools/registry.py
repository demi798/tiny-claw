"""工具抽象与注册分发接口。"""

from __future__ import annotations

import json
import logging
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from internal.observability.trace import start_span
from internal.schema import ToolCall, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# 中间件签名：接收当前 ToolCall，返回 (是否允许执行, 拦截原因)
# 放行时 reject_reason 可为空字符串
MiddlewareFunc = Callable[[ToolCall], tuple[bool, str]]


class BaseTool(ABC):
    """所有具体工具必须实现的通用契约。

    一个工具需要能：说出名字与描述、给出参数 JSON Schema、
    并接收原始 JSON 字节执行具体逻辑。
    """

    @abstractmethod
    def name(self) -> str:
        """返回工具的全局唯一名称（大模型通过该名字调用）。"""
        ...

    @abstractmethod
    def definition(self) -> ToolDefinition:
        """返回提交给大模型的元信息与参数 JSON Schema。"""
        ...

    @abstractmethod
    async def execute(self, args: bytes) -> str:
        """接收大模型给出的原始 JSON 参数字节并执行业务逻辑。

        反序列化由各个具体工具自行处理；失败时抛出异常。
        """
        ...


@runtime_checkable
class Registry(Protocol):
    """工具注册与分发契约（结构化类型，避免仅一实现时的 ABC 负担）。"""

    def register(self, tool: BaseTool) -> None:
        """挂载一个新工具到系统中。"""
        ...

    def use(self, mw: MiddlewareFunc) -> None:
        """挂载全局 Middleware。"""
        ...

    def get_available_tools(self) -> list[ToolDefinition]:
        """返回当前已挂载工具的 Schema，供主循环交给 Provider。"""
        ...

    async def execute(self, call: ToolCall) -> ToolResult:
        """路由并执行模型请求的工具调用。

        业务失败应通过 ToolResult.is_error 表达，而不是抛异常。
        """
        ...


class ToolRegistry:
    """Registry 的默认实现：用工具名做 O(1) 路由。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        # 保存挂载的中间件链
        self._middlewares: list[MiddlewareFunc] = []

    def register(self, tool: BaseTool) -> None:
        name = tool.name()
        if name in self._tools:
            logger.warning("工具 '%s' 已经被注册，将被覆盖。", name)
        self._tools[name] = tool
        logger.info("成功挂载工具: %s", name)

    def use(self, mw: MiddlewareFunc) -> None:
        """挂载全局 Middleware，按注册顺序在 Execute 前依次执行。"""
        self._middlewares.append(mw)

    def get_available_tools(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values()]

    async def execute(self, call: ToolCall) -> ToolResult:
        # 【埋点 5】工具执行 Span（挂在当前 Turn / 调用栈下）
        span = start_span("Tool.Execute")
        span.add_attribute("tool_name", call.name)
        span.add_attribute("arguments", _arguments_to_str(call.arguments))

        try:
            # 1. 路由查找：找不到说明模型产生了幻觉
            tool = self._tools.get(call.name)
            if tool is None:
                msg = f"Error: 系统中不存在名为 '{call.name}' 的工具。"
                span.add_attribute("error", msg)
                return ToolResult(
                    tool_call_id=call.id,
                    output=msg,
                    is_error=True,
                )

            # 2. 【核心防御】执行底层逻辑前，依次运行所有 Middleware
            for mw in self._middlewares:
                allowed, reason = mw(call)
                if not allowed:
                    span.add_attribute("intercepted", True)
                    span.add_attribute("reject_reason", reason)
                    logger.warning(
                        "[Registry] ⚠️ 工具 %s 被 Middleware 拦截: %s",
                        call.name,
                        reason,
                    )
                    return ToolResult(
                        tool_call_id=call.id,
                        output=f"执行被系统拦截。原因: {reason}",
                        # 必须返回 Error，强制大模型阅读拒绝理由
                        is_error=True,
                    )

            # 3. 所有 Middleware 放行后，将原始 JSON 字节交给具体工具
            try:
                output = await tool.execute(_to_args_bytes(call.arguments))
            except Exception as err:
                span.add_attribute("error", str(err))
                # 底层执行失败：封装后交还给主循环
                return ToolResult(
                    tool_call_id=call.id,
                    output=f"Error executing {call.name}: {err}",
                    is_error=True,
                )

            # 只截取输出前 100 字符，防止 Trace 文件过度膨胀
            span.add_attribute("output_preview", _truncate(output, 100))

            return ToolResult(
                tool_call_id=call.id,
                output=output,
                is_error=False,
            )
        finally:
            # 无论成功失败，确保结束 Span
            span.end_span()


def new_registry() -> ToolRegistry:
    """创建默认工具注册表。

    已废弃：请直接使用 ``ToolRegistry()``。
    """
    warnings.warn(
        "new_registry() 已废弃，请改用 ToolRegistry()",
        DeprecationWarning,
        stacklevel=2,
    )
    return ToolRegistry()


def _truncate(s: str, max_len: int) -> str:
    """截断字符串，超出部分以 ... 结尾。"""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


def _arguments_to_str(arguments: Any) -> str:
    """将工具参数规范为调试用字符串。"""
    if isinstance(arguments, (bytes, bytearray)):
        return bytes(arguments).decode("utf-8", errors="replace")
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return ""
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except TypeError:
        return str(arguments)


def _to_args_bytes(arguments: Any) -> bytes:
    """把 ToolCall.arguments 规范成原始 JSON 字节。"""
    if isinstance(arguments, (bytes, bytearray)):
        return bytes(arguments)
    if isinstance(arguments, str):
        return arguments.encode("utf-8")
    return json.dumps(arguments, ensure_ascii=False).encode("utf-8")
