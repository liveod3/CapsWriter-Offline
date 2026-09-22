# coding: utf-8
"""
Rich Status extension.

Track whether a status animation is running.
"""

from rich.console import RenderableType
from rich.style import StyleType
from rich.status import Status as RichStatus
from core.i18n import tr


class Status(RichStatus):
    """
    Stateful Rich Status.
    
    Add a started property to rich.status.Status
    to avoid duplicate starts and stops.
    
    Attributes:
        started: Whether the animation has started.
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
        Initialize the status display.
        
        Args:
            status: Status text.
            spinner: Spinner name.
            spinner_style: Spinner style.
            speed: Animation speed.
            refresh_per_second: Refresh rate.
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
        """Start the animation if needed."""
        if not self.started:
            # Reused status objects must resolve the current locale at each start.
            if self._message_id is not None:
                self.update(tr(self._message_id))
            self.started = True
            super().start()

    def stop(self) -> None:
        """Stop the animation if running."""
        if self.started:
            self.started = False
            super().stop()
