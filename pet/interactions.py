"""Pure helpers and persona lines for desktop interactions."""
from __future__ import annotations

FEED_LINES = {
    "rice": ("白饭！第 {count} 碗！我开动啦。", "咕噜…今天的饭特别香。"),
    "fish": ("小鱼干！我的最爱！", "咔嚓咔嚓……谢谢投喂！", "唔，鲜！"),
    "cake": ("蛋糕！罪恶但快乐……", "甜到冒泡泡～", "嗝～又圆了一圈……"),
    "tangyuan": ("三色团子！软乎乎～", "糯叽叽，爱了爱了！"),
    "diamond": ("钻石？！这能吃吗……咕咚。真香！", "发财啦！明天开始吃高级鱼粮！"),
}

DRAG_LINES = ("起飞咯——", "晕鱼了晕鱼了……")
INNER_VOICE_LINES = (
    "这用户发的啥啊，",
    "这也太虐了吧？！我心里堵得慌！！",
    "呜呜我再也不敢了 QAQ",
    "用户要的沉浸式……脑内小剧场进行中",
)


def step_flight(x: float, y: float, vx: float, vy: float, dt: float,
                left: float, top: float, right: float, floor: float,
                gravity: float = 1800.0, friction: float = 0.985,
                bounce: float = 0.58, ground_friction: float = 0.88) -> tuple[float, float, float, float, bool]:
    """Advance a desktop-window toss by dt seconds, in screen coordinates."""
    dt = max(0.0, min(float(dt), 0.05))
    vx *= friction ** (dt * 60.0)
    vy += gravity * dt
    x += vx * dt
    y += vy * dt
    if x < left:
        x, vx = left, abs(vx) * bounce
    elif x > right:
        x, vx = right, -abs(vx) * bounce
    if y < top:
        y, vy = top, abs(vy) * bounce
    on_floor = y >= floor
    if on_floor:
        y, vy = floor, -abs(vy) * bounce
        if abs(vy) < 90:
            vy = 0.0
            vx *= ground_friction ** (dt * 60.0)  # 贴地滑行，比空中减速快
    if abs(vx) < 8:
        vx = 0.0
    return x, y, vx, vy, on_floor and vy == 0.0 and vx == 0.0


# ---------- 投喂道具：手里拿出食物，一口一口咬掉 ----------
FOOD_EMOJI = {"rice": "🍚", "fish": "🐟", "cake": "🍰", "tangyuan": "🍡", "diamond": "💎"}
EMOJI_FONT = "C:/Windows/Fonts/seguiemj.ttf"
# 每一口咬在哪（相对尺寸的圆心 x、y、半径），从右上往左下吃
BITE_SPOTS = ((0.86, 0.22, 0.24), (0.62, 0.10, 0.22), (0.92, 0.55, 0.24), (0.40, 0.30, 0.26))


def render_food(food: str, size: int = 64, bites: int = 0):
    """彩色 emoji 画成透明 RGBA 图，再按 bites 挖掉几个圆口子（咬痕）。

    字体不可用时退回一个画出来的饭团，保证总有东西可拿。
    """
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    emoji = FOOD_EMOJI.get(food, FOOD_EMOJI["rice"])
    drawn = False
    try:
        font = ImageFont.truetype(EMOJI_FONT, 109)  # Segoe UI Emoji 的位图档位，缩放后最清楚
        glyph = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((0, 0), emoji, font=font, embedded_color=True)
        box = glyph.getbbox()
        if box:
            glyph = glyph.crop(box)
            glyph.thumbnail((size, size), Image.LANCZOS)
            canvas.paste(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2), glyph)
            drawn = True
    except Exception:
        drawn = False
    if not drawn:
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((size * 0.12, size * 0.18, size * 0.88, size * 0.9), fill=(250, 250, 245, 255),
                     outline=(120, 130, 150, 255), width=max(1, size // 32))
        draw.rectangle((size * 0.3, size * 0.62, size * 0.7, size * 0.9), fill=(40, 60, 70, 255))
    if bites > 0:
        mask = canvas.getchannel("A")
        cut = ImageDraw.Draw(mask)
        for cx, cy, r in BITE_SPOTS[: min(bites, len(BITE_SPOTS))]:
            cut.ellipse(((cx - r) * size, (cy - r) * size, (cx + r) * size, (cy + r) * size), fill=0)
        canvas.putalpha(mask)
    return canvas


def visible_ratio(image) -> float:
    """不透明像素占比：测试用来确认咬一口确实变少了。"""
    alpha = image.getchannel("A")
    return sum(1 for value in alpha.getdata() if value > 16) / float(image.width * image.height)
