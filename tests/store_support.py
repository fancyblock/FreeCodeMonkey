"""测试用的会话存储工厂。

会话按工作区存放之后，默认工厂会把文件写进工作区目录——测试里工作区本来就是
临时目录，天然隔离。但「旧全局文件」和「迁移台账」的默认位置在真实的应用数据
目录，直接跑会读到开发机上的真实会话，所以测试一律用这份工厂把三处路径都关进
临时目录里。
"""

from __future__ import annotations

from pathlib import Path

from app.ui.sessions import SessionStore


def workspace_store_factory(base: Path | str):
    """返回一个 ``root -> SessionStore`` 工厂，所有落点都在 ``base`` 下。"""
    base = Path(base)

    def factory(root: Path | str) -> SessionStore:
        root = Path(root)
        return SessionStore(
            root,
            legacy_path=base / "legacy.json",
            fallback_path=base / "fallback" / f"{root.name}.json",
            ledger_path=base / "legacy_claims.json",
        )

    return factory
