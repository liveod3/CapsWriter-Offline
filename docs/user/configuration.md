# 管理配置与记录

程序读取根目录的 `config_client.py` 和 `config_server.py`，Git 跟踪的是[默认模板](../../config_templates/README.md)。编辑实际文件时保留现有取值；不要用模板整体覆盖。

## 修改运行配置

常用客户端设置、提示词模块和服务商参数可从[设置窗口](settings-gui.md)修改。下面的文件编辑方式仍适用于高级选项和服务端配置。

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

## 通过命令行校验和修改

在已准备好的项目 Python 环境中执行：

```powershell
python start_client.py settings check
python start_client.py settings show
python start_client.py settings --server check
```

这些命令不会启动麦克风、模型或窗口，也不会执行本机配置中的 Python 代码。
`show` 输出已保存值和 `revision`，凭据字段会隐藏。它只知道文件状态，不会把磁盘配置
当作另一个正在运行的进程已经生效的配置，因此运行状态字段显示为 `null`。

保存时提供上次读取的版本和要修改的字段。下面的 PowerShell 示例将界面语言改为中文，
并用 `-` 从管道读取 JSON，避免 Windows 旧版命令行对 JSON 引号的处理差异：

```powershell
$settingsView = python start_client.py settings show | ConvertFrom-Json
'{"ui_language":"zh-CN"}' | python start_client.py settings set - --revision $settingsView.revision
```

命令会校验整份候选文件，保留注释、换行和未修改的值。如果配置已被其他编辑器修改，
保存会被拒绝；重新读取并确认新内容后再保存。非法值或保存失败不会覆盖原文件。
列表或字典的值内部带有注释时，整体修改该字段会提示改用文件编辑，避免丢失注释。
包含自定义执行逻辑的配置仍可按原方式启动和手工编辑，但结构化保存要求先整理为声明式配置。

托盘的语言和 LLM 开关也使用同一保存接口。连续点击会修改上次保存的值；菜单勾选仍表示
当前生效值，任务未结束时两者可能暂时不同。完成保存不等于立即生效，仍遵守上面的任务边界
和重启要求。Provider、预设 TOML 的编辑方式暂时不变。

## 选择录音目录

`save_audio=True` 才保存录音。`audio_dir` 决定新录音位置：

| 值 | 位置 |
| --- | --- |
| `'records/audio'` | 新模板默认值，应用目录内的 `records/audio` |
| `''` | Windows 的 `%LOCALAPPDATA%/CapsWriter-Offline/audio` |
| `'audio-data'` | 应用目录中的 `audio-data`，适合便携使用 |
| `r'D:\DictationAudio'` | 指定绝对目录，也可在其他磁盘 |

目录支持环境变量与 `~`。相对路径基于应用目录，不基于当前终端目录。新文件按录音开始日期保存为 `<audio_dir>/YYYY/MM/录音文件.mp3`，无 FFmpeg 时使用 WAV。

显式目录不可写时会提示保存失败，并继续识别，不会悄悄改存到别处。托盘“打开录音目录”打开当前生效目录。录音没有自动保留期或自动删除。

旧 `YYYY/MM/assets/` 文件和已有链接保留原位。更改目录不会迁移、搜索重定向或删除旧录音；需要时直接打开旧目录。便携迁移时一并保留旧年份目录、文字记录与音频数据，跨盘链接可能使用本地文件 URI。

## 区分保存内容

诊断放在 `logs/client`、`logs/server`；新模板中的文字和录音放在 `records/transcripts`、`records/audio`。已有自定义目录继续生效。

正文可以同时保存在用户历史和诊断中，两边独立控制。正文诊断与光标参考诊断都需要显式开启，DEBUG 本身不授予内容保存权限。诊断自动轮转清理，用户记录不自动删除。

完整的四类记录、开关后果、推荐用法和阅读命令见[选择日志和内容记录](logs-and-records.md)。
