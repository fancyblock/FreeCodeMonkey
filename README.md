# FreeCodeMonkey

#### 介绍

一个跑在本机的编程智能体：能读懂你的项目、改代码、跑命令，并把过程实时显示出来。
支持多家 LLM，没有额外订阅限制。写文件和执行命令**逐次征得你同意**。

#### 工作方式

```
用户任务 ──▶ 模型推理 ──▶ 工具调用 ──┬─ 只读 ──▶ 直接执行
                                    └─ 写/执行 ──▶ 弹窗确认 ──▶ 执行
                                            ▲              │
                                            └── 结果回灌 ───┘
                                                   │
                                          无工具调用 ──▶ 最终回答
```

- ReAct 循环，单轮最多 25 步；同一操作连续失败两次，提示词要求模型换思路。
- **Plan / Build**：输入框左侧可切换模式，Plan 只暴露只读工具（读文件、列目录、搜代码），先出修改计划；Build 才能改文件、跑命令。
- **上下文占用条**：发送按钮左侧实时显示本次请求的估算 token 占模型上下文窗口的比例，≥60% 变橙、≥85% 变红。
- 每次工具调用都以步骤行形式出现在对话区，你能看清它在做什么。
- 左侧列表按顺序记录本会话的每条提示词，点击即可跳回对话区对应位置并高亮。
- 随时可以点「停止」，在两个步骤之间生效。

#### 软件架构

基于 PyQt6 + LangChain 的分层架构，界面与业务逻辑解耦：

```
main.py                          应用入口
app/
├── constants.py                 全局常量
├── config/settings.py           QSettings 持久化配置（含工作区路径）
├── core/                        LLM Provider 管理（与 agent 无关）
│   ├── provider_types.py        类型元数据 + LangChain 模型工厂
│   ├── provider_config.py       配置模型 + JSON 编解码
│   └── provider_manager.py      增删查、持久化、连接验证
├── agent/                       ★ 编程智能体（不依赖 Qt，可无界面运行）
│   ├── events.py                事件定义（步骤/工具/结果/结束）
│   ├── approval.py              审批协议 + 测试用放行/拒绝通道
│   ├── tools.py                 5 个工具与工作区路径约束
│   ├── prompts.py               系统提示词（行为纪律）
│   └── runtime.py               ReAct 循环
├── workers/task_worker.py       通用异步任务封装（QThread）
└── ui/
    ├── main_window.py           装配：工作区 + Provider + 事件渲染
    ├── agent_bridge.py          跨线程审批通道（工作线程 ↔ 界面线程）
    ├── widgets/                 对话视图（含步骤流）、提示词列表、输入区
    └── dialogs/                 Provider 设置、危险操作确认
```

关键边界：`app/agent` 里没有一行 Qt 代码。循环只依赖两个注入项——事件回调与审批通道。
界面负责把事件画出来、把审批弹出来，其余都在 agent 侧，因此可以脱离 GUI 直接测试。

#### 可用工具

| 工具 | 作用 | 需要审批 |
|------|------|----------|
| `read_file` | 读文本文件，带行号，单次最多 400 行，支持分段 | 否 |
| `list_dir` | 列目录结构，最多 3 层，自动跳过 `.git`、`.venv`、`node_modules` 等 | 否 |
| `grep` | 正则搜索文件内容，支持 `glob` 过滤与上下文行 | 否 |
| `edit_file` | 精确字符串替换，`old_string` 必须唯一 | **是** |
| `run_command` | 在工作区根目录执行 shell 命令（Windows 为 cmd） | **是** |

`edit_file` 兼做新建：文件不存在且 `old_string` 传空串时，按 `new_string` 创建文件。

#### 安全边界

- **路径限制**：所有路径解析后必须落在工作区内，越界直接返回错误文本，不会执行。
- **Plan 模式只读**：Plan 只注册读类工具，写文件与执行命令在模型侧不可见；runtime 另有一道拦截，模型强行调用也会被拒绝且不触发审批。
- **上下文预算**：单次请求正文超过 40k 字符时，从最旧的工具输出开始替换成占位提示（模型可重新调用工具取回），不删除消息，避免工具调用链失配。
- **审批**：写文件和执行命令每次弹窗，默认按钮是「拒绝」；勾选「本会话内不再询问」后同类放行。
- **拒绝可反馈**：拒绝结果会作为工具消息回灌，模型知道被拒并会换方案，而不是假装成功。
- **输出截断**：工具返回最长 12000 字符，grep 最多 80 行，避免撑爆上下文。
- **超时**：命令默认 60 秒、上限 600 秒，超时强制终止。

没有沙箱：`run_command` 的能力等同于你在终端里的权限。审批弹窗是唯一的闸门。

#### 支持的 Provider 类型

| 类型 | 适用对象 | 必填项 |
|------|----------|--------|
| OpenAI 兼容接口 | OpenAI、DeepSeek、通义、智谱、Moonshot 等 | API Key、模型；Base URL 可选 |
| Anthropic | Claude 系列 | API Key、模型 |
| Ollama | 本地部署模型 | 模型；服务地址默认 localhost:11434 |

每种类型的表单都有可选的「上下文窗口（tokens）」字段，用于计算进度条百分比；留空时按类型默认值（128k / 200k / 32k）。

扩展新类型：在 `provider_types.py` 的 `PROVIDER_TYPES` 中登记字段元数据与模型工厂，
设置对话框会自动生成对应表单，无需改动 UI 代码。

配置以 JSON 存于 QSettings（API Key 明文保存在本机当前用户配置下），支持多条并存。

> 工具调用依赖模型的 function calling 能力。不支持 `bind_tools` 的模型会直接报错并提示。

#### 使用说明

```bash
# 首次运行：创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 启动应用（Windows）：可直接双击 run.bat
.venv\Scripts\python.exe main.py
```

1. 进入「编辑 → 设置」，点「添加」创建 Provider，选类型、填表单，可先「测试连接」再保存。
2. 顶部「使用模型」切换配置；「工作区」点「选择目录…」指定 agent 可以操作的项目根目录。
3. 在下方描述任务，`Ctrl+Enter` 发送。先选 **Plan** 让它只读分析并给出修改计划，确认后再切到 **Build** 让它动手；agent 会先探索再动手，危险操作会弹窗问你。
4. 「编辑 → 清空对话」重置多轮上下文。

#### 测试

```bash
.venv\Scripts\python.exe tests\test_agent_loop.py    # 循环与工具，用假模型，不需要 API Key
.venv\Scripts\python.exe tests\test_ui_smoke.py      # 界面装配，offscreen，不需要显示器
.venv\Scripts\python.exe tests\test_ui_run.py        # 端到端：线程 → 审批 → 回灌 → 渲染
```

`test_ui_run.py` 里唯一被替换的是模型和确认弹窗，线程、跨线程信号、事件渲染都是真的。
它覆盖了「允许修改」和「拒绝修改」两条路径。

#### 已知限制

- 没有 token 级流式输出：中间步骤实时可见，但最终回答是一次性出现的。
- 对话历史只存在内存里，关闭即失；超过 40 条会自动截断。
- 没有沙箱与网络白名单，安全全靠审批。
- `langgraph` 已在依赖中但未使用，保留给将来需要多 agent 或断点续跑时替换 `runtime.py`。

#### 两个踩过的坑（改代码前先读）

1. **`QThread` 句柄不能提前释放**：`WorkerHandle` 一旦被回收，PyQt 会连同运行中的 `QThread`
   一起销毁，Qt 直接 abort 整个进程——没有 Python 异常，窗口凭空消失。所以句柄只在
   `thread.finished` 之后释放，`task_worker` 里还有一层模块级注册表兜底。
2. **信号不能命名为 `event`**：它会遮蔽 `QObject.event()`。同线程直连还能跑，
   一旦跨线程队列投递就会抛 `TypeError: native Qt signal is not callable` 并 abort。
   所以叫 `agent_event`。
