# 管理配置与记录

程序读取根目录的 `config_client.py` 和 `config_server.py`，Git 跟踪的是[默认模板](../../config_templates/README.md)。编辑实际文件时保留现有取值；不要用模板整体覆盖。

## 修改运行配置

1. 从托盘或编辑器打开需要修改的本机配置。
2. 修改类属性并保存完整、有效的 Python 文件。
3. 查看终端的应用结果；当前任务尚未结束时，等待安全边界。
4. 对提示需要重启的字段，在任务完成后重启对应进程。

监视器大约每秒读取一次，连续两次内容一致后再验证。语法错误、未知或重复字段、非法值、删除已有字段及不支持的表达式会拒绝整份候选，继续使用最后有效配置。恢复默认值时应赋回该值，而不是删除字段。

热重载只接受模板所用的声明式配置，不执行新编辑的任意 Python 函数或导入逻辑。包含自定义可执行代码的旧配置可能只能在启动时使用。

| 设置类型 | 生效时机 |
| --- | --- |
| 客户端界面、识别语言、部分文本输出、LLM 开关、光标参考、保存开关与音频目录 | 当前听写及其 ASR/LLM/输出/归档完成后；文件模式在两文件之间 |
| 服务端 `format_num`、`format_spell` | 新任务接收时固定快照 |
| 设备、快捷键、连接、模型、GPU、日志资源等 | 重启对应进程 |
| Provider 与预设 TOML | 下一次 LLM 请求 |

完整字段矩阵见[配置参考](../reference/configuration.md)。一次编辑同时包含可重载与需重启字段时，只应用可重载部分，并列出需重启字段名；磁盘上的设置仍保留。

## 选择录音目录

`save_audio=True` 才保存录音。`audio_dir` 决定新录音位置：

| 值 | 位置 |
| --- | --- |
| `''` | Windows 的 `%LOCALAPPDATA%/CapsWriter-Offline/audio` |
| `'audio-data'` | 应用目录中的 `audio-data`，适合便携使用 |
| `r'D:\DictationAudio'` | 指定绝对目录，也可在其他磁盘 |

目录支持环境变量与 `~`。相对路径基于应用目录，不基于当前终端目录。新文件按录音开始日期保存为 `<audio_dir>/YYYY/MM/录音文件.mp3`，无 FFmpeg 时使用 WAV。

显式目录不可写时会提示保存失败，并继续识别，不会悄悄改存到别处。托盘“打开录音目录”打开当前生效目录。录音没有自动保留期或自动删除。

旧 `YYYY/MM/assets/` 文件和已有链接保留原位。更改目录不会迁移、搜索重定向或删除旧录音；需要时直接打开旧目录。便携迁移时一并保留旧年份目录、文字记录与音频数据，跨盘链接可能使用本地文件 URI。

## 区分保存内容

| 数据 | 开关 | 默认位置与行为 |
| --- | --- | --- |
| 听写文字 | `save_transcripts`，模板开启 | `logs/transcripts/YYYY/MM/DD.md` |
| ASR 原文对照 | `transcript_save_original`，默认关闭 | 启用文字记录且原文不同于最终结果时附加 |
| 麦克风音频 | `save_audio`，默认关闭 | 由 `audio_dir` 决定 |
| 成功文本动作的输入/输出 | `save_llm_records`，默认关闭 | `logs/text-actions/YYYY/MM/DD.md`；不保存提示词、密钥或光标参考 |
| 诊断归档 | `save_diagnostic_logs` | `logs/diagnostics/YYYY/MM/`；最新日志仍是独立入口 |
| 文件转录日志 | `file_separate_log` | `logs/transcribe/YYYY/MM/` |

只保存文字、不保存音频时，设置 `save_transcripts=True`、`save_audio=False` 即可。

`diagnostic_log_retention_days=30` 控制诊断归档过期清理；`0` 禁用自动清理。它不删除文字、音频或动作记录。诊断文件保持英文，显式内容记录保留用户语言。旧日志和原生后端输出仍应在分享前检查；不要上传私人录音或完整转写来代替最小故障说明。
