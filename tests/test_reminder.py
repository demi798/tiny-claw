"""ReminderInjector：连续失败 ≥3 / 连续成功 ≥8 打断。"""

from __future__ import annotations

from internal.engine.reminder import ReminderInjector
from internal.schema.message import Role, ToolCall, ToolResult


def _call(name: str = "bash", args: dict | None = None) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=args or {"command": "ls"})


def _ok() -> ToolResult:
    return ToolResult(tool_call_id="c1", output="ok", is_error=False)


def _err() -> ToolResult:
    return ToolResult(tool_call_id="c1", output="boom", is_error=True)


def test_failure_below_threshold_returns_none() -> None:
    inj = ReminderInjector()
    call = _call()
    assert inj.check_and_inject(call, _err()) is None
    assert inj.check_and_inject(call, _err()) is None


def test_failure_reaches_threshold_injects_user_reminder() -> None:
    inj = ReminderInjector()
    call = _call()
    assert inj.check_and_inject(call, _err()) is None
    assert inj.check_and_inject(call, _err()) is None
    msg = inj.check_and_inject(call, _err())
    assert msg is not None
    assert msg.role == Role.USER
    assert "SYSTEM REMINDER" in msg.content
    assert "bash" in msg.content
    assert "失败" in msg.content


def test_success_reaches_threshold_injects_user_reminder() -> None:
    inj = ReminderInjector()
    call = _call()
    for _ in range(7):
        assert inj.check_and_inject(call, _ok()) is None
    msg = inj.check_and_inject(call, _ok())
    assert msg is not None
    assert msg.role == Role.USER
    assert "SYSTEM REMINDER" in msg.content
    assert "成功" in msg.content or "重复" in msg.content


def test_success_clears_failure_counter() -> None:
    inj = ReminderInjector()
    call = _call()
    assert inj.check_and_inject(call, _err()) is None
    assert inj.check_and_inject(call, _err()) is None
    # 一次成功清空失败计数，之后再失败两次仍不触发
    assert inj.check_and_inject(call, _ok()) is None
    assert inj.check_and_inject(call, _err()) is None
    assert inj.check_and_inject(call, _err()) is None


def test_different_fingerprint_resets_success_streak() -> None:
    inj = ReminderInjector()
    call_a = _call(args={"command": "ls"})
    call_b = _call(args={"command": "pwd"})
    for _ in range(7):
        assert inj.check_and_inject(call_a, _ok()) is None
    # 换指纹后重新计数，第 8 次成功（相对 B）才可能触发；此处第 1 次 B 成功应无打断
    assert inj.check_and_inject(call_b, _ok()) is None
