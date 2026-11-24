"""设置对话框：管理已保存的 LLM Provider。

支持新增、删除、编辑 provider，并在保存前异步验证连接可用性。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core import (
    ConnectionError,
    ProviderConfig,
    ProviderManager,
    PROVIDER_TYPES,
    get_spec,
)
from app.ui.theme import set_state
from app.workers import run_in_thread


class SettingsDialog(QDialog):
    """Provider 管理对话框。"""

    def __init__(self, manager: ProviderManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._field_edits: dict[str, QLineEdit] = {}
        self._editing_id: str = ""
        self._active_handles: list = []

        self.setWindowTitle("设置 — LLM Provider")
        self.setMinimumSize(720, 480)
        self._build_ui()
        self._connect_signals()
        self._load_list()
        self._select_first()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        # 左侧：已保存列表
        self._list = QListWidget()
        self._add_button = QPushButton("添加")
        self._delete_button = QPushButton("删除")
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(self._add_button)
        list_buttons.addWidget(self._delete_button)

        left = QVBoxLayout()
        left.addWidget(QLabel("已保存的配置"))
        left.addWidget(self._list)
        left.addLayout(list_buttons)

        # 右侧：类型选择 + 动态表单
        self._type_combo = QComboBox()
        for key, spec in PROVIDER_TYPES.items():
            self._type_combo.addItem(spec.label, key)

        self._form_layout = QFormLayout()
        self._hint_label = QLabel("选择或添加一个配置以编辑")
        self._hint_label.setWordWrap(True)

        self._test_button = QPushButton("测试连接")
        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)

        right = QVBoxLayout()
        right.addWidget(QLabel("类型"))
        right.addWidget(self._type_combo)
        right.addLayout(self._form_layout)
        right.addWidget(self._test_button, alignment=Qt.AlignmentFlag.AlignLeft)
        right.addWidget(self._test_result)
        right.addStretch(1)

        columns = QHBoxLayout()
        columns.addLayout(left, 1)
        columns.addLayout(right, 2)

        self._done_button = QPushButton("完成")
        self._done_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(columns)
        layout.addWidget(self._done_button, alignment=Qt.AlignmentFlag.AlignRight)

    def _connect_signals(self) -> None:
        self._list.currentItemChanged.connect(self._on_item_changed)
        self._add_button.clicked.connect(self._on_add)
        self._delete_button.clicked.connect(self._on_delete)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._test_button.clicked.connect(self._on_test)

    # ---- 列表与表单 ----
    def _load_list(self) -> None:
        self._list.clear()
        for config in self._manager.providers:
            item = QListWidgetItem(f"{config.name}  [{get_spec(config.type).label}]")
            item.setData(Qt.ItemDataRole.UserRole, config.id)
            self._list.addItem(item)

    def _select_first(self) -> None:
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._clear_form()

    def _build_form(self, type_key: str) -> None:
        """根据类型元数据重建表单控件。"""
        self._clear_form_widgets()
        spec = get_spec(type_key)
        for field in spec.fields:
            edit = QLineEdit()
            edit.setPlaceholderText(field.placeholder)
            edit.setText(field.default)
            if field.secret:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._field_edits[field.name] = edit
            label = field.label + (" *" if field.required else "")
            self._form_layout.addRow(label, edit)

    def _clear_form_widgets(self) -> None:
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._field_edits.clear()

    def _clear_form(self) -> None:
        self._clear_form_widgets()
        self._editing_id = ""
        self._hint_label.setText("点击「添加」创建一个新配置")
        self._test_result.setText("")

    def _set_form_values(self, config: ProviderConfig) -> None:
        for name, edit in self._field_edits.items():
            edit.setText(config.values.get(name, ""))

    def _collect_form_values(self) -> dict[str, str]:
        return {name: edit.text().strip() for name, edit in self._field_edits.items()}

    def _current_config(self) -> ProviderConfig | None:
        """用当前表单构造配置（不落盘）。"""
        type_key = self._type_combo.currentData()
        values = self._collect_form_values()
        if self._editing_id:
            return ProviderConfig(id=self._editing_id, type=type_key, values=values)
        return ProviderConfig.create(type_key, values)

    # ---- 槽函数 ----
    @pyqtSlot(QListWidgetItem, QListWidgetItem)
    def _on_item_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        # 切换前先把旧表单内容写回旧配置
        self._commit_form()
        if current is None:
            self._clear_form()
            return
        config_id = current.data(Qt.ItemDataRole.UserRole)
        config = self._manager.get(config_id)
        if config is None:
            return
        self._editing_id = config.id
        index = self._type_combo.findData(config.type)
        if index >= 0:
            self._type_combo.setCurrentIndex(index)
        self._build_form(config.type)
        self._set_form_values(config)
        self._type_combo.setEnabled(False)
        self._hint_label.setText(f"正在编辑：{config.name}")
        self._test_result.setText("")

    def _on_type_changed(self) -> None:
        if not self._editing_id:
            self._build_form(self._type_combo.currentData())

    def _on_add(self) -> None:
        self._commit_form()
        # 新建时默认使用当前下拉框选中的类型
        type_key = self._type_combo.currentData()
        self._build_form(type_key)
        self._editing_id = ""
        self._type_combo.setEnabled(True)
        self._list.setCurrentRow(-1)
        self._hint_label.setText(f"新建 {get_spec(type_key).label} 配置，填写后点击「完成」保存")
        self._test_result.setText("")

    def _on_delete(self) -> None:
        current = self._list.currentItem()
        if current is None:
            return
        config_id = current.data(Qt.ItemDataRole.UserRole)
        config = self._manager.get(config_id)
        if config is None:
            return
        self._manager.remove(config_id)
        self._editing_id = ""
        self._load_list()
        self._select_first()

    @pyqtSlot()
    def _on_test(self) -> None:
        config = self._current_config()
        if config is None:
            return
        self._test_button.setDisabled(True)
        self._test_result.setText("正在验证连接…")
        handle = run_in_thread(self._manager.test_connection, config)
        self._active_handles.append(handle)
        handle.worker.result.connect(lambda text: self._on_test_result(text, handle))
        handle.worker.failed.connect(lambda msg: self._on_test_failed(msg, handle))

    def _on_test_result(self, text: str, handle) -> None:
        self._test_result.setText(f"连接成功。模型回复：{text}")
        set_state(self._test_result, "success")
        self._finish_test(handle)

    def _on_test_failed(self, message: str, handle) -> None:
        self._test_result.setText(f"连接失败：{message}")
        set_state(self._test_result, "danger")
        self._finish_test(handle)

    def _finish_test(self, handle) -> None:
        self._test_button.setDisabled(False)
        if handle in self._active_handles:
            self._active_handles.remove(handle)

    def _commit_form(self) -> None:
        """把当前表单内容写回到正在编辑的配置。"""
        if not self._editing_id or not self._field_edits:
            return
        config = self._manager.get(self._editing_id)
        if config is None:
            return
        config.values.update(self._collect_form_values())

    def accept(self) -> None:
        """保存当前表单（若为新建则写入）并关闭对话框。"""
        if self._editing_id:
            self._commit_form()
        elif self._field_edits:
            # 新建配置：表单有内容才保存
            config = self._current_config()
            if config is not None:
                try:
                    config.validate()
                except ValueError as exc:
                    self._test_result.setText(f"请完善必填项：{exc}")
                    set_state(self._test_result, "danger")
                    return
                self._manager.add(config)
        super().accept()
