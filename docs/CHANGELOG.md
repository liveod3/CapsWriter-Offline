# 更新日志

## 2026-09-22 — Unreleased, terminal-outcomes item accepted

- Complete **Guarantee terminal task outcomes and bounded recovery**, accepted by the user on 2026-09-22 after reporting no material issues; includes the delivery slice recorded below. Add connection/task-scoped cancellation with bounded disconnect fallback; bound model startup, ASR task-loop progress and inactive task state; stop/reap the service on aligner timeout or abnormal exit. Dispatch network shutdown on its owning loop and defer process cleanup until startup/network work returns, including signal races during startup. Keep clean idle aligner replacement; automatic crash restart/replay and a new IPC acknowledgement system are not required.
- Validation: `capswriter`, Python 3.11.15; **412 passed, 2 deselected**, configured coverage **84.70%**. Ruff, selected mypy targets, compileall including existing local configurations, fixture help and diff checks pass. Includes synthetic Windows spawn/hang/termination/reaping; real models, microphone/desktop interaction and release packaging remain for their applicable manual checks. See the [single acceptance record](validation/P0-03-terminal-outcomes.md). User acceptance authorizes committing the remaining changes and marking the single TODO entry complete. Earlier stage records below are historical evidence, not additional acceptance gates.

## 2026-09-21 — Unreleased

- Stage 3d implementation slice, self-reviewed and uncommitted; acceptance is now deferred until the whole terminal-outcomes TODO item is complete: bound server result sends and output-queue backpressure; retire a failed client connection once while preserving peers. Stop the listener and close clients on observable IPC queue failure, a stuck result read or ASR worker exit. Signal worker channel failure before model cleanup, preserve cleanup after broken shutdown queues and join ASR after forced termination. Add 32 synthetic regressions; 378 default tests pass. Live process hangs, cancellation and shutdown ownership remain pending. Silent IPC loss must reach a bounded task outcome; a new IPC acknowledgement system and automatic restart/replay are outside the required scope. See the [stage 3d handoff](validation/P0-03d-server-result-delivery.md).

- Stage 3c, accepted by the user after manual testing: add compatible microphone upload and final ASR deadlines, reject late/duplicate/unowned finals, and wait for successful upload before processing a result. Receive results independently of serial LLM/output work, preserve accepted results across disconnects, bound pending dictations and join owned receive/process/deadline tasks on exit. Close failed uploads with bounded cleanup and transport abort fallback. Add 26 regressions; 346 default tests pass. See the [stage 3c handoff](validation/P0-03c-microphone-deadlines.md).

- Stage 3b, accepted by the user after manual testing: report caught recognition-pipeline failures as content-free terminal task errors. Preserve connection/task isolation, discard failed-task tails and bound failure history; close legacy clients on failure instead of sending a misleading empty success. Stop only the matching microphone recorder or file upload, skip LLM/output/archive success paths and report a readable failure. Bound task-error send/close fallback and abort stalled transport cleanup. The default suite passes 320 tests. See the [stage 3b handoff](validation/P0-03b-task-error-outcomes.md).

- Stage 3a, accepted by the user after functional checks and the terminal presentation follow-up: add compatible file I/O and stalled-result timeouts, reject foreign-task results and wait for successful upload before saving final output. Cancel and join file sender/receiver tasks before closing their connection; keep application shutdown waiting for runner cleanup. Own late-created FFmpeg/ffprobe children through cancellation, drain output pipes, and escalate termination after a bounded wait. Present file failures once with a reason and action hint, remove failed progress displays and retain technical diagnostics in enabled file sinks; expected child cancellation is not an error. Add 31 regressions, including a real synthetic Python child with buffered stdout; 283 default tests pass. A03 remains open for protocol, microphone and server supervision work. See the [stage 3a handoff](validation/P0-03a-file-task-lifecycle.md).

## 2026-09-20 — Unreleased

- Isolate worker sessions and scheduler buffers by `(socket_id, task_id)`. Completing or disconnecting one connection no longer removes another connection's same-ID task state. Clean disconnected sessions during idle polling and before selecting buffered work.
- Add 15 synthetic regressions covering colliding IDs, FIFO/round-robin scheduling, audio/text/token separation, final cleanup, disconnects, serialization and result delivery. Stage 1 passed the default suite (206 tests) and was accepted by the user after dictation, concurrent file/dictation and dual-file checks. Concurrent dictation latency remains a separate follow-up. See the [stage 1 record](validation/P0-01-connection-task-isolation.md).
- Stage 2a, accepted after rapid recordings interleaved with delayed LLM processing remained independent: give each recording a private bounded capture bridge and serialize microphone ownership across shortcuts and pause/resume. Roll back readiness timeouts and recorder failures, reject stale audio/completion callbacks, and cancel draining recorders on shortcut shutdown. Add 20 synthetic regressions; the default suite now passes 226 tests. Driver recovery, blocking file I/O and FFmpeg/shutdown completion remain pending under A02. See the [stage 2a handoff](validation/P0-02a-recording-ownership.md).
- Stage 2b, accepted by the user after repeat recording, pause/resume, device recovery, exit/restart and saved-audio checks: coalesce device recovery on one monitor thread and isolate private PortAudio refresh behind version/initialization guards. Move recording file I/O and hardware startup/resume off the event loop, check encoder completion and interrupt blocked FFmpeg writes on cancellation or timeout. Keep the loop alive for actual recorder cleanup and reject late startup after shutdown. Add 26 regressions; 252 default tests pass. A02 is complete at the reported local acceptance scope; broader hardware validation remains separate. See the [stage 2b record](validation/P0-02b-audio-lifecycle.md). Add dedicated recording storage and client/server configuration reload to the future P1 backlog.

## 2026-09-15 工作区更新（未发布）

- 默认纠错预设要求明确数值使用半角阿拉伯数字，保留单位、精度及编号前导零；固定词语与不确定数量不机械转换，日期格式暂不处理。
- LLM 失败日志细分网络超时、HTTP 状态、Gemini/兼容接口错误码、限流等待和生成结束原因；空结果、内容阻止与 token 截断保留原文。记录请求编号和耗时，服务端错误文本只提取受控原因，不保存任意响应正文。
- 录音结束立即显示持续的转写状态，随后衔接 LLM 准备与等待状态，完成/取消/失败时清理；状态操作经 Tk 队列投递，不再阻塞等待 root 就绪。增加按任务隔离和各阶段耗时诊断。

## 2026-09-14 工作区更新（未发布）

- 按 Google 官方文档增加 Gemini 3.5 Flash-Lite Provider；模板与本机凭据 TOML 分离，打包不再链接 LLM 目录或复制本机 Key。

- 修复客户端退出时正常 WebSocket 关闭被包装为未捕获异常；恢复接收循环的通信异常边界，主动退出不重连，意外断线和解析失败清理连接后重连。
- 修复 Server/Client 托盘菜单空白：Tooltip 通过独立菜单窗口过程接入，其余消息转交原过程，保留 Windows 默认绘制；新增默认消息分发和菜单文字回归检查。
- 托盘菜单改为英文、统一深蓝图标与逐项悬停说明；增加记录与静态配置入口。
- 删除客户端/服务端热词检索、音近及正则替换，删除旧上下文编辑菜单和会话历史。
- 增加默认关闭的光标周围文本参考，按次读取、超时降级、分片使用固定快照。
- 使用静态 TOML Provider/预设替代 Python 角色。内置保守纠错与翻译，单一可关闭默认预设；取消不自动上屏，失败保留原文。
- 文字、音频、文本动作记录与诊断保存分别控制，文字记录按 `logs/transcripts/YYYY/MM/DD.md` 归档。
- 手动暂停不再被录音快捷键唤醒；闲置挂起仍可自动恢复。
- 更新 Agent 指引、配置模板、CI 输入和使用说明。Windows 交互、真实模型及完整发行包仍需实机回归。

详情与迁移见[文本动作与记录](文本动作与记录.md)。以下为历史版本记录，不代表当前功能。


## v2.6

- **GPU 预加速**：服务端新配置（默认关闭），适用于 Nvidia 独显，录音开始时主动锁定显存高频率，大幅降低模型转录延迟至 0.1s
- **标点处理优化**：仅对 8 词以内的结果去除末尾标点，长句不再误伤；可通过配置 config_client.py 指定某些程序强制去除标点
- **带延迟的附加回车**：用于同花顺等场景，输入股票名后等待 0.5s 自动回车切换，可在 config_client.py 配置
- **角色功能简化**：角色功能简化为单一开关；默认角色改用 DeepSeek API，方便新用户填入密钥后直接体验
- **ITN 增强**：连续多数字转换更鲁棒
- **活动窗口日志**：每次识别后通过 debug 日志输出当前活动窗口名，方便用户配置 paste_apps
- **标点模型容错**：取消强制检查，加载失败时静默回退，避免启动受阻
- **热词使用体验**：右键菜单点击「热词」直接打开 hot.txt 文件，不再弹窗
- **重启菜单**：托盘右键新增「重启」选项，便于修改配置后快速重启生效
- **集成显卡兼容**：加入调试性配置项，集显出问题时可通过禁用 feature 解决
- **Windows Terminal 适配**：支持隐藏 Windows Terminal 控制台窗口

## v2.5

- **引入 [Qwen3-ASR-1.7B](https://github.com/HaujetZhao/Qwen3-ASR-GGUF)**：140ms 极速推理，准确率夯爆。Decoder Vulkan 加速默认打开，需占 1.6GB 显存。显卡空闲时，会降低显存频率，冷启动转录延迟升至 300ms。若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- **集成 Force Aligner**：辅助 Qwen3-ASR 支持时间戳，按需加载、超时释放，仅文件转录时占用资源
- **热词别名**：热词支持用 `|` 分隔定义多个别名，
- **角色别名**：`name` 用 `|` 分隔定义多个别名，解决 ASR 对角色名识别不准的问题
- **移除纠错历史**：热词别名已能覆盖纠错历史的需求，移除 `hot-rectify.txt` 及相关逻辑
- **文件转录支持热词**：文件转录现在也可以使用热词功能
- **语言配置**：`config_client.py` 新增 `language` 选项，支持指定识别目标语言
- **架构重构**：进行了大量重构，方便后续维护
- **日志优化**：只保留一份日志文件

## v2.4

- **改进 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型，使 Encoder 支持通过 DML 用显卡（独显、集显均可）加速推理，Encoder 和 CTC 默认改为 FP16 精度，以便更好利用显卡算力**，短音频延迟最低可降至 200ms 以内。
  - 若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- 服务端 Fun-ASR-Nano 使用单独的热词文件 hot-server.txt ，只具备建议替换性，而客户端的热词具有强制替换性，二者不再混用
- 可以在句子的开头或结尾说「逗号、句号、回车」，自动转换为对应标点符号，支持说连续多个回车。
- Fun-ASR-Nano 加入采样温度，避免极端情况下的因贪婪采样导致的无限复读
- 服务端字母拼写合并处理

## v2.3

- **引入 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型支持，推理更轻快**
- 重构了大文件转录逻辑，采用异步流式处理
- 优化中英混排空格
- 增强了服务端对异常断连的清理逻辑

## v2.2

- **改进热词检索**：将每个热词的前两个音素作为索引进行匹配，而非只用首音素索引。
- **UDP广播和控制**：支持将结果 UDP 广播，也可以通过 UDP 控制客户端，便于做扩展。
- **Toast窗口编辑**：支持对角色输出的 Toast 窗口内容进行编辑。
- **多快捷键**：支持设置多个听写键，以及鼠标快捷键，通过 pynput 实现。
- **繁体转换**：支持输出繁体中文，通过 zhconv 实现。

## v2.1

- **更强的模型**：内置多种模型可选，速度与准确率大幅提升。
- **更准的 ITN**：重新编写了数字 ITN 逻辑，日期、分数、大写转换更智能。
- **RAG 检索增强**：热词识别不再死板，支持音素级的 fuzzy 匹配，就算发音稍有偏差也能认出。
- **LLM 角色系统**：集成大模型，支持润色、翻译、写作等多种自定义角色。
- **纠错检索**：可记录纠错历史，辅助LLM润色。
- **托盘化运行**：新增托盘图标，可以完全隐藏前台窗口。
- **完善的日志**：全链路日志记录，排查问题不再抓瞎。


## v1.0 

- 通过分段识别和去重，实现了支持无限时长语音的转写
- 客户端支持转写音视频文件为 srt 字幕，只需将音视频文件拖动到客户端 exe 上打开即可

## v0.6 


- 新增日记功能，将每日的录音结果保存在一个 Markdown 文件中
- 新增关键词日记功能，每日的以关键词开头的录音结果会保存在特别的 Markdown 文件中
- 新建录音文件夹的时候，会复制一个 Python 辅助脚本，用于清理没有被 Markdown 文件引用的附件，这样一来，通过编辑 Markdown 日记就可以清理不需要保存的录音
- 新增定义录音文件保存目录
- 默认保存48000采样率高品质录音录音，如果用户安装了 FFmpeg 则保存为 mp3 格式，否则保存为 wav 格式
- 输入方式改为模拟 Ctrl + V 粘贴，粘贴完后恢复剪贴板内容
- 使用 rich 库输出彩色文字，尽量在各种终端达到一致的显示效果

## v0.5 

- 修改热词文件后，不用重启客户端，就可以动态更新热词了。

## v0.4

- 为客户端加入了三种热词功能：中文、英文、自定义
- 改进了对中文数字的搜索，当数字的左侧或者右侧有英文时，就一定会被选中。
- 改进了中英空格排版，能够正常输出 iPhone 4s 这样的词语。


## v0.3 

- 客户端当音频设备名不可 utf-8 解码时，不再闪退
- 客户端添加配置可以编辑修改，要消除识别结果末尾哪些标点
- 客户端添加配置可以修改快捷键
- 客户端添加配置可以修改快捷键触发的时间阈值
- 客户端连接中断会自动进行重试
- 客户端提示当前所用的快捷键
- 服务端对识别结果，中英文混排进行空格校正
- 当地址无法被绑定时提示问题，而不是直接闪退


## 起源

**2020年10月**，因手机上的语音输入法很好用，但电脑上却没有足够好用的语音输入法，我手写了一个工具 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)**，通过长按大写锁定键录音，松开后，调用阿里云的一句话识别 API，识别后上屏。12月的时候，还加入图形化界面。但当时大学宿舍的校园网经常抽风，没有稳定的网络环境，转录延迟飘忽不定。有时候说完话了，等了好几秒，才发现 WiFi 没有连接，毁心态。但是当时并没有识别率能满足要求的离线中文 ASR 模型。

后来 Whisper 发布，中文识别率确实不错，但是模型延迟很高，无法满足本地语音输入。

到了**2023年5月**，在B站看到了 [极客湾](https://space.bilibili.com/25876945) 的视频 [我们做了个能对话的AI派蒙，免费给大家玩！](https://www.bilibili.com/video/BV1bm4y117ba/) ，他们实现的效果非常好，其中提到 ASR 模型用了阿里 [FunASR](https://github.com/modelscope/FunASR) 团队发布的开源 [Paraformer](https://www.modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1) 模型，于是就拿来测试了下，果然识别准确率很棒、延迟超低，于是弃坑 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)** ，立马新开了 **[CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)** ，用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 调用 Paraformer 进行转录，效果非常好。

**2025年12月09日**，我看到阿里 [FunASR](https://github.com/modelscope/FunASR) 团队开源了 [Fun-ASR-Nano](https://www.modelscope.cn/models/FunAudioLLM/Fun-ASR-Nano-2512) 模型，拿来一测，果然准确率又上一个台阶，在 sherpa_onnx 支持后，我赶紧更新了 CapsWriter-Offline v2.1，一次性加入了 SenseVoice-Small 和 Fun-ASR-Nano 模型的支持。但问题是当时 sherpa_onnx 是用 ONNX 实现的 Fun-ASR-Nano，速度有些慢。

我研究了 Fun-ASR-Nano 的架构，发现它的 Decoder 是 LLM 架构，而推理 LLM 最快的是 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 。虽然我编程能力差，但刚好此时，Google 推出了 Antigravity AI 编程 IDE，里面有 Gemini 和 Claude 模型，在他们的帮助下，我成功写出了 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF)，用 onnx 和 gguf 格式混合运行 Fun-ASR-Nano，用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分，在我的笔记本上实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.025 |
| CPU U9-285H  | 0.1 |

**2026年01月21日**，[Qwen3-TTS](https://www.modelscope.cn/collections/Qwen/Qwen3-TTS) 开源发布了，生成效果极其优异，让我特别想要，但官方版本推理速度很慢，于是基于 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 的加速推理经验，在 Antigravity 的帮助下，我又实现了 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) ，也是用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分。

**2026年01月28日**，间隔没几天，[Qwen3-ASR](https://www.modelscope.cn/collections/Qwen/Qwen3-ASR) 开源发布了，本来没抱太大预期的，但下载 1.7B 一测后，又给我震惊了，准确率竟比 Fun-ASR-Nano 还上一个台阶，能吊打闭源模型！于是在 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) 经验的帮助下，我马不停蹄地实现了 [Qwen3-ASR-GGUF](https://github.com/HaujetZhao/Qwen3-ASR-GGUF) 加速推理，实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.05 |
| CPU U9-285H  | 0.2 |

这就是 CapsWriter-Offline 大致的来路。总体就是这几个组件：

- 模型推理
- 热词替换
- 按键监听与录音

当前只有 Windows 的打包，是因为我只有 Windows 电脑，没有 Linux 与 MacOS 的需求。在 Vibe Coding 时代，我相信需求的小伙伴在 Claude Code 的帮助下，也能在其系统上做出类似功能的实现。
