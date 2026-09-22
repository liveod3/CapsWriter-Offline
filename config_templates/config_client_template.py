import os
from collections.abc import Iterable
from pathlib import Path

# 这是受 Git 跟踪的客户端默认配置模板。
# 首次使用时将本文件复制到仓库根目录，并重命名为 config_client.py。
# 请在根目录副本中保存本机设置，不要直接修改或移动本模板。

# 版本信息
__version__ = '2.6'

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# 客户端配置
# Supported settings reload after active tasks finish. Resource settings require
# restart; see docs/configuration-reload.md for the exact policy and feedback.
class ClientConfig:
    # Interface language only: 'auto' (system), 'en', or 'zh-CN'. Reloads between tasks.
    # The server in this installation also follows this saved preference.
    ui_language = 'auto'

    addr = '127.0.0.1'          # Server 地址
    port = '6016'               # Server 端口

    # 连接远程 LAN 服务端时，通过环境变量配置与服务端相同的令牌
    auth_token = os.environ.get('CAPSWRITER_AUTH_TOKEN', '')

    # 服务端配置 TLS 后设为 True；自签名证书可通过 tls_ca_file 指定受信 CA/证书
    use_tls = False
    tls_ca_file = ''

    # 限制服务端响应在客户端的单消息大小与待消费数量。
    websocket_max_message_bytes = 16 * 1024 * 1024
    websocket_max_queue = 16
    file_max_inflight_chunks = 4

    # Per-operation deadline for file connection, decoding and sends (seconds).
    file_io_timeout = 60.0
    # Maximum wait without advancing recognition progress, not total file time.
    # Increase for slow hardware or heavily concurrent file/dictation workloads.
    file_result_timeout = 600.0

    # Per-message microphone upload deadline (seconds).
    mic_io_timeout = 60.0
    # Final ASR result deadline from final-message submission; excludes LLM work.
    # Increase for slow hardware or concurrent recognition workloads.
    mic_result_timeout = 600.0

    # 快捷键配置列表
    shortcuts = [
        {
            'key': 'ctrl_r',     # 监听右 Ctrl 键
            'type': 'keyboard',     # 是键盘快捷键
            'suppress': False,      # 不阻塞原按键
            'hold_mode': False,      # 切换模式：单击开始，再次单击结束
            'enabled': True         # 启用此快捷键
        },
        {
            'key': 'x2',
            'type': 'mouse',
            'suppress': False,
            'hold_mode': False,
            'enabled': True
        },
    ]

    threshold    = 0.3          # 快捷键触发阈值（秒）

    paste        = False        # 是否以写入剪切板然后模拟 Ctrl-V 粘贴的方式输出结果
    restore_clip = True         # 模拟粘贴后是否恢复剪贴板
    paste_apps   = ['WeiXin.exe', 'Telegram.exe']  # 匹配时强制粘贴

    enter_apps   = [('happ.exe', 0.5), ('hexin.exe', 0.5)]  # (应用名, 延迟秒数) 输出完成后自动回车，如同花顺，输入股票名后，需要回车才能切换

    save_audio = False           # 是否保存录音文件
    # Empty: per-user LOCALAPPDATA/CapsWriter-Offline/audio on Windows.
    # Portable: 'audio-data'; relative paths are based on the application folder.
    # Absolute paths may use another drive. Existing recordings are never moved.
    audio_dir = ''
    audio_name_len = 20         # 将录音识别结果的前多少个字存储到录音文件名中，建议不要超过200
    
    language = 'auto'           # 识别语言：'auto', 'chinese', 'english', 'japanese' 等（各引擎支持范围不同）

    trash_punc = '，。,.'       # 识别结果要消除的末尾标点
    trash_punc_thresh = 8       # 识别结果的单词数量低于阈值时，强制去除末尾标点
    trash_punc_apps = ['WeiXin.exe', ]   # 对于指定的应用，强制去除末尾标点

    traditional_convert = False     # 是否将识别结果转换为繁体中文
    traditional_locale = 'zh-hant'  # 繁体地区：'zh-hant'（标准繁体）, 'zh-tw'（台湾繁体）, 'zh-hk'（香港繁体）


    llm_enabled = False          # LLM 总开关；托盘 LLM actions 可全部开启/关闭并保存
    llm_correction_enabled = True   # 润色/纠错独立开关，受总开关控制
    llm_translation_enabled = True  # 翻译独立开关，默认由“翻译…”口令触发

    # 独立保存听写文字；不要求同时保存麦克风音频。
    save_transcripts = True
    transcript_dir = 'logs/transcripts'
    transcript_save_original = False
    save_llm_records = False

    # 自动读取本次听写起点附近的文本；关闭时不访问文本控件。
    caret_context_enabled = False
    caret_context_before_chars = 800
    caret_context_after_chars = 200

    # 最多一个自动预设；None 表示仅显式口令触发，受 llm_enabled 总开关控制。
    # 托盘分别控制两种能力；关闭对应能力后不再自动或显式调用它。
    llm_default_preset = 'correct_asr'
    llm_config_dir = 'LLM'
    # 诊断日志按年/月保留，0 表示不自动清理；不控制识别文字归档。
    save_diagnostic_logs = True
    diagnostic_log_retention_days = 30

    llm_stop_key = 'esc'        # 中断 LLM 输出的快捷键

    enable_tray = True          # 客户端默认启用托盘图标功能

    # 日志配置
    log_level = 'DEBUG'          # 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'

    mic_seg_duration = 60       # 麦克风听写时分段长度：60秒
    mic_seg_overlap = 4         # 麦克风听写时分段重叠：4秒
    # 输入设备：None/'' 跟随系统默认；也可填写数字索引或唯一名称（Windows 重名时加 Host API）
    # 示例：'Microphone (Realtek(R) Audio), Windows WASAPI'
    input_device = None
    enable_idle_suspend = True  # 是否启用闲置自动挂起（释放麦克风，避免耳机长期通话模式）
    idle_suspend_seconds = 20   # 空闲超过多少秒后自动挂起

    # 文件转录默认值：CLI 未提供对应覆盖参数时使用，命令行不会写回这些配置。
    file_seg_duration = 60      # 转录文件时分段长度
    file_seg_overlap = 4        # 转录文件时分段重叠
    file_scan_recursive = True  # 未指定 --recursive/--no-recursive 时是否递归扫描
    # 每次文件转写运行生成独立日志，保存到 logs/transcribe/年份/月份/。
    file_separate_log = True
    file_media_extensions = (   # 文件夹扫描时纳入批量转写的媒体格式
        '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma',
        '.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v', '.ts',
    )

    # 未指定 --format 时使用以下四项；指定后由命令行完整覆盖本次输出集合。
    file_save_srt = True        # 转录文件时是否保存 srt 字幕
    file_save_txt = True        # 转录文件时是否保存 txt 文本（按标点切分后的）
    file_save_json = True       # 转录文件时是否保存 json 结果（含原始时间戳）
    file_save_merge = False     # 转录文件时是否保存 merge.txt（未切分的段落长文本）

    udp_broadcast = False               # 是否启用 UDP 广播输出结果
    udp_broadcast_targets = [           # UDP 广播目标地址列表，格式: (地址, 端口)
        ('127.255.255.255', 6017),      # 本地回环广播
        # ('192.168.1.255', 6017),      # 局域网广播（示例，按需启用）
    ]

    udp_control = False             # 是否启用 UDP 控制录音（外部程序发送 START/STOP 命令）
    udp_control_addr = '127.0.0.1'  # UDP 控制监听地址（'0.0.0.0' 允许外部访问）
    udp_control_port = 6018         # UDP 控制监听端口


# 快捷键配置说明
r"""
快捷键配置字段说明：
  key        - 按键名称（见下方可用按键列表）
  type       - 输入类型：'keyboard'（键盘）或 'mouse'（鼠标）
  suppress   - 是否阻塞按键（True=阻塞，False=不阻塞）
  hold_mode  - 长按模式（True=按下录音松开停止，False=单击开始再次单击停止）
  enabled    - 是否启用此快捷键

阻塞模式说明：
  - 阻塞模式  ：长按录音识别，短按（<0.3秒）则自动补发按键，不影响单击功能
  - 非阻塞模式：对于 CapsLock/NumLock/ScrollLock 这类切换键，松开时会自动补发，以恢复按键状态

可用按键名称：

  字母数字：a - z, 0 - 9（大键盘）

  符号键：, . / \ ` ' - = [ ] ; '


  功能键：f1 - f24

  控制键:
      ctrl_l,   ctrl_r,
      shift,  shift_r,
      alt_l,    alt_gr,
      cmd,    cmd_r

  特殊键：
      space, enter, tab, backspace, delete, insert, home, end
      page_up, page_down, esc, caps_lock, num_lock, scroll_lock
      print_screen, pause, menu

  方向键：up, down, left, right

  鼠标键：x1, x2

示例配置：
  {'key': 'caps_lock', 'type': 'keyboard', 'suppress': False, 'hold_mode': True, 'enabled': True}, 
  {'key': 'f12', 'type': 'keyboard', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
  {'key': 'x2', 'type': 'mouse', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
"""
