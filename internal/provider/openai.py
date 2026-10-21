"""OpenAI 兼容 Provider（默认对接智谱 GLM 兼容端点）。"""

from __future__ import annotations

import json
import os
from typing import Any
from dotenv import load_dotenv
from openai import AsyncOpenAI

from internal.provider.interface import LLMProvider
from internal.schema.message import Message, Role, ToolCall, ToolDefinition

_ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
load_dotenv()

class OpenAIProvider(LLMProvider):
    """基于 OpenAI 官方 SDK 的异步 Provider。"""

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_zhipu(cls, model: str) -> OpenAIProvider:
        """构造指向智谱 OpenAI 兼容端点的 Provider。"""
        api_key = os.getenv("ZHIPU_API_KEY")
        if not api_key:
            raise ValueError("请设置 ZHIPU_API_KEY 环境变量")

        client = AsyncOpenAI(api_key=api_key, base_url=_ZHIPU_BASE_URL)
        return cls(client, model)

    async def generate(
        self,
        messages: list[Message],
        available_tools: list[ToolDefinition] | None,
    ) -> Message | None:
        openai_messages = _to_openai_messages(messages)
        openai_tools = _to_openai_tools(available_tools or [])

        # 慢思考：无工具时不挂载 tools，迫使模型只输出纯文本
        request: dict[str, Any] = {
            "model": self._model,
            "messages": openai_messages,
        }
        if openai_tools:
            request["tools"] = openai_tools

        try:
            resp = await self._client.chat.completions.create(**request)
        except Exception as err:
            raise RuntimeError("OpenAI/Zhipu API 请求失败") from err

        if not resp.choices:
            raise RuntimeError("API 返回了空的 Choices")

        return _from_openai_message(resp.choices[0].message)


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """将内部 Message 列表翻译为 OpenAI Chat Completions 消息格式。"""
    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            result.append({"role": "system", "content": msg.content})
            continue

        if msg.role == Role.USER:
            if msg.tool_call_id:
                # 工具观察结果：role=tool，必须带 tool_call_id
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id,
                    }
                )
            else:
                result.append({"role": "user", "content": msg.content})
            continue

        if msg.role == Role.ASSISTANT:
            item: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or None,
            }
            # 历史中的 tool_calls 必须原样回传，维系推理链
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _arguments_to_str(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            result.append(item)

    return result


def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """将内部 ToolDefinition 翻译为 OpenAI function tools。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": _normalize_parameters(tool.input_schema),
            },
        }
        for tool in tools
    ]


def _normalize_parameters(schema: Any) -> dict[str, Any]:
    """把 input_schema 规范成 JSON Schema 对象。"""
    if isinstance(schema, dict):
        return schema
    # fallback：JSON 往返，兼容非 dict 输入
    return json.loads(json.dumps(schema))


def _arguments_to_str(arguments: Any) -> str:
    """OpenAI 要求 function.arguments 为 JSON 字符串。"""
    if isinstance(arguments, (bytes, bytearray)):
        return arguments.decode("utf-8")
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False)


def _from_openai_message(choice_message: Any) -> Message:
    """将 API 响应消息反向翻译为内部 schema.Message。"""
    result = Message(
        role=Role.ASSISTANT,
        content=choice_message.content or "",
    )

    for tc in choice_message.tool_calls or []:
        # 仅处理 function 类型工具调用
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        result.tool_calls.append(
            ToolCall(
                id=tc.id,
                name=fn.name,
                arguments=fn.arguments,
            )
        )

    return result
