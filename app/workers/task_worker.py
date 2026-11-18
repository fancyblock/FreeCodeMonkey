"""通用异步任务封装。

采用 QObject.moveToThread 模式：每次调用创建一个 QThread，
任务函数在该线程执行，结果通过信号回传界面线程。
线程与 Worker 均由 Qt 父子机制自动清理，无需手动管理生命周期。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot


class FunctionWorker(QObject):
    """在后台线程执行任意可调用对象，并以信号返回结果或异常。"""

    result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.result.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:  # 捕获所有异常，避免后台线程崩溃
            self.failed.emit(f"{type(exc).__name__}: {exc}")


@dataclass
class WorkerHandle:
    """一次后台任务的句柄。调用方需保持引用直至任务结束。"""

    thread: QThread
    worker: FunctionWorker


def run_in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> WorkerHandle:
    """启动后台任务并返回句柄。

    任务完成（成功或失败）后线程自动退出并释放资源。
    调用方连接 handle.worker.result / failed 获取结果。
    """
    thread = QThread()
    worker = FunctionWorker(fn, *args, **kwargs)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    worker.result.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    return WorkerHandle(thread, worker)
