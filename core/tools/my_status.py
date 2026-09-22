# coding: utf-8
"""
Rich Status 扩展模块

提供增强版的 Status 类，支持状态追踪。
"""

from rich.console import RenderableType
from rich.style import StyleType
from rich.status import Status as RichStatus
from core.i18n import tr


class Status(RichStatus):
    """
    增强版 Rich Status
    
    扩展 rich.status.Status，添加 started 属性用于追踪状态。
    防止重复启动和停止动画。
    
    Attributes:
        started: 是否已启动动画
    """
    
    def __init__(
        self,
        status: RenderableType = "",
        *,
        message_id: str | None = None,
        spinner: str = "dots",
        spinner_style: StyleType = "status.spinner",
        speed: float = 1.0,
        refresh_per_second: float = 12.5
    ):
        """
        初始化 Status
        
        Args:
            status: 要显示的状态文本
            spinner: 动画类型名称
            spinner_style: 动画样式
            speed: 动画速度
            refresh_per_second: 刷新频率
        """
        super().__init__(
            status,
            console=None,
            spinner=spinner,
            spinner_style=spinner_style,
            speed=speed,
            refresh_per_second=refresh_per_second,
        )
        self.started = False
        self._message_id = message_id

    def start(self) -> None:
        """启动动画（如果尚未启动）"""
        if not self.started:
            # Reused status objects must resolve the current locale at each start.
            if self._message_id is not None:
                self.update(tr(self._message_id))
            self.started = True
            super().start()

    def stop(self) -> None:
        """停止动画（如果已启动）"""
        if self.started:
            self.started = False
            super().stop()
