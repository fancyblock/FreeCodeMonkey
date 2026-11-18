"""设置对话框：配置 LLM 提供者、API Key 与模型。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import Settings
from app.core import ProviderRegistry


class SettingsDialog(QDialog):
    """LLM 配置对话框。点击确定后写回 Settings。"""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("设置")
        self.setMinimumWidth(420)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        self._provider_combo = QComboBox()
        display_names = ProviderRegistry.display_names()
        for name, label in display_names.items():
            self._provider_combo.addItem(label, name)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("留空则不修改")

        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("例如 gpt-4o-mini")

        form = QFormLayout()
        form.addRow("提供者:", self._provider_combo)
        form.addRow("API Key:", self._api_key_edit)
        form.addRow("模型:", self._model_edit)

        self._save_button = QPushButton("保存")
        self._save_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            self._save_button, alignment=Qt.AlignmentFlag.AlignRight
        )

    def _load(self) -> None:
        index = self._provider_combo.findData(self._settings.provider)
        if index >= 0:
            self._provider_combo.setCurrentIndex(index)
        self._api_key_edit.setText(self._settings.api_key)
        self._model_edit.setText(self._settings.model)

    def accept(self) -> None:
        """将界面值写回配置并落盘。"""
        self._settings.provider = self._provider_combo.currentData()
        key = self._api_key_edit.text()
        if key:
            self._settings.api_key = key
        self._settings.model = self._model_edit.text().strip()
        self._settings.sync()
        super().accept()
