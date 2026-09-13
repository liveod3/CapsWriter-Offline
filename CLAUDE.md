# CapsWriter-Offline：Claude 工作入口

开始任务前必须阅读并遵循 [AGENTS.md](AGENTS.md)。它是本仓库 Agent 公共规则的唯一维护入口，包含环境选择、本机配置保护、架构约束、验证命令和交付要求；本文件只补充按任务查找代码的导航。用户当前会话的明确要求优先于仓库文档。

## 先确认事实来源

- 当前字段、默认值与版本：以 [配置模板](config_templates/README.md) 及其两个 Python 模板为规范源；实际运行读取根目录被忽略的本机配置。不要用模板整体覆盖用户设置。
- 当前工作项与产品方向：见 [TODO.md](TODO.md)。其中英文 UI 迁移是专项计划，开发沟通仍优先使用中文。
- 历史判断：见 [2026-08-08 审计归档](docs/archive/PROJECT_AUDIT_REPORT-2026-08-08.md)，不能直接当作当前缺陷清单或通过验证的证据。
- 运行环境与检查命令：使用 AGENTS.md 第 3、6 节，不另写机器专用 Python 路径或第二套 Conda 环境名。

## 按任务阅读代码

| 任务 | 优先阅读 | 现有相关测试 |
| --- | --- | --- |
| CLI、拖拽、批量文件入口 | [cli.py](core/client/cli.py)、[app.py](core/client/app.py)、[file_runner.py](core/client/manager/file_runner.py) | [CLI](tests/unit/test_client_cli.py)、[文件入口](tests/unit/test_file_runner.py) |
| 录音、暂停、设备切换 | [stream.py](core/client/audio/stream.py)、[客户端状态](core/client/state.py)、[快捷键](core/client/shortcut/)、[MicRunner](core/client/manager/mic_runner.py) | [mock 音频生命周期](tests/unit/test_audio_stream_lifecycle.py)；仍需 Windows 实机回归 |
| WebSocket、认证、输入边界 | [protocol.py](core/protocol.py)、[server_manager.py](core/server/connection/server_manager.py)、[ws_recv.py](core/server/connection/ws_recv.py)、[客户端连接](core/client/connection/websocket_manager.py) | [输入上限](tests/test_aud03_limits.py)、[连接关闭](tests/unit/test_websocket_shutdown.py) |
| 调度、会话、文本合并 | [schema.py](core/server/schema.py)、[state.py](core/server/state.py)、[task_handler.py](core/server/worker/task_handler.py)、[pipeline.py](core/server/worker/pipeline.py)、[merger](core/server/merger/) | [TaskBuffer](tests/test_aud04_task_buffer.py)、[文本处理](tests/unit/test_text_processing.py) |
| 模型、对齐与 GPU 生命周期 | [factory.py](core/server/engines/factory.py)、[model_loader.py](core/server/worker/model_loader.py)、[process_manager.py](core/server/worker/process_manager.py)、[aligner_worker.py](core/server/worker/aligner_worker.py)、[进程代理](core/server/engines/manager.py) | [GPU 监控 mock](tests/unit/test_gpu_monitor.py)；不能替代真实推理验证 |
| 字幕、转录进度、输出 | [file_transcriber.py](core/client/transcribe/file_transcriber.py)、[result_handler.py](core/client/transcribe/result_handler.py)、[srt_adjuster.py](core/client/transcribe/srt_adjuster.py)、[media_tool.py](core/client/transcribe/media_tool.py) | [转录](tests/unit/test_file_transcriber.py)、[结果保存](tests/unit/test_transcribe_result_handler.py)、[字幕](tests/unit/test_srt_adjuster.py) |
| 热词、规则、听写上屏 | [hotword](core/client/hotword/)、[result_processor.py](core/client/output/result_processor.py)、[text_output.py](core/client/output/text_output.py) | [文本处理](tests/unit/test_text_processing.py)；上屏需要桌面验证 |
| LLM 角色、上下文、输出取消 | [RoleConfig](core/client/llm/llm_role_config.py)、[加载器](core/client/llm/llm_role_loader.py)、[检测器](core/client/llm/llm_role_detector.py)、[消息组装](core/client/llm/llm_message_builder.py)、[ClientPool](core/client/llm/llm_client_pool.py)、[处理器](core/client/llm/llm_processor.py)、[角色目录](LLM/) | mock Provider 等待补齐，见 TODO.md；禁止用真实密钥/私人选区作为测试输入 |
| 配置兼容与发布 | [config_templates](config_templates/)、[build.spec](build.spec)、[build-client.spec](build-client.spec)、[build_hook.py](build_hook.py)、[zip_release.py](zip_release.py) | [配置兼容](tests/unit/test_config_compatibility.py)、[发布冒烟工作流](.github/workflows/release-smoke.yml)；仅检查 EXE 存在不代表可运行 |

## 容易误判的行为

- 客户端先解析命令，再加载应用。无参数等同于 `mic`；`transcribe` 可处理多个媒体文件/目录并覆盖本次输出格式；`rebuild-srt` 需要配套 TXT/JSON，不是任意 SRT/VTT 导入。参数见 CLI 源码及[文件转录说明](docs/文件转录功能如何使用.md)。
- 默认快捷键、长按/切换模式和阈值必须按当前模板及本机配置判断，不能把“松开按键结束”套用于所有模式。
- 音频通过 WebSocket 发送 JSON，其中音频字段是 Base64 编码的 16 kHz、单声道 float32 数据；子协议名 `binary` 不代表原始二进制音频帧。客户端指定切片参数，服务端接收层维护任务缓存并按时间切片。
- ASR/标点在识别子进程运行；缺少时间戳能力时由 `ProcessAlignerProxy` 挂载对齐器兄弟进程，文件转录请求才调用对齐。闲置卸载通过结束对齐进程完成，主进程监控补位空载进程。
- `text` 是不依赖时间戳的文本合并结果；`text_accu`、tokens 和 timestamps 用于精确合并/字幕。不同 ASR 引擎的能力以 [EngineCapabilities](core/server/engines/base.py) 和各引擎实现为准，不能假定每个引擎都提供时间戳或热词。
- 服务端已有认证、输入限制与连接内 task 缓存；Worker 会话仍仅按 `task_id` 索引，跨连接同 ID 是需继续加固的边界，见 AGENTS.md 第 8 节。
- LLM 支持 Ollama 与 OpenAI 兼容 API 路由，具体地址及默认值见 [llm_constants.py](core/client/llm/llm_constants.py) 和角色配置。不要按某个历史角色文件名推断 Provider/模型，也不要把本地 ASR 等同于所有功能都不外发数据。
- `LLM/*.py` 是热加载执行的代码，合法字段由 `RoleConfig` 定义。修改角色前静态核对加载器和调用方；不为审计执行角色或复制可能含密钥的配置。
- 诊断可从 `logs/client_latest.log`、`logs/server_latest.log` 及 `logs/transcribe/` 的单次转录日志定位，但只读取相关且必要的片段，交付内容应脱敏。日志不是唯一证据，还需结合代码、配置和复现条件。

## 文档维护

公共环境、测试、隐私和架构规则只修改 AGENTS.md；模块路径或阅读顺序变化时更新本导航。完成对应工作后同步 TODO.md，不因历史文档的过时描述恢复已经替换的实现，也不把计划中的能力写成已完成。
