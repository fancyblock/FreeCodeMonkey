"""界面提示音：任务完成、需要确认。

用 ``QMediaPlayer`` 而不是 ``QSoundEffect``：后者只支持未压缩音频，喂 mp3
会一直停在 Loading 状态、静默不出声。

每种音效单独一个播放器，各有各的播放状态，互不打断——任务刚好完成的同时
又弹出下一个确认框时，两声都该听得见。播放器是模块级单例且不回收：
``QMediaPlayer`` 被回收会中断正在播的声音，反复创建也等于反复打开音频设备。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl

# 音效文件名；sound/ 目录在仓库根
COMPLETION_SOUND = "mission_complete_strong_2s.mp3"
CONFIRM_SOUND = "question_confirmation_strong_1_5s.mp3"

# app/ui/sound.py 往上三层就是仓库根
_SOUND_DIR = Path(__file__).resolve().parents[2] / "sound"

# 文件名 → 播放器；创建失败时记 None，避免每次调用都重试一遍
_players: dict[str, object] = {}
_broken: set[str] = set()


def sound_path(name: str) -> Path:
    """音效文件的绝对路径。"""
    return _SOUND_DIR / name


def _ensure_player(name: str):
    """按文件名惰性创建播放器；环境不支持时返回 None 并记住。"""
    if name in _players:
        return _players[name]
    if name in _broken:
        return None
    try:
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    except ImportError:
        # 精简安装可能没带 QtMultimedia，静默降级而不是让调用方炸掉
        _broken.add(name)
        return None
    player = QMediaPlayer()
    output = QAudioOutput()
    player.setAudioOutput(output)
    # 音频输出对象必须跟着播放器一起存活
    player._audio_output = output  # type: ignore[attr-defined]
    _players[name] = player
    return player


def is_available(name: str = COMPLETION_SOUND) -> bool:
    """音效文件存在且播放器可用。"""
    return sound_path(name).is_file() and _ensure_player(name) is not None


def play(name: str) -> bool:
    """播放指定音效；返回是否真的发起了播放。

    没有音频设备（例如离屏跑测试）时 Qt 不会报错，只是静默不响，所以这里
    不把「有没有出声」当作失败。
    """
    path = sound_path(name)
    if not path.is_file():
        return False
    player = _ensure_player(name)
    if player is None:
        return False
    player.setSource(QUrl.fromLocalFile(str(path)))
    # 上一轮播完会停在末尾，不复位的话第二次就听不到了
    player.setPosition(0)
    player.play()
    return True


def play_completion() -> bool:
    """任务完成的提示音。"""
    return play(COMPLETION_SOUND)


def play_confirmation() -> bool:
    """弹出需要用户确认的对话框时的提示音。"""
    return play(CONFIRM_SOUND)


def reset() -> None:
    """释放全部播放器；测试用来隔离状态，正常运行时不需要调用。"""
    for player in _players.values():
        if player is not None:
            player.stop()
            player.deleteLater()
    _players.clear()
    _broken.clear()
