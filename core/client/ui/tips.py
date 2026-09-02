# coding: utf-8
"""
提示信息显示模块

提供 TipsDisplay 类用于显示启动提示信息。
"""

from __future__ import annotations

import os

from rich.panel import Panel
from rich.table import Table

from core.client.state import console
from config_client import ClientConfig as Config, __version__
from . import logger



def _format_shortcut_name(key: str) -> str:
    """
    格式化快捷键名称用于显示

    Args:
        key: 快捷键名称（如 'caps_lock', 'f12'）

    Returns:
        str: 格式化后的名称（如 'CapsLock', 'F12'）
    """
    # 将下划线替换为空格，然后标题化
    return key.replace('_', ' ').title()


def _get_shortcuts_display() -> str:
    """
    获取所有启用快捷键的显示字符串

    Returns:
        str: 格式化的快捷键列表，用逗号分隔
    """
    enabled_shortcuts = [sc for sc in Config.shortcuts if sc.get('enabled', True)]
    if not enabled_shortcuts:
        return '未配置快捷键'

    # 格式化每个快捷键名称
    formatted = [_format_shortcut_name(sc['key']) for sc in enabled_shortcuts]
    return '、'.join(formatted)


class TipsDisplay:
    """
    提示信息显示器
    
    显示客户端启动时的提示信息。
    """
    
    @staticmethod
    def show_mic_tips() -> None:
        """显示麦克风模式的启动提示"""
        shortcuts_display = _get_shortcuts_display()

        details = Table.grid(padding=(0, 2))
        details.add_column(style='ui.label', no_wrap=True)
        details.add_column(style='ui.value')
        details.add_row('快捷键', shortcuts_display)
        details.add_row('识别服务', f'{Config.addr}:{Config.port}')
        details.add_row('工作目录', os.getcwd())
        details.add_row('状态', '等待语音输入')
        console.print()
        console.print(Panel(
            details,
            title=f'[ui.title]CapsWriter Offline[/]  [ui.accent]语音输入[/]  '
                  f'[ui.secondary]v{__version__}[/]',
            title_align='left',
            border_style='ui.border',
            padding=(0, 2),
        ))
        console.print('[ui.muted]按下快捷键开始录音；再次按下或松开以结束。[/]')

        logger.debug("已显示麦克风模式启动提示")
    
    @staticmethod
    def show_file_tips(total: int, formats: str) -> None:
        """显示文件转录模式的启动提示"""
        details = Table.grid(padding=(0, 2))
        details.add_column(style='ui.label', no_wrap=True)
        details.add_column(style='ui.value')
        details.add_row('识别服务', f'{Config.addr}:{Config.port}')
        details.add_row('工作目录', os.getcwd())
        details.add_row('任务', f'{total} 个文件 · {formats}')
        console.print()
        console.print(Panel(
            details,
            title=f'[ui.title]CapsWriter Offline[/]  [ui.accent]文件转写[/]  '
                  f'[ui.secondary]v{__version__}[/]',
            title_align='left',
            border_style='ui.border',
            padding=(0, 2),
        ))
        
        logger.debug("已显示文件转录模式启动提示")
