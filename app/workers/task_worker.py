"""通用异步任务封装。

采用 QObject.moveToThread 模式：每次调用创建一个 QThread，
任务函数在该线程执行，结果通过信号回传界面线程。

句柄生命周期是这里唯一的坑：WorkerHandle 一旦被回收，PyQt 会连同它持有的
QThread 一起销毁；若此时线程还在跑，Qt 会直接 abort 掉整个进程（表现为
窗口毫无征兆地消失，且没有 Python 异常）。因此所有句柄都额外登记在模块级
集合里，只有在「线程确实结束」之后才允许释放。
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


@dataclass(eq=False)
class WorkerHandle:
    """一次后台任务的句柄。eq=False 保证按身份比较且可哈希。"""

    thread: QThread
    worker: FunctionWorker


# 兜底注册表：只要还在跑，就保证有强引用
_ACTIVE_HANDLES: set[WorkerHandle] = set()


def run_in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> WorkerHandle:
    """启动后台任务并返回句柄。

    调用方连接 handle.worker.result / failed 获取结果，并在
    handle.thread.finished 之后调用 release_handle 释放句柄。
    """
    _sweep_finished()

    thread = QThread()
    worker = FunctionWorker(fn, *args, **kwargs)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    worker.result.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)

    handle = WorkerHandle(thread, worker)
    _ACTIVE_HANDLES.add(handle)
    thread.start()
    return handle


def release_handle(handle: WorkerHandle) -> None:
    """释放句柄。必须在线程结束（thread.finished）之后调用。"""
    _ACTIVE_HANDLES.discard(handle)


def _sweep_finished() -> None:
    """清理上一批已结束的句柄，防止长时间运行累积。"""
    for handle in list(_ACTIVE_HANDLES):
        try:
            if handle.thread.isFinished():
                _ACTIVE_HANDLES.discard(handle)
        except RuntimeError:
            # C++ 对象已被销毁，直接从注册表移除
            _ACTIVE_HANDLES.discard(handle)
