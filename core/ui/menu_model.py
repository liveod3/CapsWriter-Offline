"""菜单动作声明，将文案、说明、图标与业务回调分离。"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class MenuAction:
    label: str | Callable
    callback: Callable | None = None
    tooltip: str | Callable = ""
    icon: str | Callable = ""
    enabled: bool | Callable = True
    children: list = field(default_factory=list)
    default: bool = False
    checked: Callable | None = None
    radio: bool = False

    def to_item(self):
        import pystray

        action = (
            pystray.Menu(*(child.to_item() for child in self.children))
            if self.children
            else self.callback
        )
        item = pystray.MenuItem(
            self.label, action or (lambda: None), enabled=self.enabled, default=self.default,
            checked=self.checked, radio=self.radio,
        )
        item.caps_tooltip = self.tooltip
        item.caps_icon = self.icon
        return item
