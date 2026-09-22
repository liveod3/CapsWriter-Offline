# 文档中心

从要完成的任务进入文档。用户指南使用中文；开发约定、配置参考和新验证记录使用英文。历史材料保留原始语言，并明确标注适用时间。

## 用户指南

| 任务 | 说明 |
| --- | --- |
| [安装与启动](user/setup.md) | 运行库、FFmpeg、启动顺序、快捷键 |
| [配置模型](user/models.md) | 引擎、模型文件、路径和时间戳能力 |
| [设置语言](user/languages.md) | 界面、识别、LLM 目标语言的区别 |
| [配置 GPU](user/gpu.md) | ONNX、GGUF、对齐进程和故障隔离 |
| [转录文件](user/transcription.md) | 命令行、批量输入、输出和字幕重建 |
| [使用文本动作](user/text-actions.md) | Provider、预设、取消和光标参考 |
| [管理配置与记录](user/configuration.md) | 保存位置、热重载、旧文件保留策略 |
| [连接局域网服务端](user/network.md) | 令牌、TLS 和重启要求 |
| [排查故障](user/troubleshooting.md) | 输入、麦克风、模型和诊断路径 |

## Reference and development

LLM 用量与费用：[查询费用记录](user/llm-costs.md)。

- [Configuration reference](reference/configuration.md): reload field matrix and storage semantics.
- [Development setup](development/setup.md): source environment and entry points.
- [Architecture](development/architecture.md): component ownership and task flow.
- [Localization](development/localization.md): catalogs, stable IDs, console/file language boundaries.
- [Internal language policy](development/internal-language.md): English scope, exceptions, and checks.
- [Writing guide](development/writing-guide.md): Microsoft-style structure and maintenance rules.
- [Text merging](development/text-merging.md): current algorithm and limitations.
- [Build and release](development/build.md): packaging inputs, junctions, and validation limits.

## 项目状态与证据

| 内容 | 入口 | 用途 |
| --- | --- | --- |
| 当前待办 | [TODO](../TODO.md) | 唯一开发清单及验收状态 |
| 改动记录 | [Changelog](CHANGELOG.md) | 按日期记录实现与验收 |
| 验证证据 | [Validation](validation/README.md) | 自动检查、人工范围和未验证项目 |
| 历史材料 | [Archive](archive/README.md) | 审计、旧发布说明和历史问题现场 |
| Agent 规则 | [AGENTS.md](../AGENTS.md) | 配置保护、架构约束和验证要求 |

历史文档不是当前配置指南。发现内容冲突时，先核对代码和配置模板，再更新对应主题；不要在多个页面维护互相独立的同一份规则。
