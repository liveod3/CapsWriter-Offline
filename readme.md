# CapsWriter-Offline

![demo](assets/demo.png)

面向 Windows 的语音输入与文件转录工具。默认右 Ctrl 或鼠标 X2，按一次开始录音、再按一次结束并输入文字；已有安装以本机配置为准。

默认识别链路可完全离线。LLM 文本处理默认关闭，启用云 Provider 后会外发当次文本；光标参考和 UDP 也需按各自配置判断。当前没有覆盖所有组件的严格离线总开关。

## 功能

- 语音输入：支持切换或长按录音、暂停恢复、录音及处理状态提示。
- 文件转录：支持媒体文件与目录，输出 SRT、TXT 和时间戳 JSON；可用配套 TXT/JSON 重建字幕。
- 识别引擎：Paraformer、SenseVoice-Small、Fun-ASR-Nano、Qwen3-ASR，按模型能力选择 CPU 或 GPU。
- 文本动作：可选单次纠错、翻译与光标参考，无会话历史。
- 独立记录：文字默认保存到 `logs/transcripts/YYYY/MM/DD.md`；音频和 LLM 动作记录默认关闭，可分别开启。

主要目标平台为 Windows 10/11（64 位）。Windows 7、Linux 和 macOS 尚未验证或提供当前版本的兼容保证。

## 快速开始

1. 按[环境依赖说明](docs/环境依赖安装说明.md)准备 VC++ 运行库；文件转录需要 PATH 中可找到 FFmpeg。
2. 下载 [软件发行包](https://github.com/HaujetZhao/CapsWriter-Offline/releases/latest)，按[模型说明](docs/模型下载的若干问题.md)下载并放置模型。
3. 启动 `start_server.exe`，再启动 `start_client.exe`。默认启用托盘菜单。
4. 按右 Ctrl 或鼠标 X2 开始录音，再按一次结束。快捷键与录音模式可在 `config_client.py` 中调整。

发行包与当前工作区可能有差异；[更新日志](docs/CHANGELOG.md)区分未发布改动和历史版本。

## 从源码运行

环境选择、依赖安装与开发检查见 [AGENTS.md](AGENTS.md)。当前工作区使用 Conda 环境 `capswriter`；以下 `python` 指该环境的解释器。

首次运行，仅在本机配置不存在时复制模板：

```powershell
if (!(Test-Path config_server.py)) { Copy-Item config_templates/config_server_template.py config_server.py }
if (!(Test-Path config_client.py)) { Copy-Item config_templates/config_client_template.py config_client.py }
```

在两个终端分别执行：

```powershell
python start_server.py
python start_client.py mic
```

本机也可运行 `./start_capswriter.ps1`，脚本会定位 `capswriter` 环境并打开两个终端；支持 `-ServerOnly`、`-ClientOnly` 和预览用的 `-WhatIf`。

文件转录需要服务端；字幕重建不需要服务端或麦克风：

```powershell
python start_client.py transcribe --format srt,txt,json "D:\Videos"
python start_client.py rebuild-srt --text "edited.txt" --json "timestamps.json"
python start_client.py --help
```

打包版支持拖拽媒体，以及同时拖入配套 TXT 和 JSON 重建 SRT。

## 配置与文档

实际运行读取根目录的 `config_client.py` 和 `config_server.py`，它们是 Git 忽略的本机配置。默认值与字段说明见[配置模板](config_templates/README.md)。升级时只合并必要字段，保留已有设备、模型、快捷键和开关。

| 需要了解 | 文档 |
| --- | --- |
| Provider、预设、光标参考、记录开关与旧配置迁移 | [文本动作与记录](docs/文本动作与记录.md) |
| 媒体转录、输出格式与字幕重建 | [文件转录](docs/文件转录功能如何使用.md) |
| 模型选择与下载 | [模型说明](docs/模型下载的若干问题.md) |
| 识别语言 | [语言配置](docs/识别语言如何配置.md) |
| DirectML、Vulkan 与对齐进程 | [显卡加速](docs/显卡加速的若干问题.md) |
| LAN 认证与 TLS | [局域网连接](docs/局域网连接安全配置.md) |
| 输入、麦克风与记录排障 | [常见问题](docs/常见问题.md) |
| 当前开发计划 | [TODO](TODO.md) |
| 历史改动 | [CHANGELOG](docs/CHANGELOG.md) |

## 致谢

本项目基于 [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx) 与 [FunASR](https://github.com/alibaba-damo-academy/FunASR) 等开源项目。感谢原作者、贡献者及捐助者。

![sponsor](assets/sponsor.jpg)
