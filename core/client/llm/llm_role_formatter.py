"""
LLM 角色信息格式化器

功能：
1. 格式化角色状态显示
2. 提供统一的 Rich 渲染支持
"""
import unicodedata
from rich.text import Text
from core.client.state import console
from .llm_role_config import RoleConfig


class RoleFormatter:
    """角色信息格式化器 - 负责角色显示的格式化"""

    @staticmethod
    def _get_display_width(text: str) -> int:
        """计算字符串的显示宽度（考虑中文字符占2个单位）"""
        width = 0
        for char in text:
            if unicodedata.east_asian_width(char) in ('W', 'F', 'A'):
                width += 2
            else:
                width += 1
        return width

    @staticmethod
    def format_status(role_name: str, role_config: RoleConfig) -> Text:
        """
        格式化角色状态显示

        Args:
            role_name: 角色名称
            role_config: 角色配置（RoleConfig 对象）

        Returns:
            Text 对象（用于 Rich 渲染）
        """
        text = Text()

        # 角色名称：统一对齐到至少 8 个半角字符宽度
        display_width = RoleFormatter._get_display_width(role_name)
        padding = " " * max(0, 8 - display_width)
        text.append(f"{role_name}{padding}  ", style="ui.accent")

        # 启用状态
        enabled = role_config.enabled
        text.append("启用 ", style="ui.success" if enabled else "ui.muted")

        # 输出方式
        output_mode = role_config.output_mode
        if output_mode == 'typing':
            text.append("打字 ", style="ui.success")
        elif output_mode == 'toast':
            text.append("弹窗 ", style="ui.secondary")
        else:
            text.append("打字 ", style="ui.muted")

        # 思考
        thinking = role_config.enable_thinking
        text.append("思考 ", style="ui.success" if thinking else "ui.muted")

        # 记忆
        history = role_config.enable_history
        text.append("记忆 ", style="ui.success" if history else "ui.muted")

        # 热词
        hotwords = role_config.enable_hotwords
        text.append("热词 ", style="ui.success" if hotwords else "ui.muted")

        # 读取选中文字
        read_selection = role_config.enable_read_selection
        text.append("读选区 ", style="ui.success" if read_selection else "ui.muted")
        

        # 模型信息
        text.append(f"  {role_config.model} · {role_config.provider}", style="ui.muted")

        return text

    @staticmethod
    def print_status(role_name: str, role_config: RoleConfig, prefix: str = "  "):
        """
        打印角色状态（带前缀）

        Args:
            role_name: 角色名称
            role_config: 角色配置
            prefix: 前缀文本（默认两个空格）
        """
        status_line = RoleFormatter.format_status(role_name, role_config)

        text = Text(prefix)
        text.append(status_line)
        console.print(text)

    @staticmethod
    def print_update(role_name: str, role_config: RoleConfig):
        """
        打印角色更新信息

        Args:
            role_name: 角色名称
            role_config: 角色配置
        """
        status_line = RoleFormatter.format_status(role_name, role_config)

        # 构建 "角色更新  " 前缀 + 状态行
        prefix = Text("\n角色更新  ")
        prefix.append(status_line)
        console.print(prefix + "\n")
