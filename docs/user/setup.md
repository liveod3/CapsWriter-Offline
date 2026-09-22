# 安装与启动

本指南适用于 Windows 10/11（64 位）发行包。源码用户参阅[开发环境](../development/setup.md)。Windows 7、Linux 和 macOS 尚无当前版本的兼容保证。

## 准备依赖

安装 [Microsoft Visual C++ x64 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)。缺少 DLL 时，先记录具体名称；运行库、推理后端和模型路径都可能造成加载失败，不能仅凭“缺少 DLL”判断原因。

文件转录需要 FFmpeg，提前显示媒体时长还需要 ffprobe：

1. 从 [FFmpeg 下载页面](https://ffmpeg.org/download.html)选择 Windows 构建。
2. 解压后，将包含 `ffmpeg.exe` 和 `ffprobe.exe` 的 `bin` 目录加入 PATH。
3. 重新打开终端，执行以下命令确认可用：

   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

FFmpeg 也用于保存 MP3 录音；未找到 FFmpeg 时，录音保存可退回 WAV。文件转录不能使用这一退路。

## 启动应用

1. 解压发行包到准备长期使用的目录。
2. 按[模型配置](models.md)下载模型并核对 `config_server.py` 中的路径。
3. 运行 `start_server.exe`，等待模型加载完成。
4. 运行 `start_client.exe`，检查终端显示的设备和快捷键。
5. 将光标放入文本框，按右 Ctrl 或鼠标 X2 开始录音，再按一次结束。

服务端负责识别，客户端负责采集与输入。只运行其中一个，不能完成听写。

## 调整快捷键

编辑根目录 `config_client.py` 的 `ClientConfig.shortcuts`。模板默认两种快捷键均为切换模式：`hold_mode=False`。长按模式使用 `True`，按下开始、松开结束。不要把历史版本的 CapsLock 长按说明用于所有安装。

`suppress` 控制是否阻止原按键传给其他应用，`threshold` 控制激活阈值。字段说明和按键名见[客户端模板](../../config_templates/config_client_template.py)。快捷键属于需要重启客户端的配置。

## 使用托盘

托盘可暂停或恢复听写、重连麦克风、复制最近结果、打开配置与记录目录、切换界面语言，以及退出程序。菜单文字随界面语言变化。

手动暂停需要从菜单恢复；录音键不会解除手动暂停。闲置挂起仍允许录音键唤醒设备。隐藏控制台不会停止识别。

## 下一步

- [调整配置与保存位置](configuration.md)。
- [转录媒体文件](transcription.md)。
- [排查无输入或设备问题](troubleshooting.md)。
