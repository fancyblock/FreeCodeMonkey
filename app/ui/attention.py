"""任务栏闪烁：把用户叫回窗口。

声音只能提醒坐在电脑前的人，窗口被别的程序挡住时也不一定听得见；任务栏按钮
闪烁才是「有事找你」最直接的信号。会话跑完、卡在危险操作确认、或者任务中途
失败，除了响一声，窗口也该闪起来。

只在应用不在前台时闪：正看着窗口的人不需要提醒，标题栏跟着闪反而碍事。
Windows 用 ``FlashWindowEx`` 一直闪到窗口回到前台（系统自己会停）；其它平台
退回 Qt 的 alert：macOS 会跳 Dock，支持 ``_NET_WM_STATE_DEMANDS_ATTENTION``
的窗口管理器会给任务栏项加标记。
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

# Windows 用原生 FlashWindowEx；其它平台退回 Qt 的 alert
_WINDOWS = sys.platform == "win32"

# FlashWindowEx 的旗标（WinUser.h）
_FLASHW_STOP = 0x00000000
_FLASHW_ALL = 0x00000003        # 标题栏和任务栏按钮一起闪
_FLASHW_TIMERNOFG = 0x0000000C  # 持续闪，直到窗口回到前台


def flash(window: QWidget) -> bool:
    """让窗口在任务栏上闪起来，直到用户回到它；返回是否发起了闪烁。"""
    if not window.isVisible() or _is_foreground(window):
        return False
    if _WINDOWS:
        return _flash_windows(window, _FLASHW_ALL | _FLASHW_TIMERNOFG)
    return _alert_qt(window)


def stop(window: QWidget) -> None:
    """停止闪烁：等待确认已经失效，别让任务栏一直招人。

    其它平台不需要手动收尾——Qt 的 alert 在窗口被激活时自动结束。
    """
    if _WINDOWS:
        _flash_windows(window, _FLASHW_STOP)


def _is_foreground(window: QWidget) -> bool:
    """用户是不是正看着这个应用。

    看整个应用而不是单个窗口：设置对话框之类也算「正看着」，用户就在里面，
    这时再闪只是打扰。最小化则不算——那正是最需要提醒的时候。
    """
    if window.isMinimized():
        return False
    return _app_is_active()


def _app_is_active() -> bool:
    """单独抽出来，测试可以替换它模拟「用户在看别的程序」。"""
    return QApplication.applicationState() == Qt.ApplicationState.ApplicationActive


def _alert_qt(window: QWidget) -> bool:
    """非 Windows：交给平台自己请求注意；msec=0 表示一直提示到窗口被激活。"""
    handle = window.windowHandle()
    if handle is None:
        return False
    handle.alert(0)
    return True


def _flash_windows(window: QWidget, flags: int) -> bool:
    """调用 FlashWindowEx；拿不到原生句柄时安静返回 False。"""
    try:
        import ctypes
        from ctypes import wintypes

        # 结构体只有 Windows 用得上，放在这里免得别的平台导入 ctypes.wintypes
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        hwnd = int(window.winId())
        if not hwnd:
            return False
        info = FLASHWINFO(
            cbSize=ctypes.sizeof(FLASHWINFO),
            hwnd=hwnd,
            dwFlags=flags,
            uCount=0,     # 带 TIMERNOFG 时由系统决定闪多久
            dwTimeout=0,  # 0 = 用系统的闪烁频率
        )
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:  # noqa: BLE001 - 提醒是锦上添花，不能反过来打断界面
        return False
    return True
