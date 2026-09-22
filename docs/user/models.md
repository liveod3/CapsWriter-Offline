# 配置识别模型

语音识别模型在本地运行，模型文件与程序分开发行。LLM 文本动作是另一条可选链路，不受本页的本地识别设置约束。

## 选择引擎

| `ServerConfig.model_type` | 模型 | 本项目使用的后端 | 时间戳与标点 |
| --- | --- | --- | --- |
| `qwen_asr` | Qwen3-ASR | ONNX 编码器、GGUF 解码器 | 自带标点；文件对齐使用独立 ForcedAligner |
| `fun_asr_nano` | Fun-ASR-Nano | ONNX 编码器/CTC、GGUF 解码器 | CTC 时间戳，自带标点 |
| `sensevoice` | SenseVoice-Small | ONNX | 自带时间戳和标点 |
| `paraformer` | Paraformer | sherpa-onnx | 自带时间戳；按需加载独立标点模型 |

模板选择 `qwen_asr`。实际速度和准确率取决于音频、硬件、驱动、量化与配置；本指南不提供未经同条件测试的星级或固定延迟排名。先用短样本确认输入质量与结果，再比较适合本机的引擎。

## 下载并放置文件

1. 打开项目的[模型发行页](https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models)，下载选定引擎的模型。
2. 解压到应用的 `models` 目录。
3. 对照 `config_server.py` 的 `ModelPaths` 核对层级及文件名。默认路径见[服务端模板](../../config_templates/config_server_template.py)。
4. 设置 `ServerConfig.model_type`，保存后重启服务端。

Qwen 默认目录示例：

```text
models/
├── Qwen3-ASR/
│   └── Qwen3-ASR-1.7B/
│       ├── qwen3_asr_encoder_frontend.onnx
│       ├── qwen3_asr_encoder_backend.onnx
│       └── qwen3_asr_llm.gguf
└── Qwen3-ForcedAligner/
    └── Qwen3-ForcedAligner-0.6B/
        ├── qwen3_aligner_encoder_frontend.int4.onnx
        ├── qwen3_aligner_encoder_backend.int4.onnx
        └── qwen3_aligner_llm.q5_k.gguf
```

不要重复套一层同名文件夹。其他引擎以 `ModelPaths` 对应字段为准。只下载已选择模型所需的文件，不必为普通源码检查下载模型。

## 检查加载结果

启动服务端后，确认模型检查通过，并查看实际选择的 ONNX Provider 与 GGUF 后端。若提示文件缺失，按提示核对路径；不要把更改模型名当作修复不匹配模型文件的办法。

Qwen 的对齐模型只在文件任务需要时间戳时加载。服务端启动后暂时看不到对齐模型的 GPU 占用是正常现象。对齐能力缺失时，字幕时间戳质量仍需人工检查；参阅[文件转录](transcription.md)。

## 相关说明

- [识别语言](languages.md)。
- [GPU 配置与对齐进程](gpu.md)。
- [模型加载故障](troubleshooting.md)。
