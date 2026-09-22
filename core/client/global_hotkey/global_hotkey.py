# coding: utf-8
"""
全局快捷键管理器

使用 pynput GlobalHotKeys 实现全局快捷键监听，替代 keyboard 库。

使用示例:
    from core.client.global_hotkey import GlobalHotkeyManager

    manager = GlobalHotkeyManager()
    manager.register('<esc>', lambda: print('ESC pressed'))
    manager.start()
"""
from __future__ import annotations

from core.i18n import Notice

import threading
from typing import Callable, Dict, Optional

from pynput import keyboard

from . import logger



class GlobalHotkeyManager:
    """
    全局快捷键管理器

    使用 pynput GlobalHotKeys 实现，支持动态注册/注销快捷键。
    
    对比 keyboard 库的优势：
    - 与 pynput 的其他功能兼容
    - 不需要额外的依赖
    - 更好的跨平台支持
    """

    # 单例实例
    _instance: Optional[GlobalHotkeyManager] = None
    _lock = threading.Lock()

    def __new__(cls) -> GlobalHotkeyManager:
        """单例模式"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self._hotkeys: Dict[str, Callable] = {}
        self._listener: Optional[keyboard.GlobalHotKeys] = None
        self._running = False
        self._initialized = True
        logger.debug(Notice('diagnostic.global_hotkey.globalhotkeymanager_initialized'))

    def register(self, key_str: str, callback: Callable) -> None:
        """
        注册全局快捷键

        Args:
            key_str: 快捷键字符串，pynput 格式，如 '<esc>', '<ctrl>+<alt>+h'
            callback: 按下快捷键时的回调函数
        """
        self._hotkeys[key_str] = callback
        logger.debug(Notice('diagnostic.global_hotkey.global_hotkey_registered', value0=key_str))
        
        # 如果已经在运行，重启监听器以应用新的快捷键
        if self._running:
            self._restart_listener()

    def unregister(self, key_str: str) -> bool:
        """
        注销全局快捷键

        Args:
            key_str: 快捷键字符串

        Returns:
            是否成功注销
        """
        if key_str in self._hotkeys:
            del self._hotkeys[key_str]
            logger.debug(Notice('diagnostic.global_hotkey.global_hotkey_unregistered', value0=key_str))
            
            if self._running:
                self._restart_listener()
            return True
        return False

    def start(self) -> None:
        """启动快捷键监听"""
        if self._running:
            logger.debug(Notice('diagnostic.global_hotkey.globalhotkeymanager_already_running'))
            return
        
        if not self._hotkeys:
            logger.warning(Notice('diagnostic.global_hotkey.no_hotkeys_registered_startup_skipped'))
            return
        
        self._running = True
        self._start_listener()
        logger.info(Notice('diagnostic.global_hotkey.globalhotkeymanager_started_with_hotkeys', value0=len(self._hotkeys)))

    def stop(self) -> None:
        """停止快捷键监听"""
        self._running = False
        if self._listener:
            try:
                self._listener.stop()
            except Exception as e:
                logger.warning(Notice('diagnostic.global_hotkey.failed_to_stop_globalhotkeys_listener', value0=e))
            self._listener = None
        logger.info(Notice('diagnostic.global_hotkey.globalhotkeymanager_stopped'))

    def _start_listener(self) -> None:
        """启动监听器"""
        if not self._hotkeys:
            return
        
        try:
            self._listener = keyboard.GlobalHotKeys(self._hotkeys)
            self._listener.start()
            logger.debug(Notice('diagnostic.global_hotkey.globalhotkeys_listener_started', value0=list(self._hotkeys.keys())))
        except Exception as e:
            logger.error(Notice('diagnostic.global_hotkey.failed_to_start_globalhotkeys_listener', value0=e))
            self._listener = None

    def _restart_listener(self) -> None:
        """重启监听器（用于更新快捷键后）"""
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        
        if self._running and self._hotkeys:
            self._start_listener()


# 全局单例实例
_global_hotkey_manager: Optional[GlobalHotkeyManager] = None


def get_global_hotkey_manager() -> GlobalHotkeyManager:
    """获取全局快捷键管理器单例"""
    global _global_hotkey_manager
    if _global_hotkey_manager is None:
        _global_hotkey_manager = GlobalHotkeyManager()
    return _global_hotkey_manager
