"""
Harness Benchmark 入口：跑微型评测集并汇总通过率 / 成本。
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

from internal.eval.benchmark import TestCase, new_benchmark_runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 选用廉价模型跑分，控制成本
_BENCH_MODEL = "glm-4.6v"


def _build_testcases() -> list[TestCase]:
    """构建一套微型评测集。"""
    return [
        TestCase(
            id="test_001_edit",
            name="测试模糊替换工具的准确性",
            # 准备靶机：生成一个有待改版本号的 json 文件
            setup_script=(
                'echo \'{"name": "tiny-claw", "version": "v1.0.0"}\' > config.json'
            ),
            # 考题：要求修改版本号
            task_prompt=(
                "当前目录下有一个 config.json。"
                "请你使用 edit_file 工具，将其中的 version 从 v1.0.0 改为 v2.0.0。"
                "不要做其他多余操作。"
            ),
            # 判卷：grep 检查是否包含 v2.0.0
            validate_script='grep \'"version": "v2.0.0"\' config.json',
        ),
        TestCase(
            id="test_002_code_gen",
            name="测试代码阅读与创建新文件的综合能力",
            # 准备靶机：生成一个简单的乘法函数
            setup_script=(
                "printf '%s\\n' "
                "'def multiply(a: int, b: int) -> int:' "
                "'    return a * b' "
                "> mathutil.py"
            ),
            # 考题：根据代码写单元测试
            task_prompt=(
                "当前目录下有一个 mathutil.py。"
                "请你仔细阅读它，然后在同级目录下，帮我写一个规范的单元测试文件 "
                "test_mathutil.py，用来测试 multiply 函数。"
                "请务必使用 Python 标准库 unittest，并包含正常的测试用例。"
            ),
            # 判卷：直接跑 unittest；不通过则 0 分
            validate_script="python -m unittest test_mathutil.py -v",
        ),
    ]


async def _async_main() -> None:
    if not os.getenv("ZHIPU_API_KEY"):
        raise SystemExit("请先设置 ZHIPU_API_KEY 环境变量进行跑分测试")

    testcases = _build_testcases()
    runner = new_benchmark_runner(_BENCH_MODEL)
    await runner.run_suite(testcases)


def main() -> None:
    try:
        asyncio.run(_async_main())
    except Exception:
        logger.exception("Benchmark 运行崩溃")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
