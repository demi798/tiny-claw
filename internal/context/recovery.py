"""工具失败救援：根据报错特征注入可执行的恢复建议。"""

from __future__ import annotations


class RecoveryManager:
    """在工具执行失败时，按报错特征分析并注入恢复建议。"""

    def analyze_and_inject(self, tool_name: str, raw_error: str) -> str:
        """接收原始报错，匹配已知特征模式，返回增强后的报错信息。

        未匹配到特征时原样返回；匹配到则拼接「系统救援指南」。
        """
        hint = ""
        lower_error = raw_error.lower()

        if tool_name == "edit_file":
            # 匹配 edit_file 模糊替换的固定报错文案
            if (
                "在文件中未找到 old_text" in raw_error
                or "找不到该代码片段" in raw_error
            ):
                hint = (
                    "你提供的 old_text 与文件当前内容不一致，或者缺少必要的缩进。"
                    "请先使用 `read_file` 工具重新读取该文件，获取最新、准确的内容后，"
                    "再重新发起编辑。"
                )
            elif (
                "匹配到了多处" in raw_error
                or "提供更多上下文" in raw_error
                or "请提供更多的上下文" in raw_error
                or "请提供更多上下行" in raw_error
            ):
                hint = (
                    "你的 old_text 不够具体，命中了多个相同代码块。"
                    "请在 old_text 中增加上下相邻的几行代码，以确保替换的唯一性。"
                )

        elif tool_name in {"read_file", "write_file"}:
            # 匹配 POSIX / 本机常见路径错误
            if "no such file or directory" in lower_error:
                hint = (
                    "路径似乎不正确。请不要凭空猜测，先使用 `bash` 执行 "
                    "`ls -la` 或 `find . -name` 命令查找正确的目录结构和文件名。"
                )
            elif "permission denied" in lower_error:
                hint = (
                    "你没有权限操作该文件。请检查工作区限制，"
                    "或者思考是否需要修改其他文件。"
                )

        elif tool_name == "bash":
            if "command not found" in lower_error:
                hint = (
                    "系统中未安装该命令。请先思考：是否有替代命令？"
                    "或者你需要先编写脚本进行安装？"
                )
            elif "超时" in raw_error or "deadlineexceeded" in lower_error:
                # 匹配 bash 工具超时强杀文案
                hint = (
                    "该命令执行被超时强杀。如果它是一个常驻服务（如 server 或 watch），"
                    "请将其转入后台执行（例如使用 `nohup ... &`），不要阻塞主线程。"
                )
            elif "syntax error" in lower_error:
                hint = (
                    "Bash 语法错误。请检查引号转义或特殊字符，"
                    "确保命令在终端中可直接运行。"
                )

        if not hint:
            return raw_error

        return f"{raw_error}\n\n[系统救援指南]: {hint}"
