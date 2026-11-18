# FreeCodeMonkey

#### 介绍
简单的编程智能体
支持多家LLM
没有编码限制（除非LLM自己有）


#### 软件架构

基于 PyQt6 的分层架构，界面与业务逻辑解耦：

```
main.py                     应用入口
app/
├── constants.py            全局常量
├── config/settings.py      QSettings 持久化配置
├── core/llm_provider.py    LLM 抽象基类 + 提供者注册表
├── workers/task_worker.py  通用异步任务封装（QThread）
└── ui/
    ├── main_window.py      主窗口（串联各层）
    ├── widgets/            对话视图、输入区
    └── dialogs/            设置对话框
```

扩展新的 LLM：继承 `LLMProvider` 实现 `generate()`，用 `@ProviderRegistry.register` 注册即可，界面代码无需改动。当前内置 `EchoProvider` 用于验证链路，尚未接入真实 API。


#### 使用说明

```bash
# 首次运行：创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 启动应用（Windows）
.venv\Scripts\python.exe main.py
```

在输入框输入内容后点击「发送」或按 `Ctrl+Enter` 提交。「编辑 → 设置」中配置 LLM 提供者、API Key 与模型。

