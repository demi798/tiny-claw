"""工具抽象与注册分发接口。"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from internal.schema import ToolCall, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """所有具体工具必须实现的通用契约。

    一个工具需要能：说出名字与描述、给出参数 JSON Schema、
    并接收原始 JSON 字节执行具体逻辑。
    """

    @abstractmethod
    def name(self) -> str:
        """返回工具的全局唯一名称（大模型通过该名字调用）。"""
        raise NotImplementedError

    @abstractmethod
    def definition(self) -> ToolDefinition:
        """返回提交给大模型的元信息与参数 JSON Schema。"""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, args: bytes) -> str:
        """接收大模型给出的原始 JSON 参数字节并执行业务逻辑。

        反序列化由各个具体工具自行处理；失败时抛出异常。
        """
        raise NotImplementedError


class Registry(ABC):
    """工具的注册与分发接口。"""

    @abstractmethod
    def register(self, tool: BaseTool) -> None:
        """挂载一个新工具到系统中。"""
        raise NotImplementedError

    @abstractmethod
    def get_available_tools(self) -> list[ToolDefinition]:
        """返回当前已挂载工具的 Schema，供主循环交给 Provider。"""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, call: ToolCall) -> ToolResult:
        """路由并执行模型请求的工具调用。

        业务失败应通过 ToolResult.is_error 表达，而不是抛异常。
        """
        raise NotImplementedError


class ToolRegistry(Registry):
    """Registry 的默认实现：用工具名做 O(1) 路由。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        name = tool.name()
        if name in self._tools:
            logger.warning("工具 '%s' 已经被注册，将被覆盖。", name)
        self._tools[name] = tool
        logger.info("成功挂载工具: %s", name)

    def get_available_tools(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values()]

    async def execute(self, call: ToolCall) -> ToolResult:
        # 1. 路由查找：找不到说明模型产生了幻觉
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id,
                output=f"Error: 系统中不存在名为 '{call.name}' 的工具。",
                is_error=True,
            )

        # 2. 将原始 JSON 字节交给具体工具
        try:
            output = await tool.execute(_to_args_bytes(call.arguments))
        except Exception as err:
            # 3. 底层执行失败：封装后交还给主循环
            return ToolResult(
                tool_call_id=call.id,
                output=f"Error executing {call.name}: {err}",
                is_error=True,
            )

        return ToolResult(
            tool_call_id=call.id,
            output=output,
            is_error=False,
        )


def new_registry() -> Registry:
    """创建默认工具注册表。"""
    return ToolRegistry()


def _to_args_bytes(arguments: Any) -> bytes:
    """把 ToolCall.arguments 规范成原始 JSON 字节。"""
    if isinstance(arguments, (bytes, bytearray)):
        return bytes(arguments)
    if isinstance(arguments, str):
        return arguments.encode("utf-8")
    return json.dumps(arguments, ensure_ascii=False).encode("utf-8")
