"""tiny-claw 入口：真实 Provider + 极简工具集（读/写/bash）。"""

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

load_dotenv(_ROOT / "internal" / "provider" / ".env")
load_dotenv()

from internal.engine.loop import AgentEngine
from internal.provider.openai import OpenAIProvider
from internal.tools.registry import new_registry
from internal.tools.bash import BashTool
from internal.tools.read_file import ReadFileTool
from internal.tools.write_file import WriteFileTool
from internal.tools.edit_file import EditFileTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def _async_main() -> None:
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量")

    # Path.cwd()：获取当前工作目录（Current Working Directory），返回一个 Path 对象
    work_dir = str(Path.cwd())

    llm_provider = OpenAIProvider.from_zhipu("glm-4.7-flash")
    registry = new_registry()

    # 挂载工具全家桶
    registry.register(ReadFileTool(work_dir))
    registry.register(WriteFileTool(work_dir))
    registry.register(BashTool(work_dir))
    registry.register(EditFileTool(work_dir))

    engine = AgentEngine(
        llm_provider,
        registry,
        work_dir,
        enable_thinking=True,
    )

    prompt = dedent(
        # """
        # 请帮我执行以下操作：
        # 1. 用 bash 查看一下我当前电脑的 Python 版本。
        # 2. 帮我写一个简单的 helloworld.py 文件，输出 "Hello, tiny-claw!"。
        # 3. 用 bash 运行这个 python 文件，确认它能正常工作。
        # """
        # """
        # 我当前目录下有一个 server.py 文件。 
        # 请帮我把里面 "TODO: 增加鉴权逻辑" 下面的那个 if 语句，整个替换为：
        # if user_id is None:
        #     print("No auth, everyone can access.")
        #     return
        # """
        # 下发一个需要收集多源信息的任务 prompt
        """
        当前目录下有 a.txt, b.txt, c.txt 三个文件。 
        为了节省时间，请你同时一次性读取这三个文件，先读取三个文件各自的内容，最后将它们的内容综合起来，告诉我它们分别记录了什么领域的信息.
        """
    ).strip()

    await engine.run(prompt)


def main() -> None:
    try:
        asyncio.run(_async_main())
    except Exception:
        logger.exception("引擎运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
