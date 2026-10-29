"""Plan Mode CLI 入口：通过 --prompt 接收任务，开启计划模式执行。

即便进程重启导致内存 Session 丢失，只要 workspace 内 PLAN.md / TODO.md 仍在，任务即可断点续传。
"""

from __future__ import annotations

import argparse
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
from internal.tools.bash import BashTool
from internal.tools.edit_file import EditFileTool
from internal.tools.read_file import ReadFileTool
from internal.tools.registry import new_registry
from internal.tools.write_file import WriteFileTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 固定 SessionID：多次运行可复用同一内存会话；
# 进程重启后内存会丢，但 TODO.md 仍在 → 演示断点续传
_SESSION_ID = "task_web_server_001"


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="tiny-claw Plan Mode：把长程任务进度外部化到 PLAN.md / TODO.md",
    )
    parser.add_argument(
        "--prompt",
        "-p",
        required=True,
        help="要交给 Agent 执行的任务描述",
    )
    return parser.parse_args()


async def _async_main(prompt: str) -> None:
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    work_dir = str(Path.cwd() / "workspace")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    llm_provider = OpenAIProvider.from_zhipu("glm-4.6v")
    registry = new_registry()

    # 挂载四大基础工具
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(BashTool(work_dir))
    registry.register(EditFileTool(work_dir))

    # 实例化引擎：关闭慢思考，开启计划模式 (plan_mode=True)
    engine = AgentEngine(
        llm_provider,
        registry,
        enable_thinking=False,
        plan_mode=True,
    )
    reporter = TerminalReporter()

    session = global_session_mgr.get_or_create(_SESSION_ID, work_dir)

    logger.info("\n>>> 🚀 收到指令: %s", prompt)
    session.append(Message(role=Role.USER, content=prompt))
    await engine.run(session, reporter)


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_async_main(args.prompt))
    except Exception:
        logger.exception("引擎运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
