"""
gcjp/debug_logger.py
可控的调试日志工具。

用法：
    from gcjp.debug_logger import debug

    # 默认 VERBOSE=False，不输出到控制台
    debug.log("[DEBUG] 阶段 1: 添加任务节点")

    # 开启控制台输出
    debug.set_verbose(True)
    debug.log("现在可以看到这条消息了")

    # 查看历史日志
    for msg in debug.get_logs():
        print(msg)

    # 清空日志（可选）
    debug.clear_logs()
"""

from __future__ import annotations


class DebugLogger:
    """可控的调试日志器。

    VERBOSE=True   → log() 同时输出到控制台
    VERBOSE=False  → log() 只写入内部缓存，不打印
    """

    def __init__(self, verbose: bool = False):
        self.VERBOSE = verbose
        self._logs: list[str] = []

    # ── 日志方法 ──────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        """记录一条日志。VERBOSE=True 时同步打印到控制台。"""
        if self.VERBOSE:
            print(msg)
        self._logs.append(msg)

    def log_banner(self, msg: str, width: int = 60, char: str = "-") -> None:
        """输出分隔横幅。"""
        self.log(f"\n{char * width}")
        self.log(msg)
        self.log(f"{char * width}")

    # ── 开关与查询 ────────────────────────────────────────────────────────

    def set_verbose(self, enabled: bool) -> None:
        """动态切换是否输出到控制台。"""
        self.VERBOSE = enabled

    def get_logs(self) -> list[str]:
        """返回所有历史日志（含未打印到控制台的）。"""
        return list(self._logs)

    def clear_logs(self) -> None:
        """清空历史日志缓存。"""
        self._logs.clear()


# 全局单例，所有模块共享
debug = DebugLogger()
