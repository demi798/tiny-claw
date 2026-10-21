"""Anthropic / Claude 兼容 Provider（默认对接智谱兼容端点）。"""

from __future__ import annotations

import json
import os
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from internal.provider.interface import LLMProvider
from internal.schema.message import Message, Role, ToolCall, ToolDefinition

_ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/anthropic"
_DEFAULT_MAX_TOKENS = 4096

load_dotenv()

class ClaudeProvider(LLMProvider):
    """基于 Anthropic 官方 SDK 的异步 Provider。"""

    def __init__(self, client: AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_zhipu(cls, model: str) -> ClaudeProvider:
        """构造指向智谱兼容端点的 Claude Provider。"""
        api_key = os.getenv("ZHIPU_API_KEY")
        if not api_key:
            raise ValueError("请设置 ZHIPU_API_KEY 环境变量")

        client = AsyncAnthropic(api_key=api_key, base_url=_ZHIPU_BASE_URL)
        return cls(client, model)

    async def generate(
        self,
        messages: list[Message],
        available_tools: list[ToolDefinition] | None,
    ) -> Message | None:
        system_prompt, anthropic_messages = _to_anthropic_messages(messages)
        anthropic_tools = _to_anthropic_tools(available_tools or [])

        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "messages": anthropic_messages,
        }
        if system_prompt:
            request["system"] = system_prompt
        # 慢思考：无工具时不挂载 tools
        if anthropic_tools:
            request["tools"] = anthropic_tools

        try:
            resp = await self._client.messages.create(**request)
        except Exception as err:
            raise RuntimeError("Claude/Zhipu API 请求失败") from err

        return _from_anthropic_response(resp)


def _to_anthropic_messages(
    messages: list[Message],
) -> tuple[str, list[dict[str, Any]]]:
    """将内部消息转为 Claude 格式；system 单独抽出。"""
    system_prompt = ""
    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            system_prompt = msg.content
            continue

        if msg.role == Role.USER:
            if msg.tool_call_id:
                # 工具观察结果：user + tool_result block
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content,
                                "is_error": False,
                            }
                        ],
                    }
                )
            else:
                result.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": msg.content}],
                    }
                )
            continue

        if msg.role == Role.ASSISTANT:
            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})

            # 历史 tool_calls 转回 Claude 的 tool_use，维系推理链
            for tc in msg.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": _arguments_to_dict(tc.arguments),
                    }
                )

            if blocks:
                result.append({"role": "assistant", "content": blocks})

    return system_prompt, result


def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """将内部 ToolDefinition 翻译为 Claude tools。"""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": _to_input_schema(tool.input_schema),
        }
        for tool in tools
    ]


def _to_input_schema(schema: Any) -> dict[str, Any]:
    """从 JSON Schema 中提取 properties / required。"""
    if not isinstance(schema, dict):
        schema = json.loads(json.dumps(schema))

    properties: dict[str, Any] = {}
    required: list[str] = []

    raw_props = schema.get("properties")
    if isinstance(raw_props, dict):
        properties = raw_props

    raw_required = schema.get("required")
    if isinstance(raw_required, list):
        required = [str(item) for item in raw_required]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _arguments_to_dict(arguments: Any) -> dict[str, Any]:
    """Claude tool_use.input 需要是对象，而不是 JSON 字符串。"""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, (bytes, bytearray)):
        arguments = arguments.decode("utf-8")
    if isinstance(arguments, str):
        if not arguments:
            return {}
        parsed = json.loads(arguments)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    # fallback：JSON 往返
    parsed = json.loads(json.dumps(arguments))
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _from_anthropic_response(resp: Any) -> Message:
    """将 Claude 响应 content blocks 反向翻译为内部 Message。"""
    result = Message(role=Role.ASSISTANT, content="")

    for block in resp.content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            result.content += getattr(block, "text", "") or ""
        elif block_type == "tool_use":
            raw_input = getattr(block, "input", {}) or {}
            result.tool_calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    # 序列化为 JSON 字符串/字节语义，这里用 str 更 Pythonic
                    arguments=json.dumps(raw_input, ensure_ascii=False),
                )
            )

    return result
