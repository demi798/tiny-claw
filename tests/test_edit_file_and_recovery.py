"""可选：fuzzy_replace 与 Recovery 特征匹配。"""

from __future__ import annotations

import pytest

from internal.context.recovery import RecoveryManager
from internal.tools.edit_file import fuzzy_replace


def test_fuzzy_replace_exact_match() -> None:
    src = "alpha\nbeta\ngamma\n"
    out = fuzzy_replace(src, "beta", "BETA")
    assert out == "alpha\nBETA\ngamma\n"


def test_fuzzy_replace_multiple_match_raises() -> None:
    src = "x\nx\n"
    with pytest.raises(ValueError, match="匹配到了"):
        fuzzy_replace(src, "x", "y")


def test_fuzzy_replace_crlf_normalized() -> None:
    src = "a\r\nb\r\nc\r\n"
    out = fuzzy_replace(src, "a\nb\n", "A\nB\n")
    assert "A\nB\n" in out.replace("\r\n", "\n")


def test_recovery_edit_file_not_found() -> None:
    mgr = RecoveryManager()
    raw = "在文件中未找到 old_text"
    out = mgr.analyze_and_inject("edit_file", raw)
    assert "[系统救援指南]" in out
    assert "read_file" in out


def test_recovery_bash_command_not_found() -> None:
    mgr = RecoveryManager()
    raw = "bash: foo: command not found"
    out = mgr.analyze_and_inject("bash", raw)
    assert "[系统救援指南]" in out
    assert "替代命令" in out


def test_recovery_unknown_error_passthrough() -> None:
    mgr = RecoveryManager()
    raw = "some unknown failure"
    assert mgr.analyze_and_inject("bash", raw) == raw
