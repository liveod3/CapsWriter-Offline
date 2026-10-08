# 配置 GPU 推理

ONNX 编码器与 GGUF 解码器分别选择后端。修改模型、Provider、GPU 开关或对齐进程设置后，需要等待任务完成并重启服务端。

## 选择后端

| 组件 | 配置位置 | 常用值 |
| --- | --- | --- |
| ONNX 编码器 | 对应模型参数类的 `onnx_provider` | `CPU`、`DML` |
| GGUF 解码器 | 对应模型参数类的 `llm_use_gpu` | `True` 使用 GPU，`False` 使用 CPU |
| DirectML 填充 | 对应模型参数类的 `dml_pad_to` | 模板通常为 30 秒 |

在 `config_server.py` 中修改实际使用的类属性，不要新增一个没有被应用读取的构造调用。例如：

```python
class Qwen3ASRGGUFArgs:
    # Keep the other existing fields in this class.
    onnx_provider = 'DML'
    llm_use_gpu = True
```

此片段只展示相关字段，不能替换整个类。Qwen ASR 与 ForcedAligner 模板使用 DML；SenseVoice 与 Fun-ASR-Nano 模板使用 CPU。实际配置可能不同，完整默认值见[服务端模板](../../config_templates/config_server_template.py)。

DirectML 填充用于减少短音频输入形状变化。固定形状可能减少重复准备工作，也可能增加计算量；不能保证所有显卡都更快。比较时使用相同音频，并分开记录首次加载与稳定运行耗时。

## 理解对齐进程

ASR 常驻识别子进程。ForcedAligner 位于独立兄弟进程，文件任务首次需要对齐时才加载。达到 `aligner_idle_timeout` 后，整个对齐进程退出，由主进程补位空载进程。`0` 表示常驻。

对齐超时或异常退出会触发有界失败与服务停止，以回收卡住的进程；不要按旧文档假定此时总能跳过时间戳继续成功。检查终端原因，再重新启动服务端。

显存紧张时，可以在 `ForceAlignerGGUFArgs` 中将 `onnx_provider` 设为 `CPU`、`llm_use_gpu` 设为 `False`，再比较耗时。对齐与 ASR 的资源配置彼此独立。

## 排查 GPU 问题

1. 检查服务端日志中的实际 Provider、模型加载结果和原生后端诊断。
2. 用同一音频比较 CPU 和 GPU，区分采集问题与推理问题。
3. 若仅 GPU 失败，记录 GPU、驱动、运行库和模型版本后再调整配置。

不要直接用“最新版” DLL 覆盖现有后端：Python 绑定可能依赖特定 ABI。源码的 llama.cpp 下载入口指定了版本，参阅[构建指南](../development/build.md)。集显兼容环境变量见服务端模板，按具体故障逐项验证。

### 驱动更新后连续识别失败

`DmlFusedNode` 报错中的 `887A0005` 表示 DirectML 设备已失效。运行期间更新显卡驱动可能使已加载的会话失效；持续调用旧会话会让后续听写反复失败。这与连接空闲超时是不同的故障。微软说明失效设备必须释放并重建，参阅 [DirectML 设备移除处理](https://learn.microsoft.com/en-us/windows/ai/directml/dml-errors)。

等待驱动安装完成后，退出并重新启动软件，确保服务端也已重启并重新加载模型；仅重启客户端不会重建服务端的 GPU 会话。更新驱动前先结束转写并退出软件，可以避免这个问题。

2026-10-07 的一次报告发生在 NVIDIA 驱动更新之后。对此采用重启软件的处理方式，不增加自动重建会话、推理重试或冷却机制，也不调整连接空闲超时行为。局部 ONNX 恢复无法保证其他 GPU 后端同时恢复，不值得为此增加日常运行和维护复杂度。

## GPU 预加速与显存提示

`gpu_boost_enabled` 默认关闭。启用后，服务端会执行 `gpu_boost_cmd` 与 `gpu_unboost_cmd`；模板示例通过 NVIDIA `nvidia-smi` 锁定和恢复显存频率。这些命令依赖硬件支持与权限，不能将模板中的频率直接视为适用于所有显卡。

显存压力提示只做低频采样，不参与调度，也不能证明已经发生显存换页。没有 `nvidia-smi` 时会停用该提示。GPU 预加速和显存提示是独立功能。
