# 设置语言

界面语言、语音识别语言和 LLM 翻译目标分别设置。切换菜单语言不会翻译你的识别结果。

## 切换界面语言

在客户端托盘的语言菜单中选择 English、简体中文或跟随系统。选择保存为 `ClientConfig.ui_language`，值分别为 `en`、`zh-CN`、`auto`。

客户端等待当前任务结束后应用。相同安装目录中的本地服务端跟随该设置；远程客户端不会通过网络更改服务端界面语言。终端提示可本地化，诊断文件保持英文。实现细节见[本地化说明](../development/localization.md)。

## 设置语音识别语言

在 `config_client.py` 的 `ClientConfig` 中设置：

```python
language = 'chinese'
```

语言名不区分大小写。支持范围以当前[语言映射](../../core/server/engines/language.py)和所安装模型为准：

| 引擎 | 语言选择 |
| --- | --- |
| Paraformer | 当前使用中文模型，不提供语言切换 |
| SenseVoice | `auto`、`chinese`、`english`、`cantonese`、`japanese`、`korean` |
| Fun-ASR-Nano | `chinese`、`english`、`japanese`；不要把 MLT 版能力套用到标准模型 |
| Qwen3-ASR / ForcedAligner | 映射表包含中文、英文、粤语、日语、韩语及其他语言；对齐器使用明确语言名 |

`auto` 的处理方式因引擎而异，不保证所有引擎都执行自动检测。指定已知语种可以避免依赖默认回退。语言配置在安全任务边界重载，不改变已经开始的任务。

## 设置 LLM 翻译目标

修改 `LLM/presets.toml` 中翻译预设的提示词。内置翻译提示词以英文为目标；界面语言与 `ClientConfig.language` 都不会覆盖它。参阅[文本动作](text-actions.md)。
