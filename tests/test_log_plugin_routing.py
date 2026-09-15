"""插件日志文件路由测试。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.runtime.log import LoggerManager, logger


class CapturingLogWriter:
    """记录日志写入目标，避免测试访问真实文件系统。"""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, Path]] = []

    def write_log(
        self, level: str, message: str, file_path: Path, exc_info: Any = None
    ) -> None:
        """保存单条日志的级别、内容和目标路径。"""
        self.entries.append((level, message, file_path))

    @staticmethod
    def shutdown() -> bool:
        """测试写入器没有待释放资源。"""
        return True


def test_virtual_plugin_routes_log_by_runtime_module_identity(monkeypatch, tmp_path):
    """共享物理源码的虚拟实例应按运行实例 ID 写入独立日志文件。"""
    writer = CapturingLogWriter()
    monkeypatch.setattr(LoggerManager, "_writer", writer)
    monkeypatch.setattr(LoggerManager, "_log_path", tmp_path)
    monkeypatch.setattr(
        LoggerManager,
        "_get_console_logger",
        classmethod(
            lambda _cls, _logfile: SimpleNamespace(info=lambda *_args, **_kwargs: None)
        ),
    )
    namespace = {
        "__name__": "app.plugins.mediawarp1",
        "logger": logger,
    }
    source = compile(
        "def emit_log():\n    logger.info('virtual instance started')\n",
        "/config/app/plugins/mediawarp/__init__.py",
        "exec",
    )
    exec(source, namespace)

    namespace["emit_log"]()

    assert writer.entries == [
        (
            "INFO",
            "mediawarp - virtual instance started",
            tmp_path / "plugins" / "mediawarp1.log",
        )
    ]


def test_file_log_route_preserves_exc_info(monkeypatch, tmp_path):
    """异常上下文必须随文件日志路径透传，使文件日志也能保留堆栈。"""
    received: list = []

    class _ExcInfoWriter:
        def write_log(self, level, message, file_path, exc_info=None):
            received.append((level, message, file_path, exc_info))

        @staticmethod
        def shutdown() -> bool:
            return True

    monkeypatch.setattr(LoggerManager, "_writer", _ExcInfoWriter())
    monkeypatch.setattr(LoggerManager, "_log_path", tmp_path)
    monkeypatch.setattr(
        LoggerManager,
        "_get_console_logger",
        classmethod(
            lambda _cls, _logfile: SimpleNamespace(
                info=lambda *_a, **_k: None, error=lambda *_a, **_k: None
            )
        ),
    )

    captured = ValueError("boom")
    logger.error("task failed", exc_info=captured)

    assert received, "文件写入器未收到日志"
    assert received[0][3] is captured, "exc_info 未随文件日志路径透传"
