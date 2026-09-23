# 选择日志和内容记录

`logs` 帮助排查程序问题，`records` 保存你自己的文字和录音。同一段文字可以在两边各有一份，开关互不替代。更改配置后，日志设置需重启对应进程；文字和录音设置在当前任务完成后生效。

## 先选择用途

| 你想做什么 | 建议设置 | 后果 |
| --- | --- | --- |
| 日常使用，保留详细故障线索 | `save_diagnostic_logs=True`、`log_level='DEBUG'` | 保存详细运行事件，自动轮转清理；不因此保存正文 |
| 减少运行细节 | `log_level='INFO'` | 保留任务开始、完成和异常，省略分块、内部状态等细节 |
| 对照识别前后或 LLM 前后文字排错 | 另开 `diagnostic_include_text=True` | 日志会含识别文字、LLM 输入/提示词/回复等私人内容 |
| 排查光标参考是否影响 LLM | 客户端另开 `diagnostic_include_context=True` | 还保存本次实际发送的光标参考；需要同时开启正文诊断 |
| 不在磁盘保存诊断 | `save_diagnostic_logs=False` | 仍有终端反馈，但无法事后查该次诊断；不会删除旧文件 |
| 只保存听写历史 | `save_transcripts=True`、`save_audio=False` | 保存最终文字，不保存录音 |
| 保存成功的 LLM 处理过程 | `save_llm_records=True` | 在同一条文字历史中附加输入、系统提示词、返回结果和请求标识 |
| 文字历史也要包含光标参考 | 另开 `save_llm_context=True` | 需要开启 LLM 记录；不增加任何光标读取或网络请求 |
| 回听录音 | `save_audio=True` | 保存 MP3/WAV；录音不会自动过期删除 |

配置模板中的正文诊断、上下文诊断、LLM 内容记录和录音保存默认关闭。DEBUG 默认开启。实际安装保留自己的配置值，不应直接用模板覆盖。

客户端和服务端各有自己的日志开关。要完全关闭两端诊断保存，需要分别修改 `config_client.py` 的 `ClientConfig` 和 `config_server.py` 的 `ServerConfig`。费用账目、文件转录输出和用户记录不受诊断开关控制。

## 找到文件

| 内容 | 新模板默认位置 | 阅读方式 |
| --- | --- | --- |
| 客户端诊断 | `logs/client/YYYY/MM/client-运行标识.jsonl` | 下方阅读命令；也可以用文本编辑器打开 |
| 服务端诊断 | `logs/server/YYYY/MM/server-运行标识.jsonl` | 同上；主进程、ASR、对齐进程分别有文件 |
| 听写与成功 LLM 记录 | `records/transcripts/YYYY/MM/DD.md` | 任意 Markdown 或文本编辑器 |
| 麦克风录音 | `records/audio/YYYY/MM/` | 音频播放器；实际位置服从 `audio_dir` |

相对目录基于应用目录，支持绝对路径、环境变量和 `~`。`audio_dir=''` 保留旧版每用户目录语义；已有安装的自定义值不变。应用目录不可写时，应指定可写目录。文件转录的 TXT/SRT/JSON 仍保存在原输出位置，不额外复制到听写历史。

诊断文件夹按运行开始日期分组；跨午夜继续写同一运行文件。每条事件有完整时间及时区。文字历史则按每次录音开始日期选择日文件。

不再同时生成 `client_latest.log`、`server_latest.log` 或 `transcribe` 副本。文件转录汇总显示当前客户端诊断路径；关闭诊断保存时不显示虚假的文件路径。托盘的诊断目录入口打开当前客户端目录。

## 阅读和筛选诊断

在源码环境中，以下 `python` 指项目的 `capswriter` 环境解释器：

```powershell
# 人可读视图，不显示另行保存的正文副本
python scripts/read_logs.py logs/client

# 只查看警告及更严重的问题
python scripts/read_logs.py logs/server --level WARNING

# 按完整任务 ID 定位；只对带该字段的事件生效
python scripts/read_logs.py logs/server --task TASK_ID

# 对照 LLM 请求，明确显示已保存的敏感正文
python scripts/read_logs.py logs/client --request REQUEST_ID --content

# 提供给脚本的 JSONL 视图；正文同样需要显式加 --content
python scripts/read_logs.py logs/client --level ERROR --json
```

JSONL 表示每行一个 JSON 对象，适合程序解析。阅读命令不生成另一份永久日志，也不改变原文件。跨进程文件按路径逐个读取，不保证全局时间排序；需要精确对齐时按记录中的时间和任务标识判断。

## 级别怎样选择

| 级别 | 意思 | 例子 |
| --- | --- | --- |
| DEBUG | 内部细节 | 音频流停止、文字合并计数、格式化前后长度 |
| INFO | 正常的重要步骤 | 任务开始/完成、模型加载、LLM 请求耗时 |
| WARNING | 出现问题，但有恢复或降级办法 | 对齐降级、LLM 失败后保留原文 |
| ERROR | 某项操作失败 | 当前识别任务失败、文字记录写入失败 |
| CRITICAL | 整个服务无法继续 | 留给不可恢复的服务级故障；不是所有模块都使用此级别 |

选择某一级会包含它和更严重的级别。DEBUG 包含全部；INFO 也包含 WARNING、ERROR、CRITICAL。正文诊断事件使用 INFO：即使开启正文开关，设置为 WARNING 或 ERROR 也不会保存这些正文事件。

通常保留 DEBUG 即可，不必日常调整。INFO 适合减少体积。WARNING/ERROR 适合只关注异常，但会缺少问题前后的正常过程。终端主要显示警告和错误，识别正文和进度属于产品显示，不等同于诊断日志。

## 空间、清理和内容边界

- 默认每个进程文件约 10 MiB 轮转，保留 5 个旧分段。
- 默认清理超过 30 天的非活动诊断运行文件；客户端、服务端各有 200 MiB 软预算。
- 当前仍在使用的运行文件不会被预算清理，所以多个活动进程可能临时超预算。
- `diagnostic_log_retention_days=0` 只关闭按天过期；轮转和容量预算仍生效。
- 正文诊断每个字段默认最多保存 16,000 字符，超出时记录原长度并标明截断；Markdown 内容记录不受该限制。
- 诊断清理不删除文字历史、录音、费用数据库、旧版日志或测试临时目录。

开启正文或上下文副本后，日志就是含私人内容的文件。分享前检查需要提供的那次运行，不要直接上传整个目录。诊断不会主动保存认证头、配置中的密钥或音频二进制，也不会为了记录多读一次剪贴板或光标内容。

日志写入在有界后台队列中进行。磁盘慢或队列满时，识别继续，部分诊断可能丢失；后续可写时报告丢弃数量。强制结束进程也可能丢失最后少量诊断。日志不是保证完整的事务账本。

## 旧文件怎么办

新程序不会自动迁移或删除旧文件。停止旧版客户端和服务端后，可把旧 `logs` 保存为应用目录旁的 `old-logs-日期`，保留相同目录深度，避免破坏历史音频相对链接。仍在使用的日志应保留原位，不能边运行边强制清理。

旧配置中的 `file_separate_log` 仅保留兼容，不再创建额外文件。已有 `transcript_dir` 和 `audio_dir` 值继续生效；要采用新目录，明确修改它们。详细字段、记录结构和实现边界见[技术参考](../reference/logging-and-records.md)。
