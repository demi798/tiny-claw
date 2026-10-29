"""OpenAI 兼容 Provider（默认对接智谱 GLM 兼容端点）。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from internal.provider.interface import LLMProvider
from internal.schema.message import Message, Role, ToolCall, ToolDefinition, Usage

logger = logging.getLogger(__name__)

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
        openai_messages = _sanitize_openai_messages(_to_openai_messages(messages))
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

        result_msg = _from_openai_message(resp.choices[0].message)

        # 【新增】提取 Usage 信息，供 Session / Tracker 累加账单
        usage = getattr(resp, "usage", None)
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            if prompt_tokens > 0 or completion_tokens > 0:
                result_msg.usage = Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

        return result_msg


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """将内部 Message 列表翻译为 OpenAI Chat Completions 消息格式。"""
    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            result.append({"role": "system", "content": msg.content or ""})
            continue

        if msg.role == Role.USER:
            if msg.tool_call_id:
                # 工具观察结果：role=tool，必须带 tool_call_id
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content or "",
                        "tool_call_id": msg.tool_call_id,
                    }
                )
            else:
                result.append({"role": "user", "content": msg.content or ""})
            continue

        if msg.role == Role.ASSISTANT:
            # 过滤 name 为空的畸形 tool_call，避免智谱 1214
            valid_tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": _arguments_to_str(tc.arguments),
                    },
                }
                for tc in msg.tool_calls
                if tc.name and tc.id
            ]
            item: dict[str, Any] = {
                "role": "assistant",
                # 智谱对 content=null 更挑剔，统一用空字符串
                "content": msg.content or "",
            }
            if valid_tool_calls:
                item["tool_calls"] = valid_tool_calls
            # 无正文且无合法 tool_calls 的空 assistant 跳过
            if item["content"] or item.get("tool_calls"):
                result.append(item)

    return result


def _sanitize_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验并修复 messages，保证 tool 调用链完整，降低智谱 1214 风险。

    规则：
    1. role=tool 必须紧跟在带匹配 tool_calls 的 assistant 之后；
    2. 丢弃孤儿 tool（找不到对应 tool_call_id）；
    3. assistant 的 tool_calls 若缺少对应 tool 结果，则去掉无结果的 call；
       若全部无结果，则降级为纯文本 assistant（有 content）或整段丢弃；
    4. 不完整的尾部 assistant(tool_calls) 同样按上面规则处理。
    """
    sanitized: list[dict[str, Any]] = []
    dropped = 0
    i = 0
    n = len(messages)

    while i < n:
        msg = messages[i]
        role = msg.get("role")

        if role in {"system", "user"}:
            sanitized.append(msg)
            i += 1
            continue

        if role == "tool":
            # 孤儿 tool：前面没有配对的 assistant.tool_calls
            dropped += 1
            logger.warning(
                "[Provider] 丢弃孤儿 tool 消息 tool_call_id=%s",
                msg.get("tool_call_id"),
            )
            i += 1
            continue

        if role != "assistant":
            dropped += 1
            logger.warning("[Provider] 丢弃未知 role 消息: %s", role)
            i += 1
            continue

        tool_calls = list(msg.get("tool_calls") or [])
        if not tool_calls:
            # 纯文本 assistant
            sanitized.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                }
            )
            i += 1
            continue

        # 收集紧随其后的、且 id 命中的 tool 结果
        expected_ids = {
            tc.get("id")
            for tc in tool_calls
            if isinstance(tc, dict) and tc.get("id")
        }
        matched_tools: list[dict[str, Any]] = []
        matched_ids: set[str] = set()
        j = i + 1
        while j < n and messages[j].get("role") == "tool":
            tid = messages[j].get("tool_call_id")
            if tid in expected_ids and tid not in matched_ids:
                matched_tools.append(messages[j])
                matched_ids.add(tid)
            else:
                # 不匹配或重复的 tool，跳过
                dropped += 1
                logger.warning(
                    "[Provider] 丢弃无法配对的 tool 消息 tool_call_id=%s",
                    tid,
                )
            j += 1

        kept_calls = [
            tc
            for tc in tool_calls
            if isinstance(tc, dict) and tc.get("id") in matched_ids
        ]

        if kept_calls and len(kept_calls) == len(matched_tools):
            sanitized.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": kept_calls,
                }
            )
            sanitized.extend(matched_tools)
        else:
            # 调用链不完整：有 content 则降级为纯文本，否则整段丢弃
            content = msg.get("content") or ""
            if content:
                sanitized.append({"role": "assistant", "content": content})
                logger.warning(
                    "[Provider] tool_calls 链不完整，已降级为纯文本 assistant "
                    "(期望 %d 个结果，实际配对 %d)",
                    len(expected_ids),
                    len(matched_ids),
                )
            else:
                dropped += 1
                logger.warning(
                    "[Provider] 丢弃无正文且 tool_calls 不完整的 assistant"
                )
            # 已扫描过的 tool 段不再二次处理
        i = j

    if dropped:
        logger.info(
            "[Provider] messages 校验完成：丢弃/修复 %d 处，保留 %d 条",
            dropped,
            len(sanitized),
        )
    # 智谱要求 system 之后首条必须是 user，否则易触发 1214
    return _ensure_openai_user_first(sanitized)


def _ensure_openai_user_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """若 system 后首条不是 user，插入占位 user，作为 1214 最后一道保险。"""
    if not messages:
        return messages

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        out.append(messages[i])
        i += 1

    rest = messages[i:]
    if rest and rest[0].get("role") != "user":
        logger.warning(
            "[Provider] system 后首条为 %s（非 user），已插入占位 user 以防 1214",
            rest[0].get("role"),
        )
        out.append(
            {
                "role": "user",
                "content": (
                    "（系统提示：早期对话上下文已压缩。"
                    "请根据近期工具执行结果，继续完成用户任务。）"
                ),
            }
        )
    out.extend(rest)
    return out


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
        return arguments if arguments else "{}"
    if arguments is None:
        return "{}"
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
        name = getattr(fn, "name", None)
        if not name:
            # 跳过 name 为空的畸形调用，避免写入 Session 后污染后续轮次
            logger.warning("[Provider] 忽略 name 为空的 tool_call id=%s", getattr(tc, "id", None))
            continue
        result.tool_calls.append(
            ToolCall(
                id=tc.id,
                name=name,
                arguments=fn.arguments,
            )
        )

    return result
