# coding: utf-8
"""
托盘图标模块

提供最小化到系统托盘的功能。
仅在 Windows 平台有效。

功能：
- 禁用控制台窗口的关闭按钮（防止误关）
- 最小化时自动隐藏到托盘
- 双击托盘图标显示/隐藏窗口
- 托盘菜单退出程序

注意：pystray 在 Linux 无 GUI 环境下无法导入，因此采用延迟导入。
"""

import os
import sys
import time
import threading
import platform
import subprocess
from typing import Optional
from . import logger, set_ui_logger

# 退出回调函数（由主程序传入）
_exit_callback = None

# 是否可用（在 enable_min_to_tray 时检测）
_tray_available = None

def _set_exit_callback(callback):
    """设置退出回调函数"""
    global _exit_callback
    _exit_callback = callback

def _get_exit_callback():
    return _exit_callback


def _check_tray_available() -> bool:
    """
    检查托盘功能是否可用
    
    Returns:
        bool: 是否可用
    """
    global _tray_available
    
    if _tray_available is not None:
        return _tray_available
    
    # 非 Windows 系统不支持
    if platform.system() != 'Windows':
        _tray_available = False
        return False
    
    # 尝试导入 pystray
    try:
        import pystray
        from PIL import Image
        _tray_available = True
    except ImportError as e:
        logger.warning(f"托盘功能不可用: {e}")
        _tray_available = False
    except Exception as e:
        logger.warning(f"托盘功能检测失败: {e}")
        _tray_available = False
    
    return _tray_available


# Windows API（延迟初始化）
_win_api_initialized = False
user32 = None
kernel32 = None

SW_HIDE = 0
SW_RESTORE = 9
SW_SHOW = 5
SC_CLOSE = 0xF060
MF_BYCOMMAND = 0x00000000
GA_ROOT = 3


def _init_win_api():
    """初始化 Windows API"""
    global _win_api_initialized, user32, kernel32
    
    if _win_api_initialized:
        return
    
    if platform.system() != 'Windows':
        return
    
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        _win_api_initialized = True
    except Exception as e:
        logger.warning(f"Windows API 初始化失败: {e}")


# 全局变量
_tray_instance: Optional['_TraySystem'] = None
_lock = threading.Lock()
_tray_recording = False
_tray_paused = False


def _get_console_hwnd():
    _init_win_api()
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        # 在 Windows Terminal 中，GetConsoleWindow 返回的是内部窗口句柄。
        # 为了能让“退出按钮不可用”以及“从任务栏消失”生效，我们需要操作最外层的顶层窗口。
        # 对于普通 CMD，GetAncestor(hwnd, GA_ROOT) 依然返回 hwnd 本身。
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT)
        if root_hwnd:
            return root_hwnd
    return hwnd


def _disable_close_button(hwnd: int) -> None:
    """禁用窗口的关闭按钮"""
    if user32 is None:
        return
    h_menu = user32.GetSystemMenu(hwnd, False)
    if h_menu:
        user32.DeleteMenu(h_menu, SC_CLOSE, MF_BYCOMMAND)


def _enable_close_button(hwnd: int) -> None:
    """恢复窗口的关闭按钮"""
    if user32 is None:
        return
    user32.GetSystemMenu(hwnd, True)


def _is_window_minimized(hwnd: int) -> bool:
    """检查窗口是否最小化"""
    if user32 is None:
        return False
    return user32.IsIconic(hwnd) != 0


def _is_window_visible(hwnd: int) -> bool:
    """检查窗口是否可见"""
    if user32 is None:
        return False
    return user32.IsWindowVisible(hwnd) != 0


def _create_icon(icon_path: Optional[str] = None, recording: bool = False):
    """
    创建托盘图标
    
    优先从指定路径加载图标文件，如果不存在则动态生成。
    
    Args:
        icon_path: 图标文件路径
        
    Returns:
        PIL Image 对象
    """
    from PIL import Image, ImageDraw
    
    # 仅正常状态加载图标文件；录音状态始终动态生成（确保红点显示）
    if not recording and icon_path and os.path.exists(icon_path):
        try:
            image = Image.open(icon_path)
            if image.mode != 'RGBA':
                image = image.convert('RGBA')
            return image.resize((64, 64), Image.Resampling.LANCZOS)
        except Exception:
            pass  # 加载失败则使用动态生成

    # 动态生成麦克风图标
    size = 64
    scale = 4
    S = size * scale  # 256px 工作分辨率（抗锯齿）

    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    dc = ImageDraw.Draw(img)

    BG  = (28, 28, 30)      # 深色背景 #1C1C1E
    MIC = (255, 255, 255)   # 白色麦克风
    RED = (255, 59, 48)     # 录音状态红点 #FF3B30

    # ── 圆角背景 ────────────────────────────────────────────
    m = 10
    dc.rounded_rectangle([m, m, S - m, S - m], radius=S // 5, fill=BG)

    cx = S // 2  # 水平中心 128

    # ── 麦克风胶囊体：细长药丸形，宽高比 ≈ 1:2.2 ────────────
    bw, bh = 46, 100
    btop    = 20
    bbottom = btop + bh           # 120
    dc.rounded_rectangle(
        [cx - bw // 2, btop, cx + bw // 2, bbottom],
        radius=bw // 2,           # 完全圆角 = 药丸形
        fill=MIC,
    )

    # ── 颈部（极细，避免"肩膀"感）────────────────────────────
    nw = 8
    dc.rectangle([cx - nw // 2, bbottom - 2, cx + nw // 2, 152], fill=MIC)

    # ── U 形支架：宽度明显窄于胶囊，视觉上是"架子"而非肩膀 ──
    aw = 36                       # < bw=46，支架比胶囊更窄
    dc.arc(
        [cx - aw, 132, cx + aw, 168],
        start=180, end=360,
        fill=MIC, width=9,
    )

    # ── 底部横杆 ────────────────────────────────────────────
    dc.rectangle([cx - aw, 161, cx + aw, 171], fill=MIC)

    # ── 录音状态红点（右下角）────────────────────────────────
    if recording:
        dr  = 24
        dcx = S - m - dr - 4
        dcy = S - m - dr - 4
        dc.ellipse([dcx - dr - 4, dcy - dr - 4, dcx + dr + 4, dcy + dr + 4], fill=BG)
        dc.ellipse([dcx - dr,     dcy - dr,     dcx + dr,     dcy + dr    ], fill=RED)

    return img.resize((size, size), Image.Resampling.LANCZOS)


class _TraySystem:
    """托盘系统内部类"""
    
    def __init__(self, name: Optional[str] = None, icon_path: Optional[str] = None, more_options: list = None):
        # 延迟导入 pystray
        import pystray
        from pystray import MenuItem as item
        
        self.hwnd = _get_console_hwnd()
        self.should_exit = False
        self.title = name if name else (os.path.basename(sys.argv[0]) or "Console App")
        self._icon_path = icon_path

        # 禁用关闭按钮
        if self.hwnd:
            _disable_close_button(self.hwnd)

        # 定义菜单
        menu_items = [
            item(f"{self.title}", lambda: None, enabled=False),
            item('👁️ 显示/隐藏', self.toggle_window, default=True),
        ]

        # 添加额外选项
        if more_options:
            for opt_name, opt_func in more_options:
                menu_items.append(item(opt_name, opt_func))

        menu_items.append(item('🔄 重启', self.on_restart))
        menu_items.append(item('❌ 退出', self.on_exit))

        self.icon = pystray.Icon(
            "console_tray",
            _create_icon(icon_path),
            title=f"{self.title}",
            menu=tuple(menu_items)
        )

    def toggle_window(self) -> None:
        """切换窗口显示状态"""
        if not self.hwnd or user32 is None:
            return

        if _is_window_visible(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_HIDE)
        else:
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            user32.SetForegroundWindow(self.hwnd)

    def monitor_loop(self) -> None:
        """监控线程：检测最小化操作"""
        while not self.should_exit:
            if self.hwnd and user32:
                # 窗口可见且最小化 -> 隐藏到托盘
                if _is_window_visible(self.hwnd) and _is_window_minimized(self.hwnd):
                    user32.ShowWindow(self.hwnd, SW_HIDE)
            time.sleep(0.2)

    def on_restart(self, icon, item) -> None:
        """托盘重启处理：启动新进程后退出当前进程"""
        logger.info("托盘重启: 用户点击重启菜单，准备重启程序")
        try:
            if getattr(sys, 'frozen', False):
                cmd = sys.argv
            else:
                cmd = [sys.executable] + sys.argv
            subprocess.Popen(cmd)
        except Exception as e:
            logger.error(f"重启失败: {e}")
            return

        # 启动新进程成功后，调用退出回调退出当前进程
        exit_callback = _get_exit_callback()
        if exit_callback:
            try:
                exit_callback()
            except Exception as e:
                logger.error(f"重启时调用退出回调发生错误: {e}")

    def on_exit(self, icon, item) -> None:
        """托盘退出处理"""
        exit_callback = _get_exit_callback()

        logger.info("托盘退出: 用户点击退出菜单，准备清理资源并退出")

        # 1. 设置退出标志，停止监控循环
        self.should_exit = True
        logger.debug("已设置托盘退出标志")

        # 2. 恢复窗口关闭按钮并显示窗口
        if self.hwnd and user32:
            _enable_close_button(self.hwnd)
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            logger.debug("已恢复窗口显示")

        # 3. 调用退出回调函数，请求主程序退出
        if exit_callback:
            try:
                logger.debug("正在调用退出回调函数...")
                exit_callback()
                logger.info("退出回调函数已调用")
            except Exception as e:
                logger.error(f"调用退出回调函数时发生错误: {e}")



        # 5. 停止托盘图标
        try:
            logger.debug("正在停止托盘图标线程...")
            self.icon.stop()
            logger.debug("托盘图标线程已停止")
        except Exception as e:
            logger.warning(f"停止托盘图标时发生错误: {e}")

    def start(self) -> None:
        """启动托盘系统"""
        # 托盘图标线程
        t_tray = threading.Thread(target=self.icon.run, daemon=False)
        t_tray.start()

        # 状态监控线程
        t_monitor = threading.Thread(target=self.monitor_loop, daemon=True)
        t_monitor.start()

        # 启动时隐藏窗口
        self.toggle_window()


def enable_min_to_tray(name: Optional[str] = None, icon_path: Optional[str] = None, exit_callback=None, more_options: list = None) -> None:
    """
    启用最小化到托盘功能

    如果检测不到控制台窗口（如 .pyw 运行），则不执行任何操作。
    如果在 Linux 等无 GUI 环境下运行，也会跳过。

    Args:
        name: 托盘图标显示的名称，默认使用程序名称
        icon_path: 图标文件路径，默认动态生成
        exit_callback: 退出回调函数，当用户点击托盘退出菜单时调用
        more_options: 额外菜单项列表，格式为 [(名称, 回调函数), ...]
    """
    global _tray_instance

    global _tray_instance

    # 设置退出回调函数
    if exit_callback is not None:
        _set_exit_callback(exit_callback)

    # 检查托盘功能是否可用
    if not _check_tray_available():
        logger.info("托盘功能不可用，跳过启用")
        return

    # DPI 感知设置
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    with _lock:
        if _tray_instance is not None:
            return  # 已启动

        if not _get_console_hwnd():
            return  # 没有控制台窗口

        _tray_instance = _TraySystem(name, icon_path, more_options)
        _tray_instance.start()



def stop_tray() -> None:
    """停止托盘图标"""
    global _tray_instance, _tray_recording, _tray_paused
    if _tray_instance and _tray_instance.icon:
        try:
            _tray_instance.icon.stop()
        except Exception:
            pass
    _tray_instance = None
    _tray_recording = False
    _tray_paused = False


def set_recording_state(recording: bool) -> None:
    """更新托盘图标录音状态（线程安全）"""
    global _tray_recording
    _tray_recording = recording
    _refresh_tray_status()


def set_dictation_paused(paused: bool) -> None:
    """更新托盘图标听写暂停状态（线程安全）"""
    global _tray_paused
    _tray_paused = paused
    _refresh_tray_status()


def _refresh_tray_status() -> None:
    """根据录音/暂停状态刷新托盘图标与标题"""
    global _tray_instance
    if _tray_instance is None:
        return
    try:
        _tray_instance.icon.icon = _create_icon(
            icon_path=_tray_instance._icon_path,
            recording=_tray_recording,
        )
        if _tray_recording:
            suffix = ' · 录音中'
        elif _tray_paused:
            suffix = ' · 已暂停'
        else:
            suffix = ''
        _tray_instance.icon.title = f"{_tray_instance.title}{suffix}"
    except Exception as e:
        logger.warning(f'更新托盘图标状态失败: {e}')


if __name__ == "__main__":
    enable_min_to_tray()
    print("程序运行中... 你可以双击托盘图标隐藏我。")
    while True:
        time.sleep(1)
