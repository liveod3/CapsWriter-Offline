# 使用纠错、翻译与光标参考

文本动作在一次听写完成后执行最多一个 LLM 预设。默认关闭，没有会话历史，不读取剪贴板或选区。云端 Provider 会收到当次文本；启用光标参考时还可能收到周围文字。

启用后会独立记录每次请求的用量与费用元数据，详见[查询 LLM 费用与设置提醒](llm-costs.md)。这不受转写或 LLM 原文保存开关影响，也不保存请求正文。

## 配置 Provider

1. 从托盘的设置菜单打开 Provider 配置。首次编辑会从 `LLM/providers.template.toml` 创建本机 `LLM/providers.toml`；已有文件不会覆盖。
2. 在本机文件中设置连接地址、模型和密钥。公开模板中的空密钥是占位值，不能填入真实凭据。
3. 检查 `LLM/presets.toml` 的 `provider` 指向需要使用的连接 ID。
4. 在客户端配置或托盘中开启需要的能力，等待当前任务结束后生效。

`api_key_env` 显式指定后优先于 `api_key`；变量为空会报错，不回退到文件中的密钥。环境变量改变后要重启客户端，使新进程读取它。仅修改 Provider 或预设 TOML 时，下次请求会重读，无需重启。

`kind = "openai"` 指兼容协议，不能据此判断请求发给哪家公司。实际目标由 `base_url` 决定；客户端会追加 `/chat/completions`，不要重复填写。`local` 这样的连接名称同样不能证明实际地址在本机。示例以[公开模板](../../LLM/providers.template.toml)为准，外部服务的模型可用性和价格以提供方为准。

## 选择处理方式

在 `ClientConfig` 中设置：

```python
llm_enabled = True
llm_correction_enabled = True
llm_translation_enabled = True
llm_default_preset = 'correct_asr'
```

此示例会启用 LLM；模板默认 `llm_enabled=False`。总开关关闭时不请求任何 Provider。

| 默认预设 | 普通听写 | 显式口令 |
| --- | --- | --- |
| `'correct_asr'` | 纠错一次 | 只执行指定动作 |
| `'translate'` | 翻译一次 | 只执行指定动作 |
| `None` | 不自动处理 | 执行匹配动作一次 |

最多选择一个自动预设，不能用数组串联动作。内置纠错与翻译各有独立开关；关闭能力后，自动和显式触发都不会调用它。自定义预设受总开关约束。

内置翻译口令是“翻译”和 `Translate:`，例如“翻译：今天有点冷”。匹配发生在开头，最长匹配优先，口令去除后剩余文字作为输入。`triggers = []` 禁用口令。显示名与稳定预设 ID 分开，界面语言不会改变口令。

纠错提示词要求结合当前文本修正有依据的同音误识别、赘词和标点，保留原意、有效强调、数值与单位；不回答转写中的问题。翻译提示词默认译为英文。实际效果取决于模型，不能保证每次纠正都准确。提示词是功能输入，内部文档英文化不会翻译这些提示词。

## 启用光标参考

默认 `caret_context_enabled=False`。确需读取插入点附近文字时，在 `ClientConfig` 中开启，并按需调整范围：

```python
caret_context_enabled = True
caret_context_before_chars = 800
caret_context_after_chars = 200
```

设置在安全任务边界生效。每次录音固定一份快照，分片复用它；下一次录音才重新读取。隔离子进程最多等待约 1.5 秒，密码框、选区、焦点改变、不支持的控件或超时均降级为没有参考，不通过模拟复制读取。

采集优先使用可编辑控件的 `TextPattern2`；接口不支持，或其光标未激活但编辑控件仍具有键盘焦点时，尝试独立验证基础 `TextPattern` 返回的单个空选区。读取范围限制在当前编辑控件内。密码框、只读或无法确认可编辑的控件、有文字选区的控件仍不会提供参考。Obsidian 已通过一次合成文本读取实测；当前 Sublime Text 安装未暴露可用文本控件，尚不能读取。不能把某一应用读取成功视为所有编辑器都支持，实际验证范围见[兼容性记录](../validation/P1-caret-context.md)。

快照只包含**开始录音时当前输入框或编辑区域内**光标附近的文字，不读取页面上方聊天记录、历史听写或其他文档。空聊天输入框返回空参考是正常现象。

开启诊断日志且日志级别包含 INFO 时，可在 `logs/client_latest.log` 中核对：

- `Caret capture`：`task` 对应录音任务，`status` 为采集结果，`method` 为使用的接口，`before_chars` / `after_chars` 为两侧字符数。
- `LLM request started`：`context_chars` 为该次请求实际附带的参考字符数（含插入点标记），`context_allowed` 表示预设是否允许参考。采集成功但预设不允许时，仍为 0。

常见采集状态：`captured` 为成功，`empty` 为附近无文字，`unsupported_text_pattern` / `unsupported_control` 为控件不支持，`selection` 为存在选区或无法取得单个光标，`readonly_or_unknown` 为只读或无法确认，`focus_changed` 为焦点改变，`timeout` 为子进程超时，`provider_error` 为 UI Automation 读取异常。`disabled` 表示功能关闭。诊断只记录状态、字数和耗时，不记录正文或窗口标题。

`caret_mismatch` 的 `reason` 进一步区分：`range_not_collapsed` 为光标范围不是单点，`caret_selection_disagree` 为光标与空选区位置不一致，`caret_before_control` / `caret_after_control` 为光标落在控件暴露的文本范围之外，`selection_changed` 为采集前后插入位置发生变化。其他状态的原因为 `none`。这些状态是接口校验结果，不能仅凭它们认定用户移动了光标。

参考通过 ASR `context` 字段发送给配置的服务端，Qwen/Fun-ASR 可使用它，SenseVoice/Paraformer 不使用。若服务端在另一台机器，这也是一次文本外发。

LLM 还需预设的 `use_caret_context=true` 才附带参考。内置纠错允许它，翻译默认不允许。关闭 LLM 不会关闭独立的 ASR 光标参考。

纠错预设用 `[Insertion point]` 区分左右已有文字，只输出待插入片段。提示词要求按插入后的完整句子判断边界标点，避免重复已有标点或改写周围文字；这依赖参考成功捕获和模型遵循提示。

## 取消或处理失败

录音结束后显示转写状态，再衔接 LLM 等待状态。完成后一次性输出结果；焦点窗口改变时不自动插入，可从托盘复制最近结果。

按 `llm_stop_key`（默认 Esc）取消时，不自动上屏。配置、认证、超时或网络错误保留待处理原文，没有自动重试。托盘还可复制最初 ASR 转写。状态中的 Local/Remote 按地址区分，Remote 可以是局域网或云端。

## 迁移旧角色和热词

旧 Python 角色、会话历史、热词检索、音近及正则强制替换已退出运行链路。升级前保留原有配置与数据，静态读取需要迁移的内容，不执行旧 `LLM/*.py`。

把地址、模型和凭据迁入本机 Provider，把需要的提示词迁入预设；不迁移旧选区、历史或输出方式字段。模型词表、CTC 解码和对齐仍保留。移除替换规则不代表 ASR 本身不会误识别。

文字、音频、动作记录分别控制，参阅[配置与记录](configuration.md)。
