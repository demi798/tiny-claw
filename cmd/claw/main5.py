"""多智能体协同演示：主 Agent 通过 spawn_subagent 委派只读探路者。

子智能体仅持有 read_file / bash；主智能体全功能 + SubagentTool，
探索结果以精炼报告回流，再由主 Agent 写 answer.txt。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from textwrap import dedent

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
from internal.tools.bash import BashTool
from internal.tools.edit_file import EditFileTool
from internal.tools.read_file import ReadFileTool
from internal.tools.registry import new_registry
from internal.tools.subagent import SubagentTool
from internal.tools.write_file import WriteFileTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_SESSION_ID = "test_subagent_001"


async def _async_main() -> None:
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    llm_provider = OpenAIProvider.from_zhipu("glm-4.6v")
    reporter = TerminalReporter()

    # 【防御沙箱】为子智能体准备受限的只读注册表
    read_only_registry = new_registry()
    read_only_registry.register(ReadFileTool(work_dir))
    read_only_registry.register(BashTool(work_dir))  # 允许简单的 grep 等搜索

    # 为主智能体准备全功能注册表
    main_registry = new_registry()
    main_registry.register(ReadFileTool(work_dir))
    main_registry.register(WriteFileTool(work_dir))
    main_registry.register(BashTool(work_dir))
    main_registry.register(EditFileTool(work_dir))

    # 初始化主引擎
    engine = AgentEngine(
        llm_provider,
        main_registry,
        enable_thinking=False,
        plan_mode=False,
    )

    # 【核心装配】：将带有 Engine 引用和只读 Registry 的 Subagent 工具注册进主线
    main_registry.register(
        SubagentTool(engine, read_only_registry, reporter)
    )

    session = global_session_mgr.get_or_create(_SESSION_ID, work_dir)

    prompt = dedent(
        """
        我需要你在这个遗留项目里，找到那个“核心密码”。
        为了防止污染主上下文，请你务必派出子智能体（spawn_subagent）去执行探索任务。
        你可以让子智能体使用 bash 去查找当前目录（及其所有子目录）下名为 config.txt 的文件。
        子智能体拿到密码向你汇报后，请你亲自使用 write_file 工具，将密码写在根目录的 answer.txt 里。
        """
    ).strip()

    logger.info("\n>>> 🚀 启动多智能体协同测试...")
    session.append(Message(role=Role.USER, content=prompt))
    await engine.run(session, reporter)


def main() -> None:
    try:
        asyncio.run(_async_main())
    except Exception:
        logger.exception("引擎运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
