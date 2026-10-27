"""Session 并发隔离 + Working Memory 截断演示入口。

双会话模拟：
- Session A：读密钥 → 刷掉短期记忆 → 再追问密钥
- Session B：另一工作区，验证会话隔离（看不到 A 的上下文）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")
load_dotenv()

from internal.engine.loop import AgentEngine
from internal.engine.session import global_session_mgr
from internal.engine.terminal_reporter import TerminalReporter
from internal.provider.openai import OpenAIProvider
from internal.schema.message import Message, Role
from internal.tools.read_file import ReadFileTool
from internal.tools.registry import new_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 演示用工作区
_FRONT_DIR = _ROOT / "tmp" / "project_front"
_BACK_DIR = _ROOT / "tmp" / "project_back"


async def _run_session_a(engine: AgentEngine, reporter: TerminalReporter) -> None:
    """模拟飞书前端群：先查密钥，再刷记忆，再追问。"""
    session_a = global_session_mgr.get_or_create(
        "chat_front_001",
        str(_FRONT_DIR),
    )

    # 回合 1：获取机密
    logger.info("\n>>> 🙋‍♂️ [Session A / Turn 1]: 帮我看看 README.md 里记录了什么密钥？")
    session_a.append(
        Message(
            role=Role.USER,
            content="帮我看看 README.md 里记录了什么密钥？",
        )
    )
    await engine.run(session_a, reporter)

    # 故意制造大量“废话”对话，刷掉记忆（Working Memory Limit=6）
    for _ in range(6):
        session_a.append(Message(role=Role.USER, content="这只是一句闲聊占位符。"))
        session_a.append(Message(role=Role.ASSISTANT, content="好的，收到闲聊。"))

    # 回合 2：验证记忆截断（第一轮密钥应已挤出 Working Memory）
    logger.info(
        "\n>>> 🙋‍♂️ [Session A / Turn 2]: 请直接告诉我，刚才第一轮你查到的那个密钥是什么？"
    )
    session_a.append(
        Message(
            role=Role.USER,
            content="请直接告诉我，刚才第一轮你查到的那个密钥是什么？不准调用工具！",
        )
    )
    await engine.run(session_a, reporter)


async def _run_session_b(engine: AgentEngine, reporter: TerminalReporter) -> None:
    """模拟飞书后端群：验证跨会话隔离。"""
    # 稍微错开一点时间发起请求
    await asyncio.sleep(1)

    session_b = global_session_mgr.get_or_create(
        "chat_back_002",
        str(_BACK_DIR),
    )

    logger.info("\n>>> 🙋‍♂️ [Session B]: 别人查到了一个密钥，你这里能看到吗？")
    session_b.append(
        Message(
            role=Role.USER,
            content="别人查到了一个密钥，你这里能看到吗？不准调用工具！",
        )
    )
    await engine.run(session_b, reporter)


async def _async_main() -> None:
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    _FRONT_DIR.mkdir(parents=True, exist_ok=True)
    _BACK_DIR.mkdir(parents=True, exist_ok=True)

    llm_provider = OpenAIProvider.from_zhipu("glm-4.6v")
    registry = new_registry()
    # 本讲演示：引擎无状态；工具仅挂载前端工作区
    registry.register(ReadFileTool(str(_FRONT_DIR)))

    engine = AgentEngine(llm_provider, registry, enable_thinking=False)
    reporter = TerminalReporter()

    # 并发跑两个会话
    await asyncio.gather(
        _run_session_a(engine, reporter),
        _run_session_b(engine, reporter),
    )

def main() -> None:
    try:
        asyncio.run(_async_main())
    except Exception:
        logger.exception("演示运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
