"""评测基准：定义用例，并在隔离沙箱中跑 Agent 验收。

对应 Go ``internal/eval/benchmark.go`` 的 TestCase / TestResult / BenchmarkRunner。
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from internal.engine.loop import AgentEngine
from internal.engine.session import Session
from internal.observability.tracker import CostTracker
from internal.provider.openai import OpenAIProvider
from internal.schema.message import Message, Role
from internal.tools.bash import BashTool
from internal.tools.edit_file import EditFileTool
from internal.tools.read_file import ReadFileTool
from internal.tools.registry import new_registry
from internal.tools.write_file import WriteFileTool

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """一个需要 Agent 完成并验证的独立任务。"""

    id: str  # 用例唯一标识
    name: str  # 用例名称
    task_prompt: str  # 发送给 Agent 的任务指令
    validate_script: str  # Agent 结束后执行的 bash 校验脚本；exit 0 成功，其它失败
    setup_script: str = ""  # 可选：Agent 运行前执行的 bash，用于初始化靶机代码
    max_turns: int = 0  # 允许 Agent 尝试的最大轮数（超时算失败）；0 表示不限制


@dataclass
class TestResult:
    """单次跑分结果。"""

    test_case_id: str
    passed: bool = False
    total_cost_cny: float = 0.0
    duration_ms: int = 0
    error_msg: str = ""


class BenchmarkRunner:
    """按用例依次拉起隔离沙箱 + 带 CostTracker 的引擎，汇总跑分报告。"""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    async def run_suite(self, testcases: list[TestCase]) -> list[TestResult]:
        """执行一组评测集，打印终极报告，并返回各用例结果。"""
        logger.info("==================================================")
        logger.info(
            "🚀 启动自动化 Harness Benchmark 评估... | 模型: %s",
            self._model_name,
        )
        logger.info("==================================================")

        results: list[TestResult] = []
        passed_count = 0
        total_cost = 0.0

        for tc in testcases:
            logger.info("\n>>> ⏳ 正在执行用例 [%s]: %s", tc.id, tc.name)
            res = await self._run_single_test(tc)
            results.append(res)

            if res.passed:
                passed_count += 1
                logger.info(
                    ">>> ✅ 用例 [%s] 测试通过! | 耗时: %dms | 花费: ¥%.6f",
                    tc.id,
                    res.duration_ms,
                    res.total_cost_cny,
                )
            else:
                logger.info(
                    ">>> ❌ 用例 [%s] 测试失败! | 错误: %s",
                    tc.id,
                    res.error_msg,
                )
            total_cost += res.total_cost_cny

        n = len(testcases)
        rate = (passed_count / n * 100.0) if n else 0.0
        logger.info("\n================ 🏆 跑分终极报告 ================")
        logger.info(
            "总用例数: %d | 成功数: %d | 成功率: %.2f%%",
            n,
            passed_count,
            rate,
        )
        logger.info("总消耗成本: ¥%.6f", total_cost)
        logger.info("==================================================")
        return results

    async def _run_single_test(self, tc: TestCase) -> TestResult:
        """在干净沙箱中跑单个用例：Setup → Agent → Validate。"""
        start = time.monotonic()

        # 1. 为每个用例创建绝对干净的沙箱目录（物理隔离）
        work_dir = str(
            Path.cwd() / "workspace" / f"{tc.id}_{int(time.time())}"
        )
        Path(work_dir).mkdir(parents=True, exist_ok=True)

        # 2. （可选）执行 Setup 脚本准备靶机代码
        if tc.setup_script:
            setup = subprocess.run(
                ["bash", "-c", tc.setup_script],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            if setup.returncode != 0:
                return TestResult(
                    test_case_id=tc.id,
                    passed=False,
                    error_msg="靶机 Setup 失败",
                )

        # 3. 组装具备打点能力（CostTracker）的引擎
        real_provider = OpenAIProvider.from_zhipu(self._model_name)
        # 为本次跑分单独建 Session 记账（不走全局 SessionManager，避免串味）
        session = Session(id=tc.id, work_dir=work_dir)
        tracked_provider = CostTracker(real_provider, self._model_name, session)

        registry = new_registry()
        registry.register(ReadFileTool(work_dir))
        registry.register(WriteFileTool(work_dir))
        registry.register(BashTool(work_dir))
        registry.register(EditFileTool(work_dir))

        engine = AgentEngine(
            tracked_provider,
            registry,
            enable_thinking=False,
            plan_mode=False,
        )

        # 4. 让 Agent 开始干活（reporter=None 屏蔽普通日志，防止刷屏）
        session.append(Message(role=Role.USER, content=tc.task_prompt))
        try:
            await engine.run(session, reporter=None)
        except Exception as err:
            return TestResult(
                test_case_id=tc.id,
                passed=False,
                total_cost_cny=session.total_cost_cny,
                duration_ms=_elapsed_ms(start),
                error_msg=f"Agent 崩溃: {err}",
            )

        # 5. 【核心断言】Agent 跑完后验收成果
        validate = subprocess.run(
            ["bash", "-c", tc.validate_script],
            cwd=work_dir,
            capture_output=True,
            text=True,
        )
        duration = _elapsed_ms(start)

        if validate.returncode != 0:
            # CombinedOutput：合并 stdout/stderr，便于定位断言失败原因
            out = (validate.stdout or "") + (validate.stderr or "")
            return TestResult(
                test_case_id=tc.id,
                passed=False,
                total_cost_cny=session.total_cost_cny,
                duration_ms=duration,
                error_msg=f"验证脚本执行失败: {out}",
            )

        return TestResult(
            test_case_id=tc.id,
            passed=True,
            total_cost_cny=session.total_cost_cny,
            duration_ms=duration,
        )


def new_benchmark_runner(model: str) -> BenchmarkRunner:
    """构造 BenchmarkRunner（对应 Go NewBenchmarkRunner）。"""
    return BenchmarkRunner(model)


def _elapsed_ms(start: float) -> int:
    """自 start（monotonic）起经过的毫秒数。"""
    return int((time.monotonic() - start) * 1000)
