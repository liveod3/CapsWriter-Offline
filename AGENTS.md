# AGENTS.md

本文件适用于整个仓库。目标是让自动化 Agent 在不破坏本地定制、音频实时性和用户数据的前提下，可靠地理解、修改与验证 CapsWriter-Offline。

## 1. 项目定位

CapsWriter-Offline 是 Windows 10/11 优先的离线语音输入工具。用户通过全局快捷键开始/结束录音；客户端采集音频并通过 WebSocket 发送给服务端；服务端在独立进程中运行 ASR、标点和可选对齐模型；客户端再进行热词、规则、LLM、上屏、字幕和日记处理。

当前代码版本由 `config_templates/config_client_template.py` 与 `config_templates/config_server_template.py` 中的 `__version__` 定义（目前均为 `2.6`），根目录本机配置中的版本字段应与模板同步。README、历史文档或发行说明可能滞后，发生冲突时以代码、当前模板和用户要求为准。

## 2. 仓库地图

- `start_client.py`：客户端冻结/源码入口。
- `start_server.py`：服务端冻结/源码入口，包含 Windows 多进程 `freeze_support()`。
- `config_client.py`：被 Git 忽略的客户端本机配置，程序实际从这里读取；不得覆盖用户取值。
- `config_server.py`：被 Git 忽略的服务端本机配置，程序实际从这里读取；不得覆盖用户取值。
- `config_templates/`：受 Git 跟踪的客户端/服务端默认配置，是配置字段、注释、默认值和版本号的规范源。
- `core/protocol.py`：客户端与服务端共享的 JSON 消息协议。
- `core/client/`：音频采集、快捷键、连接、结果处理、文件转录、LLM、热词、UDP 和托盘管理。
- `core/server/`：WebSocket 服务、任务队列、识别子进程、引擎工厂、合并与格式化。
- `core/ui/`：Tk/托盘/Toast/录音状态提示等共享 UI。
- `core/tools/`：ITN、格式化、窗口检测和其他共享工具。
- `core/server/engines/`：ASR、标点、对齐器及其推理代码。多个 `export/` 与 `gguf/` 目录包含上游/派生代码，修改前先确认代码归属。
- `LLM/`：可热加载的角色配置；这些 `.py` 文件会被执行，不只是静态数据。
- `models/`：模型说明和下载入口；大型模型文件不应提交。
- `docs/`：用户文档、变更日志和历史审计归档；当前待办统一记录在根目录 `TODO.md`。
- `build.spec`、`build-client.spec`、`build_hook.py`：PyInstaller 打包链路。

## 3. 运行与依赖

优先使用用户已经激活的 Python 虚拟环境，不要把某台机器的绝对 Python 路径写入代码或文档。项目当前未声明严格的 Python 版本，也没有锁文件；开始工作前记录 `python --version`。

本机已为本项目准备 Conda 环境 `capswriter`（注意环境名没有连字符），当前使用 Python 3.11。Agent 执行源码、安装检查、`compileall`、pytest 或打包时应优先使用该环境，不要仅因当前 PowerShell 的 `PATH` 中找不到 `conda` 就另建 `.venv` 或向系统 Python 重复安装依赖。优先执行：

```powershell
conda run -n capswriter python --version
conda run -n capswriter python -m compileall -q start_client.py start_server.py config_client.py config_server.py config_templates core LLM
```

若 Agent 的非交互 Shell 没有初始化 Conda，先读取 `$env:USERPROFILE\.conda\environments.txt` 定位已登记的 `capswriter` 环境，再直接调用该环境下的 `python.exe`；不得把解析出的本机绝对路径写入仓库文件。确认该环境确实不存在或不可用后，才考虑创建新环境或安装依赖。

开发环境的基础安装方式：

```powershell
python -m pip install -r requirements-client.txt -r requirements-server.txt
```

源码启动方式：

```powershell
# 仅在根目录本机配置不存在时初始化；不得覆盖已有配置
if (!(Test-Path config_server.py)) { Copy-Item config_templates/config_server_template.py config_server.py }
if (!(Test-Path config_client.py)) { Copy-Item config_templates/config_client_template.py config_client.py }
python start_server.py
python start_client.py
```

注意：

- 服务端启动通常需要与 `config_server.py` 中 `model_type` 对应的完整模型；不要为了普通代码检查自动下载大模型。
- 文件转录依赖系统可用的 `ffmpeg`。
- 全局快捷键、托盘、麦克风、PortAudio、DirectML/Vulkan 和模拟键盘行为需要真实 Windows 桌面会话，CI 或无头环境不能完整覆盖。
- 根目录的 `config_client.py` 与 `config_server.py` 是用户本机配置，默认快捷键、模式和模型可能与模板不同。不要以模板或文档覆盖它们。
- PyInstaller 发行包必须从 `config_templates/` 复制默认配置，不能把开发机根目录的本机配置打入发行包。
- `build/`、`dist/`、`logs/`、年份目录、模型二进制和 `__pycache__/` 是生成物或用户数据，不要纳入普通改动。

## 4. 修改前规则

1. 先执行 `git status --short`，把已有修改视为用户工作。不要重置、覆盖或顺手格式化无关文件。
2. 阅读目标模块、它的调用方、状态对象和配置项；跨客户端/服务端的改动还必须阅读 `core/protocol.py`。
3. 优先做最小、可审查的改动。不要在修复业务问题时批量改写 vendored/export 代码。
4. 保持 UTF-8 和现有中文用户界面风格。面向用户的文档、计划、说明和新增注释优先使用中文。
5. 不得把 API Key、访问令牌、私人音频、识别文本、剪贴板内容、日志或真实模型路径提交到仓库。新增密钥读取时优先使用环境变量或本地未跟踪配置。
6. 配置兼容性是产品能力。新增配置项应有安全默认值，并用 `getattr(..., default)` 或迁移逻辑兼容旧配置/发行包。
7. 修改配置字段、默认值、说明或 `__version__` 时，以 `config_templates/` 中的模板为提交对象；若根目录本机配置存在，还应只合并必要的结构变化并保留用户取值。不得只修改被忽略的根配置，因为这类变化不会进入提交。

## 5. 不可破坏的架构约束

### 音频与线程

- PortAudio/sounddevice 回调必须快速返回；不得在回调线程中执行网络等待、文件重操作、`stream.close()` 或 PortAudio 重初始化。
- 音频流的 `start`、`stop`、`reopen`、设备监控和闲置挂起会并发发生。共享生命周期状态必须由明确初始化的锁或单一状态机保护，并避免重复重启线程。
- 不要依赖 `sounddevice` 的私有属性（如 `_ffi`、`_lib`）做常规生命周期管理；若确有必要，必须隔离版本、失败路径和 Windows 实机回归测试。
- 跨线程向 asyncio 事件循环提交工作时使用线程安全入口，并处理事件循环已关闭的情况。

### asyncio、WebSocket 与多进程

- 事件循环中不得直接执行阻塞 I/O 或 CPU 密集推理。推理继续留在识别子进程，阻塞队列读取需在线程执行器中完成。
- `Task`、`Result` 及跨进程状态必须保持可 pickle；不要把 WebSocket、Tk 对象、锁或本地闭包放进多进程队列。
- 任务必须同时按 `socket_id` 和 `task_id` 隔离。同一连接上的文件转录、麦克风任务或未来并发请求不得共享音频缓存。
- 修改协议字段时，同时更新序列化、反序列化、客户端发送、服务端接收、服务端返回、客户端处理和兼容默认值。
- 网络输入是不可信数据。校验消息类型、必填字段、枚举、Base64、音频字节对齐、切片范围和消息/会话大小；不要使用无限制缓存作为默认方案。

### UI 与输入

- Tk 对象只能在 Tk 所属线程创建和修改；其他线程通过 `root.after(...)` 调度。
- 全局快捷键回调不应等待 Tk root、网络或模型。录音指示、托盘状态和实际 `ClientState.recording` 必须在开始、取消、失败和完成路径保持一致。
- 修改 suppress/hold/toggle 键逻辑时，必须验证短按、长按、重复按键、自动重复、暂停后首次触发、退出清理和管理员窗口输入。

### 数据与隐私

- 本项目默认可能保存原始音频、日记和包含识别文本的日志；任何新增采集或外发行为都必须显式配置、可关闭、可解释。
- 联网 LLM 角色可能发送语音转写、热词、历史和选中文本。新增 Provider 或上下文时，不得破坏“本地模式不联网”的可验证边界。
- 日志中避免记录 API Key、完整提示词、完整选区和不必要的完整识别内容；诊断字段优先记录长度、任务 ID 前缀、耗时和错误类型。

## 6. 验证要求

仓库当前没有正式测试套件或 CI。每次改动至少执行与范围匹配的检查，并在交付时说明没有执行的项目及原因。

无模型、无 GUI 也能执行的最低检查：

```powershell
python -m compileall -q start_client.py start_server.py config_client.py config_server.py config_templates core LLM
git diff --check
git status --short
```

如果新增了 pytest 测试并安装了开发依赖：

```powershell
python -m pytest -q
```

按改动范围补充验证：

- 协议/网络：有效消息、畸形 JSON、非法枚举、非法 Base64、超限消息、断线清理、多个 task 并发。
- 调度/合并：同 task FIFO、跨 task 无饥饿、断连任务清理、最终片段只完成一次。
- 音频：启动/停止幂等、暂停/恢复、默认设备切换、设备拔插、异常结束、退出时无重启、短录音取消。
- UI/托盘：主屏/副屏、不同 DPI、Toast 与录音提示不重叠、应用退出后无线程继续访问 Tk。
- LLM：角色热加载、Provider 路由、空 Key 错误、停止输出、历史隔离、选区恢复、离线模式无外网请求。
- 打包：源码运行通过后，再按需验证 PyInstaller；不要把 `dist/` 或大模型加入提交。

运行真实麦克风、模拟全局按键、联网 LLM、GPU 管理命令、模型下载或完整打包前，先确认环境和用户意图。这些操作可能抢占输入、访问网络、需要管理员权限或耗费大量资源。

## 7. 测试建设约定

新增测试优先放在 `tests/`，目录结构与 `core/` 对应。当前 `.gitignore` 会忽略 `test_*.py`，在建设测试套件时应先修正该规则；不要用强制添加绕过一个错误的长期配置。

优先覆盖纯函数和边界层：

1. `core/protocol.py` 的协议解析与校验。
2. `core/server/merger/` 和 `core/tools/` 的确定性算法。
3. `TaskBuffer` 的 FIFO/公平性和断连清理。
4. 音频流生命周期状态机（mock sounddevice）。
5. LLM 角色解析、消息组装与敏感信息边界（mock Provider）。

测试不得依赖真实 API Key、真实个人音频或已安装的大模型。小型音频 fixture 使用合成静音/正弦波，并明确采样率与 dtype。

## 8. 已知高风险区域

- `core/client/audio/stream.py`：PortAudio 生命周期、设备监控和多线程竞态。
- `core/client/app.py`、`core/client/shortcut/`：闲置挂起、暂停恢复与录音状态竞态。
- `core/server/connection/`：当前协议的认证、输入上限和会话隔离不足。
- `core/server/worker/task_handler.py`：调度语义直接影响实时听写延迟和文件任务公平性。
- `core/client/llm/` 与 `LLM/`：动态执行角色文件、云端数据外发和密钥管理。
- `core/server/engines/*/export/`：体积大、重复度高，含上游派生代码；避免无边界的全仓格式化。

当前待办和优先级见 `TODO.md`；历史现状与证据见 `docs/archive/PROJECT_AUDIT_REPORT-2026-08-08.md`。

## 9. 交付清单

Agent 完成任务前应确认：

- 只修改了请求范围内的文件，已有用户改动仍然存在。
- 新增行为有配置默认值、失败路径和退出清理。
- 线程/事件循环/多进程/Tk 边界没有被跨越。
- 没有提交密钥、日志、音频、日记、模型或打包生成物。
- 已执行最低语法和 diff 检查，或清楚说明阻碍。
- 最终回复列出修改文件、主要结果、验证结果和仍需实机验证的内容。
