"""Recovery 自愈演示入口：故意诱发 edit_file old_text 不匹配，观察单点纠偏。

关闭 Plan 模式，用陷阱指令跳过读文件、直接替换，
触发工具错误后由 RecoveryManager 引导 Agent 改主意。
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
from internal.tools.write_file import WriteFileTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 固定 SessionID：专门用于 Recovery 自愈演示
_SESSION_ID = "test_recovery_001"


async def _async_main() -> None:
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

    # 关闭 Plan 模式，专注于见证它改变主意的单点纠偏过程
    engine = AgentEngine(
        llm_provider,
        registry,
        enable_thinking=False,
        plan_mode=False,
    )
    reporter = TerminalReporter()

    session = global_session_mgr.get_or_create(_SESSION_ID, work_dir)

    # 这是一个巨大的陷阱指令：
    # 我们不给它查看文件的机会，直接命令它凭初始上下文去修改文件，
    # 目的是诱发 old_text 不匹配的错误。
    prompt = dedent(
        """
        我当前目录下有一个 auth.py 文件。
        请修改 auth.py 中的 login 函数。
        请直接使用 edit_file 工具替换下面的代码块，将判断条件改为同时允许"admin"、"root"和"guest"三种用户登录：

        # 鉴权入口函数
        def login(user: str) -> bool:
            # 检查用户名
            if user == "admin":
                return True
            return False
        """
    ).strip()

    logger.info("\n>>> 🚀 启动自愈测试任务...")
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
