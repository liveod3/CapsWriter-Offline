# AGENTS.md

本文件适用于整个仓库。目标是让自动化 Agent 在不破坏本地定制、音频实时性和用户数据的前提下，可靠地理解、修改与验证 CapsWriter-Offline。

本文件是仓库级 Agent 公共规则的维护入口；[CLAUDE.md](CLAUDE.md) 提供阅读导航，不另行维护一套环境和验证规则。开始任务时先阅读本文件，再按目标模块阅读代码与相关文档；若子目录存在更具体的 Agent 指引，也应一并核对。用户在当前会话中的明确要求优先于仓库文档。

## 1. 项目定位

CapsWriter-Offline 是 Windows 10/11 优先的语音输入工具，默认识别链路可完全离线，云 LLM 功能除外。用户通过全局快捷键开始/结束录音；客户端采集音频并通过 WebSocket 发送给服务端；服务端在识别子进程中运行 ASR 和按需外挂的标点模型，强制对齐由独立兄弟进程按请求加载；客户端再按运行模式进行可选的单次 LLM 文本处理、上屏、字幕和独立文字归档。

当前代码版本由 `config_templates/config_client_template.py` 与 `config_templates/config_server_template.py` 中的 `__version__` 定义（目前均为 `2.6`），根目录本机配置中的版本字段应与模板同步。README、历史文档或发行说明可能滞后，发生冲突时以代码、当前模板和用户要求为准。

模板当前默认快捷键为右 Ctrl（`ctrl_r`）和鼠标 X2，均为切换录音模式（`hold_mode=False`）；不要按旧文档假定为 CapsLock 长按。字段值与邻近注释冲突时，以代码取值和处理逻辑为准；本机实际行为仍以根配置为准。

## 2. 仓库地图

- `start_client.py`：客户端冻结/源码入口。
- `core/client/cli.py`：`mic`、`transcribe`、`rebuild-srt` 命令及无参数/拖拽兼容解析；在加载音频和 UI 模块前完成参数处理。
- `start_server.py`：服务端冻结/源码入口，包含 Windows 多进程 `freeze_support()`。
- `start_capswriter.ps1`：本机 Conda 启动辅助脚本，支持 `-ServerOnly` / `-ClientOnly`；实际执行会启动应用，不用于普通静态检查。
- `config_client.py`：被 Git 忽略的客户端本机配置，程序实际从这里读取；不得覆盖用户取值。
- `config_server.py`：被 Git 忽略的服务端本机配置，程序实际从这里读取；不得覆盖用户取值。
- `config_templates/`：受 Git 跟踪的客户端/服务端默认配置，是配置字段、注释、默认值和版本号的规范源。
- `core/protocol.py`：客户端与服务端共享的 JSON 消息协议。
- `core/client/`：音频采集、快捷键、连接、结果处理、文件转录、LLM、光标参考文本、UDP 和托盘管理。
- `core/server/`：WebSocket 服务、任务队列、识别子进程、引擎工厂、合并与格式化。
- `core/server/schema.py`、`core/server/state.py`：任务、结果、会话及进程内状态；`core/server/worker/task_handler.py` 包含 `TaskBuffer` 和调度循环。
- `core/server/worker/aligner_worker.py`、`core/server/engines/manager.py`：对齐器兄弟进程与 `ProcessAlignerProxy` 跨进程代理。
- `core/client/transcribe/`、`core/client/manager/file_runner.py`：批量文件转录、进度、输出保存与字幕重建。
- `core/ui/`：Tk/托盘/Toast/录音状态提示等共享 UI。
- `core/tools/`：ITN、格式化、窗口检测和其他共享工具。
- `core/server/engines/`：ASR、标点、对齐器及其推理代码。多个 `export/` 与 `gguf/` 目录包含上游/派生代码，修改前先确认代码归属。
- `LLM/providers.template.toml`：受版本控制的 Provider 模板，不得包含真实 Key。`LLM/providers.toml` 是被 Git 忽略的本机凭据文件；允许 `api_key` 或显式优先的 `api_key_env`，不得整份输出或提交。`LLM/presets.toml` 为公开静态预设。缺少本机配置时读取模板，首次编辑才创建副本，不覆盖现有凭据。
- `build_llm.py`：发行包只复制公开 Provider 模板和预设，不得为整个 `LLM` 建立 junction 或打入本机凭据。
- `core/client/caret_context.py`、`caret_worker.py`：按次、可关闭的 UI Automation 光标参考读取，超时结束隔离子进程。
- `core/ui/menu_model.py`、`menu_icons.py`、`tray_native.py`：英文菜单动作、统一图标和 Windows Tooltip；原生后端接口按 pystray 0.19.5 隔离。
- `core/log_archive.py`、`core/client/diary/diary_writer.py`：按月诊断与文字记录归档，保存开关相互独立。
- `models/`：模型说明和下载入口；大型模型文件不应提交。
- `docs/`：用户文档、变更日志和历史审计归档；当前待办统一记录在根目录 `TODO.md`。
- `build.spec`、`build-client.spec`、`build_hook.py`：PyInstaller 打包链路。
- `zip_release.py`：使用 7-Zip 归档发行目录；不能代替干净机器发布验证。
- `tests/`、`pyproject.toml`、`requirements-dev.txt`：pytest 测试、覆盖率、Ruff/mypy 配置与固定版本的开发工具。
- `.pre-commit-config.yaml`、`.github/workflows/`：本地钩子、Windows 质量门禁及发布冒烟流程。

## 3. 运行与依赖

优先复用项目环境，不要把某台机器的绝对 Python 路径写入代码或文档。当前 CI 和 Ruff/mypy 以 Python 3.11 为基线，开发工具版本在 `requirements-dev.txt` 中固定；运行依赖尚无完整锁文件，也未声明并验证完整的 Python 支持范围。开始工作前记录所选解释器的 `python --version`。

当前工作区约定使用已准备的 Conda 环境 `capswriter`（注意环境名没有连字符），应检查其实际 Python 版本。其他机器先确认已有项目环境，不要假定相同安装路径。Agent 执行源码、安装检查、`compileall`、pytest 或打包时应优先使用该环境，不要仅因当前 PowerShell 的 `PATH` 中找不到 `conda` 就另建 `.venv` 或向系统 Python 重复安装依赖。优先执行：

```powershell
conda run -n capswriter python --version
```

若 Agent 的非交互 Shell 没有初始化 Conda，先读取 `$env:USERPROFILE\.conda\environments.txt` 定位已登记的 `capswriter` 环境，再直接调用该环境下的 `python.exe`；不得把解析出的本机绝对路径写入仓库文件。确认该环境确实不存在或不可用后，才考虑创建新环境或安装依赖。

下文 `python` 命令均指上述已确认的解释器：未激活环境时替换为 `conda run -n capswriter python ...` 或 `& $capswriterPython ...`（变量保存实际解析出的路径）。直接调用解释器若遇到 Conda DLL/外部工具查找问题，应补齐该环境的激活设置或使用 `conda run`，不要向系统 Python 重装依赖。

开发环境的基础安装方式：

```powershell
python -m pip install -r requirements-client.txt -r requirements-server.txt
# 需要运行测试或质量门禁、且环境尚未安装开发工具时
python -m pip install -r requirements-dev.txt
```

首次准备（源码导入、CLI 帮助和部分测试也需要根配置；仅执行 compileall 不需要初始化配置）：

```powershell
# 仅在根目录本机配置不存在时初始化；不得覆盖已有配置
if (!(Test-Path config_server.py)) { Copy-Item config_templates/config_server_template.py config_server.py }
if (!(Test-Path config_client.py)) { Copy-Item config_templates/config_client_template.py config_client.py }
```

源码启动需在两个终端分别执行，服务端命令会持续运行：

```powershell
# 终端 1：服务端
python start_server.py
# 终端 2：麦克风客户端；无参数启动同样进入 mic 模式
python start_client.py mic
```

CLI 示例（媒体/TXT/JSON 路径需换成用户指定的文件；转录要求服务端已启动）：

```powershell
python start_client.py --help
python start_client.py transcribe --help
python start_client.py transcribe --format srt,txt,json --no-recursive "媒体目录"
python start_client.py rebuild-srt --text "校对.txt" --json "原始.json"
```

`rebuild-srt` 使用配套 TXT 与含 token/时间戳的 JSON 在本地重建字幕，不需要 ASR 服务或麦克风。`--format` 和递归参数仅覆盖本次运行，不写回本机配置。拖拽/裸路径仍有兼容入口，具体匹配以 `core/client/cli.py` 为准。

注意：

- 服务端启动通常需要与 `config_server.py` 中 `model_type` 对应的完整模型；不要为了普通代码检查自动下载大模型。
- 文件转录依赖当前进程可找到的 `ffmpeg`；`ffprobe` 用于提前获取时长，缺失时进度会降级。
- 全局快捷键、托盘、麦克风、PortAudio、DirectML/Vulkan 和模拟键盘行为需要真实 Windows 桌面会话，CI 或无头环境不能完整覆盖。
- 根目录的 `config_client.py` 与 `config_server.py` 是用户本机配置，默认快捷键、模式和模型可能与模板不同。不要以模板或文档覆盖它们。
- PyInstaller 发行包必须从 `config_templates/` 复制默认配置，不能把开发机根目录的本机配置打入发行包。
- 两个 spec 在 Windows 上会为部分源码、资源及模型目录建立 junction；`dist/` 可能引用工作树，清理、移动或归档前先检查链接目标。`build-client.spec` 的 Win7 注释是历史意图，不代表当前 Python/依赖组合已验证兼容 Win7。
- `build/`、`dist/`、`logs/`、年份目录、模型二进制和 `__pycache__/` 是生成物或用户数据，不要纳入普通改动。

## 4. 修改前规则

1. 先执行 `git status --short`，把已有修改视为用户工作。不要重置、覆盖或顺手格式化无关文件。
2. 阅读目标模块、它的调用方、状态对象和配置项；跨客户端/服务端的改动还必须阅读 `core/protocol.py`。
3. 优先做最小、可审查的改动。不要在修复业务问题时批量改写 vendored/export 代码。
4. Keep UTF-8. Communicate with the user in their preferred language. New or substantially rewritten internal documentation, plans, comments/docstrings, diagnostic terminal output and logs use English. User-facing text follows the multilingual UI plan in `TODO.md`; keep stable IDs separate from translated labels. Migrate existing internal material incrementally, preserving user content, language fixtures, recognition rules, task-specific prompts and upstream attribution. Do not bulk-translate unrelated files or historical records.
5. 不得把 API Key、访问令牌、私人音频、识别文本、剪贴板内容、日志或真实模型路径提交到仓库。新增密钥读取时优先使用环境变量或本地未跟踪配置。
6. 配置兼容性是产品能力。新增配置项应有安全默认值，并用 `getattr(..., default)` 或迁移逻辑兼容旧配置/发行包。
7. 修改配置字段、默认值、说明或 `__version__` 时，以 `config_templates/` 中的模板为提交对象；若根目录本机配置存在，还应只合并必要的结构变化并保留用户取值。不得只修改被忽略的根配置，因为这类变化不会进入提交。
8. 根配置为可执行 Python；LLM 配置改为 TOML。不得导入或执行遗留的 `LLM/*.py`，迁移提示词与连接信息时静态读取，避免输出旧密钥。不得将本机备份目录或生成记录纳入提交。
9. 搜索优先使用 `rg` / `rg --files`，需要核对被忽略的文件时仅扩大到相关路径。临时脚本放在临时目录或明确的本地工作目录，不将个人绝对路径、临时产物写入长期指南；不要删除用户原有脚本。

## 5. 不可破坏的架构约束

以下是修改时必须保持或补齐的约束，不表示所有历史代码都已满足；当前已知缺口见第 8 节和 `TODO.md`。

### 音频与线程

- PortAudio/sounddevice 回调必须快速返回；不得在回调线程中执行网络等待、文件重操作、`stream.close()` 或 PortAudio 重初始化。
- 音频流的 `start`、`stop`、`reopen`、设备监控和闲置挂起会并发发生。共享生命周期状态必须由明确初始化的锁或单一状态机保护，并避免重复重启线程。
- 不要依赖 `sounddevice` 的私有属性（如 `_ffi`、`_lib`）做常规生命周期管理；若确有必要，必须隔离版本、失败路径和 Windows 实机回归测试。
- 跨线程向 asyncio 事件循环提交工作时使用线程安全入口，并处理事件循环已关闭的情况。

### asyncio、WebSocket 与多进程

- 事件循环中不得直接执行阻塞 I/O 或 CPU 密集推理。推理继续留在识别子进程，阻塞队列读取需在线程执行器中完成。
- 强制对齐通过 `ProcessAlignerProxy` 与兄弟进程通信，按需加载、超时降级、闲置后退出整个对齐进程，由主进程监控补位。不要退回在 ASR 进程内卸载共享 GPU 后端的做法；修改时同时核对请求 ID、队列、退出和超时路径。
- `Task`、`Result` 及跨进程状态必须保持可 pickle；不要把 WebSocket、Tk 对象、锁或本地闭包放进多进程队列。
- 任务必须同时按 `socket_id` 和 `task_id` 隔离。同一连接上的文件转录、麦克风任务或未来并发请求不得共享音频缓存。
- 修改协议字段时，同时更新序列化、反序列化、客户端发送、服务端接收、服务端返回、客户端处理和兼容默认值。
- 网络输入是不可信数据。校验消息类型、必填字段、枚举、Base64、音频字节对齐、切片范围和消息/会话大小；不要使用无限制缓存作为默认方案。
- 保留 local 模式仅监听 loopback、LAN 模式握手 Bearer 认证及现有资源上限；LAN 令牌由 `CAPSWRITER_AUTH_TOKEN` 读取，至少 32 个字符。TLS 需显式配置，不能把令牌认证写成传输加密；跨不可信网络按 `docs/局域网连接安全配置.md` 配置 TLS 或可信反向代理。

### UI 与输入

- Tk 对象只能在 Tk 所属线程创建和修改；其他线程优先向现有 UI 管理器队列投递，由 Tk 线程的 `root.after(...)` 轮询处理。沿用现有跨线程 `after` 入口时必须处理 root 尚未就绪、mainloop 已退出和对象已销毁的情况，不把 `after` 当作无条件安全的线程桥。
- 全局快捷键回调不应等待 Tk root、网络或模型。录音指示、托盘状态和实际 `ClientState.recording` 必须在开始、取消、失败和完成路径保持一致。
- 修改 suppress/hold/toggle 键逻辑时，必须验证短按、长按、重复按键、自动重复、暂停后首次触发、退出清理和管理员窗口输入。

### 数据与隐私

- 当前模板默认保存文字记录，关闭音频、LLM 与光标参考；诊断日志仍可能因旧模块包含识别内容；任何新增采集或外发行为都必须显式配置、可关闭、可解释。
- LLM 每次只发送当前转写；预设显式启用时附带当次光标参考，没有历史或选区读取。`ServerConfig.network_mode='local'` 只约束 ASR 服务监听地址，不阻止客户端云 LLM 或 UDP 外发；严格离线总开关仍是待办。新增 Provider 或上下文时应保持本地识别可独立运行，并用 mock 验证所声称的离线边界；即使 Provider 名为 Ollama/LMStudio，也要核对实际 `base_url`。
- 日志中避免记录 API Key、完整提示词、完整选区和不必要的完整识别内容；诊断字段优先记录长度、任务 ID 前缀、耗时和错误类型。

## 6. 验证要求

仓库已有 pytest 套件、Ruff/mypy、pre-commit 配置及 Windows GitHub Actions。每次改动执行与范围匹配的检查，并在交付时说明没有执行的项目及原因；工作流存在不代表最近一次远端执行成功。

无模型、无 GUI 也能执行的最低检查：

```powershell
python -m compileall -q start_client.py start_server.py config_templates core LLM tests
# 本机配置存在时才检查，不为纯语法检查创建它们
if (Test-Path config_client.py) { python -m compileall -q config_client.py }
if (Test-Path config_server.py) { python -m compileall -q config_server.py }
git diff --check
git status --short
```

纯文档改动还应核对引用路径、命令参数与代码事实；不必启动应用或执行完整测试套件。修改 Python 代码时，在第 3 节的配置准备和开发依赖就绪后运行相关测试；协议、线程生命周期、调度等跨模块改动执行默认套件：

```powershell
python -m pytest -q
```

`pyproject.toml` 默认排除 `integration`、`windows`、`manual`；`tests/conftest.py` 会按目录标记集成/Windows 测试。`tests/windows/test_native_menu.py` 检查菜单句柄、位图和子菜单映射，不读取用户文本或启动硬件，也不能代替交互与 DPI 回归。集成目录目前只有说明。新增用例后需按实际依赖选择标记；仅运行不触及 Windows/人工环境的集成用例可用 `python -m pytest -m "integration and not windows and not manual"`。

代码改动按范围补充以下质量门禁（覆盖率阈值针对 `pyproject.toml` 列出的模块，不是全仓覆盖率）：

```powershell
python -m ruff check start_client.py start_server.py config_templates core LLM tests
python -m mypy core/protocol.py core/server/schema.py core/server/merger core/tools/format_tools.py
python -m pytest --cov --cov-report=term-missing --cov-fail-under=50
```

本地钩子以 `.pre-commit-config.yaml` 为准：Ruff/mypy/compileall 用于 Python 文件，pytest 在 pre-push 阶段；配置文件存在不等于本机已安装钩子。不要为了普通检查自动安装 Git 钩子或执行全仓自动修复。

CI 现状与限制：

- `.github/workflows/quality.yml` 在 Windows/Python 3.11 上执行语法、lint、关键路径类型检查和默认测试覆盖率检查。
- quality 从模板初始化缺失的根配置，并将模板纳入语法/lint；pre-commit 的语法检查不依赖未跟踪根配置。运行测试仍需按第 3 节准备配置，不提交本机配置。
- `.github/workflows/release-smoke.yml` 由 `v*` 标签或手动触发，先调用 quality，再构建组合包并检查两个 EXE 是否存在；它没有执行打包程序，也不覆盖无模型启动、GUI 或干净机器运行。

按改动范围补充验证：

- 协议/网络：有效消息、畸形 JSON、非法枚举、非法 Base64、超限消息、断线清理、多个 task 并发。
- 调度/合并：同 task FIFO、跨 task 无饥饿、断连任务清理、最终片段只完成一次。
- 音频：启动/停止幂等、暂停/恢复、默认设备切换、设备拔插、异常结束、退出时无重启、短录音取消。
- UI/托盘：主屏/副屏、不同 DPI、Toast 与录音提示不重叠、应用退出后无线程继续访问 Tk。
- LLM：静态配置重载、单一默认预设、显式触发覆盖默认、Provider 路由、空 Key/超时、取消、失败保留原文、上下文 opt-in、关闭时零请求。
- 光标与记录：密码框/选区/不支持/超时/焦点改变降级，分片固定快照；文字、音频、LLM 记录开关组合及跨月归档。
- 打包：源码运行通过后，再按需验证 PyInstaller；不要把 `dist/` 或大模型加入提交。

运行真实麦克风、模拟全局按键、联网 LLM、GPU 管理命令、模型下载或完整打包前，先核对环境和用户意图。这些操作可能抢占输入、访问网络、需要管理员权限或耗费大量资源；用户已明确授权且范围未变时无需重复确认，普通文档/静态检查不应顺带触发这些操作。

## 7. 测试建设约定

纯函数和 mock 单元测试优先放在 `tests/unit/`，跨组件与实机测试分别放在 `tests/integration/`、`tests/windows/`；已有协议上限和调度回归位于 `tests/` 根目录，不必为普通修复搬迁。`.gitignore` 虽有全局 `test_*.py` 规则，但已通过 `!tests/**/test_*.py` 放行套件；新增文件用 `git status --short` 或 `git check-ignore -v <路径>` 核对，不要强制添加来绕过规则。

优先覆盖纯函数和边界层：

1. `core/protocol.py` 的协议解析与校验。
2. `core/server/merger/` 和 `core/tools/` 的确定性算法。
3. `TaskBuffer` 的 FIFO/公平性和断连清理。
4. 音频流生命周期状态机（mock sounddevice）。
5. LLM 预设解析、消息组装与敏感信息边界（mock Provider）。

测试不得依赖真实 API Key、真实个人音频或已安装的大模型。小型音频 fixture 使用合成静音/正弦波，并明确采样率与 dtype。

## 8. 已知高风险区域

- `core/client/audio/stream.py`：PortAudio 生命周期、设备监控和多线程竞态。
- `core/client/app.py`、`core/client/shortcut/`：闲置挂起、暂停恢复与录音状态竞态。
- `core/server/connection/`：已有 local/LAN 认证模式、输入/队列上限和连接内按 task 的音频缓存；继续审查认证兼容、边界错误与断线清理，协议版本协商及显式取消仍待完善。
- `core/server/state.py`、`core/server/worker/task_handler.py`：Worker 会话和调度缓冲仍仅以 `task_id` 为键；连接内缓存隔离不等于跨连接同 ID 已隔离。修改会话标识时需把 `(socket_id, task_id)` 贯穿调度、合并、返回与清理。
- `core/server/worker/task_handler.py`：调度语义直接影响实时听写延迟和文件任务公平性。
- `core/server/worker/process_manager.py`、`core/server/worker/aligner_worker.py`：ASR/对齐器进程的启动、超时、闲置退出和异常重启；GPU 后端资源需按进程边界隔离。
- `core/client/llm/` 与 `LLM/`：Provider 配置、云端数据外发和密钥管理。
- `core/server/engines/*/export/`：体积大、重复度高，含上游派生代码；避免无边界的全仓格式化。

当前待办和优先级见 `TODO.md`；历史现状与证据见 `docs/archive/PROJECT_AUDIT_REPORT-2026-08-08.md`。

历史审计中的“已修复”只代表当时的范围，不能替代对当前调用链和测试的核对。任务完成时同步对应 TODO 状态；新发现的范围外缺陷记录为待办，不在文档核对任务中顺带修改业务代码。

## 9. 交付清单

Agent 完成任务前应确认：

- 只修改了请求范围内的文件，已有用户改动仍然存在。
- 新增行为有配置默认值、失败路径和退出清理。
- 线程/事件循环/多进程/Tk 边界没有被跨越。
- 没有提交密钥、日志、音频、日记、模型或打包生成物。
- 已执行最低语法和 diff 检查，或清楚说明阻碍。
- 最终回复列出修改文件、主要结果、验证结果和仍需实机验证的内容。
