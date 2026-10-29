"""成本追踪装饰器：包装 LLMProvider，统计耗时、Token 与费用。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from internal.engine.session import Session
from internal.provider.interface import LLMProvider
from internal.schema.message import Message, ToolDefinition

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ModelPrice:
    """单模型计费标准（单位：美元 / 1M Tokens）。"""

    input_price: float
    output_price: float


# 演示用硬编码定价（官方大致价位）
PRICING_MODEL: dict[str, _ModelPrice] = {
    # 本仓库默认模型，沿用同档估价便于演示账单
    "glm-4.6v": _ModelPrice(input_price=0.15, output_price=0.15),
}


class CostTracker(LLMProvider):
    """包装真实 LLMProvider 的装饰器中间件，可无缝注入主循环。"""

    def __init__(
        self,
        next_provider: LLMProvider,
        model_name: str,
        session: Session | None = None,
    ) -> None:
        self._next = next_provider
        self._model_name = model_name
        # 当前所属会话，用于累加总成本
        self._session = session

    async def generate(
        self,
        messages: list[Message],
        available_tools: list[ToolDefinition] | None,
    ) -> Message | None:
        """实现 LLMProvider：先调底层模型，再记耗时与账单。"""
        # 1. 记录请求发起时刻
        start = time.perf_counter()

        # 2. 调用真实底层大模型（耗时网络请求）
        try:
            resp_msg = await self._next.generate(messages, available_tools)
        except Exception:
            # 3. 失败只打耗时，不计费
            latency = time.perf_counter() - start
            logger.error("[Tracker] ❌ API 调用失败，耗时: %.3fs", latency)
            raise

        latency = time.perf_counter() - start

        # 4. 解析 Token 并计算成本
        if resp_msg is not None and resp_msg.usage is not None:
            prompt_tokens = resp_msg.usage.prompt_tokens
            completion_tokens = resp_msg.usage.completion_tokens

            cost = 0.0
            price = PRICING_MODEL.get(self._model_name)
            if price is not None:
                # 花费 = (输入 Tokens * 输入单价 + 输出 Tokens * 输出单价) / 1_000_000
                cost = (
                    prompt_tokens * price.input_price
                    + completion_tokens * price.output_price
                ) / 1_000_000.0

            # 5. 打印仪表盘日志（用 ¥ 展示）
            logger.info(
                "[Tracker] 📊 API 调用完成 | 耗时: %.3fs | 输入: %d tk | 输出: %d tk | 花费: ¥%.6f",
                latency,
                prompt_tokens,
                completion_tokens,
                cost,
            )

            # 6. 账单累加到当前 Session，供后续查询
            if self._session is not None:
                self._session.record_usage(prompt_tokens, completion_tokens, cost)
                logger.info(
                    "[Tracker] 💰 当前会话 (%s) 累计花费: ¥%.6f",
                    self._session.id,
                    self._session.total_cost_cny,
                )
        else:
            logger.warning(
                "[Tracker] ⚠️ API 调用完成，但未返回 Usage 数据 | 耗时: %.3fs",
                latency,
            )

        return resp_msg


def new_cost_tracker(
    next_provider: LLMProvider,
    model_name: str,
    session: Session | None = None,
) -> CostTracker:
    """构造被监控的 Provider"""
    return CostTracker(next_provider, model_name, session)
