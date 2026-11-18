# FreeCodeMonkey

#### 介绍
简单的编程智能体
支持多家LLM
没有编码限制（除非LLM自己有）


#### 软件架构

基于 PyQt6 + LangChain 的分层架构，界面与业务逻辑解耦：

```
main.py                         应用入口
app/
├── constants.py                全局常量
├── config/settings.py          QSettings 持久化配置
├── core/
│   ├── provider_types.py       Provider 类型元数据 + LangChain 模型工厂
│   ├── provider_config.py      Provider 配置模型 + JSON 编解码
│   └── provider_manager.py     配置增删查、持久化、连接验证
├── workers/task_worker.py      通用异步任务封装（QThread）
└── ui/
    ├── main_window.py          主窗口（Provider 选择 + 多轮对话）
    ├── widgets/                对话视图、输入区
    └── dialogs/settings_dialog.py  Provider 管理对话框
```

**支持的 Provider 类型**（均基于 LangChain ChatModel）：

| 类型 | 适用对象 | 必填项 |
|------|----------|--------|
| OpenAI 兼容接口 | OpenAI、DeepSeek、通义、智谱、Moonshot 等 | API Key、模型；Base URL 可选 |
| Anthropic | Claude 系列 | API Key、模型 |
| Ollama | 本地部署模型 | 模型；服务地址默认 localhost:11434 |

扩展新类型：在 `provider_types.py` 的 `PROVIDER_TYPES` 中登记字段元数据与模型工厂，设置对话框会自动生成对应表单，无需改动 UI 代码。

配置以 JSON 存于 QSettings（API Key 明文保存于本机当前用户配置），支持多条并存、随时切换。添加时可发送最短测试消息验证连接。会话内保留多轮对话历史（不持久化）。


#### 使用说明

```bash
# 首次运行：创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 启动应用（Windows）：可直接双击 run.bat
.venv\Scripts\python.exe main.py
```

1. 首次使用进入「编辑 → 设置」，点击「添加」创建 Provider：选择类型、填写表单，可先点「测试连接」确认可用，再点「完成」保存。
2. 在窗口顶部「使用模型」下拉框切换已保存的配置。
3. 输入内容后点击「发送」或按 `Ctrl+Enter` 提交；「编辑 → 清空对话」重置多轮历史。

