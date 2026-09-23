# 转录文件与重建字幕

将音视频文件转为文本、SRT 和时间戳 JSON。文件转录需要运行中的服务端与 PATH 中可用的 FFmpeg；字幕重建只使用已有 TXT/JSON，不需要模型、服务端或麦克风。

下列命令在应用根目录执行，`python` 指已准备的项目解释器。环境准备见[开发环境](../development/setup.md)。

## 转录一个文件

1. 启动服务端并等待模型就绪。
2. 执行转录：

   ```powershell
   python start_client.py transcribe --format srt,txt,json "D:\Videos\lecture.mp4"
   ```

3. 等待终端显示完成汇总，检查媒体文件旁的输出。

通过 Conda 运行并需要实时进度时，使用：

```powershell
conda run --no-capture-output -n capswriter python -u start_client.py transcribe "D:\Videos\lecture.mp4"
```

`--no-capture-output` 避免 Conda 捕获输出，`-u` 关闭 Python 输出缓冲。

## 批量转录

一次可以传入多个文件或目录：

```powershell
python start_client.py transcribe "D:\Videos\a.mp3" "D:\Videos\b.mp4" "D:\Courses"
python start_client.py transcribe --recursive "D:\Videos"
python start_client.py transcribe --no-recursive "D:\Videos"
```

目录扫描按路径排序并去重，只纳入 `file_media_extensions` 中的扩展名。未传递扫描选项时，使用 `file_scan_recursive`。直接指定的媒体文件仍需 FFmpeg 支持其格式。

发行包支持把媒体文件或目录拖到 `start_client.exe`；裸路径会转换为转录命令，并使用配置默认值。源码也保留裸路径兼容入口。

## 选择输出格式

| 格式 | 内容 | 模板默认 |
| --- | --- | --- |
| `srt` | 带时间轴的字幕 | 开启 |
| `txt` | 按标点分行的文本 | 开启 |
| `json` | 识别文本、tokens 与时间戳 | 开启 |
| `merge` | 未分行的 `.merge.txt` | 关闭 |

`--format` 或 `-f` 完整覆盖本次输出集合。例如，`--format srt,txt` 只保存两种格式，也可重复使用 `-f srt -f txt`。这些参数不写回配置，不跳过 ASR 或文件对齐。

结果保存在输入文件旁。若任一启用格式已有同名文件，程序会为整组结果选择相同编号，例如 `lecture (2).srt`、`lecture (2).txt`。检查完成提示中的实际路径；输出失败时，不要只凭目录里已有同名文件判断本次成功。

文件任务写入同一份客户端诊断，完成汇总显示当前文件路径；不再创建 `transcribe` 副本。`file_separate_log` 是已停用的兼容字段。诊断开关、正文副本和阅读方法见[日志指南](logs-and-records.md)。

## 校对后重建 SRT

1. 编辑生成的 TXT，修改错字并调整分行。
2. 保留同一段音频的原始时间戳 JSON。
3. 指定两个文件：

   ```powershell
   python start_client.py rebuild-srt --text "edited.txt" --json "timestamps.json"
   ```

程序按已有时间戳对齐新文本，不会重新识别音频。TXT 和 JSON 可以不同名，但两者都必须提供。打包版可同时拖入恰好一个 TXT 和一个 JSON。已有 SRT 使用新编号避让。

## 检查时间轴和资源限制

时间戳质量取决于模型与对齐结果。Qwen ASR 不直接产生 token 时间戳，文件任务通常需要 ForcedAligner。当前结果尚未完整标注时间戳来源/质量；生成了 SRT 不等于时间轴已经准确测量。交付字幕前试听并检查分句边界。

转录并非无条件不限时长。模板限制单任务音频总量、任务持续时间、连接任务数和队列容量；默认单任务音频上限为四小时，任务持续时间上限为六小时，另有 I/O 和无进度超时。配置及限制以[服务端模板](../../config_templates/config_server_template.py)为准。

多个文件任务和麦克风可以共享服务端，各连接/任务相互隔离。当前只有一个 ASR Worker，任务轮转共享推理资源；文件分片处理期间听写可能等待。

## 相关设置

分片长度与重叠由 `file_seg_duration`、`file_seg_overlap` 控制。输出默认值是 `file_save_srt`、`file_save_txt`、`file_save_json` 和 `file_save_merge`。不要把较大分片简单等同于更高效率，它也影响延迟、内存和引擎限制。

查看完整参数：

```powershell
python start_client.py transcribe --help
python start_client.py rebuild-srt --help
```
