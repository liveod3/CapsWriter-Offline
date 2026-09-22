# CapsWriter-Offline

面向 Windows 10/11（64 位）的语音输入与文件转录工具。默认按右 Ctrl 或鼠标 X2 开始录音，再按一次结束并输入文字；已有安装以本机配置为准。

![语音输入演示](assets/demo.png)

默认语音识别可以完全离线运行。LLM 文本处理默认关闭；启用云端 Provider 后会发送当次文本。远程 ASR、光标参考和 UDP 各有独立配置，目前没有覆盖所有组件的严格离线总开关。

## 开始使用

1. 按[安装与启动](docs/user/setup.md)准备运行库；文件转录还需要 FFmpeg。
2. 下载[发行包](https://github.com/HaujetZhao/CapsWriter-Offline/releases/latest)，按[模型配置](docs/user/models.md)放置模型。
3. 先运行 `start_server.exe`，再运行 `start_client.exe`。
4. 将光标放入文本框，按录音快捷键开始说话，再按一次结束。

发行包可能落后于当前源码。查看[更新日志](docs/CHANGELOG.md)区分未发布改动与历史版本。源码用户从[开发环境](docs/development/setup.md)开始。

## 按任务查找说明

| 要完成的任务 | 文档 |
| --- | --- |
| 安装依赖、启动或调整快捷键 | [安装与启动](docs/user/setup.md) |
| 转录音视频、批量导出、校对字幕 | [文件转录](docs/user/transcription.md) |
| 选择识别模型、配置 GPU | [模型配置](docs/user/models.md)、[GPU 配置](docs/user/gpu.md) |
| 切换界面语言或识别语言 | [语言设置](docs/user/languages.md) |
| 启用纠错、翻译和光标参考 | [文本动作](docs/user/text-actions.md) |
| 管理录音、文字与诊断记录 | [记录与配置](docs/user/configuration.md) |
| 连接另一台电脑上的服务端 | [局域网连接](docs/user/network.md) |
| 排查输入、音频或模型问题 | [故障排查](docs/user/troubleshooting.md) |

完整导航见[文档中心](docs/README.md)。开发计划只维护在 [TODO](TODO.md)，Agent 工作规则只维护在 [AGENTS.md](AGENTS.md)。

## 配置文件

程序读取根目录的 `config_client.py` 和 `config_server.py`。它们是 Git 忽略的本机文件；两份[默认模板](config_templates/README.md)受 Git 跟踪。升级时保留本机取值，只合并必要字段。

默认保存文字记录，音频、LLM 和光标参考默认关闭。快捷键、设备、模型和保存位置均以实际配置为准。

## 致谢

本项目使用 [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx)、[FunASR](https://github.com/alibaba-damo-academy/FunASR) 等开源项目。感谢原作者、贡献者和捐助者。

![捐助信息](assets/sponsor.jpg)
