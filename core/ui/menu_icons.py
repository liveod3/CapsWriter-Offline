"""托盘菜单的统一矢量式图标，按 DPI 绘制为原生菜单位图。"""

from PIL import Image, ImageDraw

ICON_NAMES = (
    "console",
    "pause",
    "resume",
    "copy",
    "text",
    "history",
    "settings",
    "tools",
    "microphone",
    "restart",
    "quit",
    "folder",
    "translate",
)


def menu_icon(name: str, size: int = 20, light: bool = False):
    scale = 4
    image = Image.new("RGBA", (24 * scale, 24 * scale))
    draw = ImageDraw.Draw(image)
    ink = "#e5f3fb" if light else "#123c50"

    def line(points, width=2):
        draw.line(
            [(x * scale, y * scale) for x, y in points],
            fill=ink,
            width=width * scale,
            joint="curve",
        )

    def box(rect, radius=2, fill=False):
        draw.rounded_rectangle(
            tuple(v * scale for v in rect),
            radius=radius * scale,
            fill=ink if fill else None,
            outline=ink,
            width=2 * scale,
        )

    if name == "console":
        box((3, 4, 21, 20))
        line([(6, 9), (9, 12), (6, 15)])
        line([(12, 15), (17, 15)])
    elif name == "pause":
        box((6, 4, 9, 20), 1, True)
        box((15, 4, 18, 20), 1, True)
    elif name == "resume":
        draw.polygon(
            [(7 * scale, 4 * scale), (20 * scale, 12 * scale), (7 * scale, 20 * scale)], fill=ink
        )
    elif name == "copy":
        box((8, 8, 21, 21))
        line([(16, 5), (16, 3), (3, 3), (3, 16), (5, 16)])
    elif name in {"text", "translate"}:
        box((3, 3, 21, 21))
        line([(7, 7), (17, 7)])
        line([(12, 7), (12, 17)])
        if name == "translate":
            line([(6, 17), (9, 14)])
            line([(15, 14), (18, 17)])
    elif name == "history":
        draw.arc(
            (4 * scale, 4 * scale, 21 * scale, 21 * scale), 210, 510, fill=ink, width=2 * scale
        )
        line([(4, 3), (4, 9), (10, 9)])
        line([(12, 7), (12, 13), (16, 15)])
    elif name in {"settings", "tools"}:
        import math

        points = []
        for i in range(24):
            radius = 9 if i % 3 != 1 else 7
            angle = i * math.pi / 12
            points.append((12 + radius * math.cos(angle), 12 + radius * math.sin(angle)))
        line(points + [points[0]])
        draw.ellipse((9 * scale, 9 * scale, 15 * scale, 15 * scale), outline=ink, width=2 * scale)
    elif name == "microphone":
        box((9, 3, 15, 15), 3)
        draw.arc((5 * scale, 7 * scale, 19 * scale, 19 * scale), 0, 180, fill=ink, width=2 * scale)
        line([(12, 19), (12, 22)])
        line([(8, 22), (16, 22)])
    elif name == "restart":
        draw.arc((4 * scale, 4 * scale, 20 * scale, 20 * scale), 30, 315, fill=ink, width=2 * scale)
        line([(14, 3), (20, 3), (20, 9)])
    elif name == "quit":
        line([(10, 3), (4, 3), (4, 21), (10, 21)])
        line([(9, 12), (21, 12)])
        line([(16, 7), (21, 12), (16, 17)])
    else:
        line([(3, 20), (3, 5), (10, 5), (12, 8), (21, 8), (21, 20), (3, 20)])
    return image.resize((size, size), Image.Resampling.LANCZOS)
