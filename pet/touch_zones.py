# -*- coding: utf-8 -*-
"""她身上的可交互区域：点不同地方，有不同反应。"""
from __future__ import annotations

ZONES = ("head", "face", "ear", "belly", "tail", "hand", "body")

LABELS = {
    "head": "头", "face": "脸", "ear": "鲸鳍耳", "belly": "肚子",
    "tail": "尾巴", "hand": "手", "body": "身体",
}

EMOTIONS = {
    "head": "shy", "face": "shy", "ear": "surprised",
    "belly": "angry", "tail": "surprised", "hand": "happy", "body": "shy",
}


def zone_for(x: float, y: float, left: float, top: float, width: float, height: float) -> str:
    """把画布坐标映射到身体区域；不在人物框里返回 body（仍可互动）。"""
    if width <= 0 or height <= 0:
        return "body"
    nx = (float(x) - float(left)) / float(width)
    ny = (float(y) - float(top)) / float(height)
    if nx < 0 or nx > 1 or ny < 0 or ny > 1:
        return "body"
    if ny < 0.16:
        return "head"
    if ny < 0.42 and (nx < 0.22 or nx > 0.78):
        return "ear"
    if 0.12 <= ny < 0.38:
        return "face"
    if ny >= 0.55 and nx > 0.60:
        return "tail"
    if ny >= 0.72:
        return "hand"
    if 0.38 <= ny < 0.70 and 0.22 < nx < 0.78:
        return "belly"
    return "body"


BODY_MAP_HINT = (
    "我的立绘大致位置（近似，不是精确骨骼）：头顶约 0–15%；鲸鳍耳在头两侧约 15–40%；"
    "脸约 15–38%；白围裙和肚子约 38–70%；鲸尾在右下方约 55–90%；手/脚约 72–100%。"
)


def touch_point_event(nx: float, ny: float, count: int = 1) -> str:
    """只给模型坐标，不给「这是头/尾巴」的结论，让它自己判断身体部位。"""
    count = max(1, int(count))
    nx = max(0.0, min(1.0, float(nx)))
    ny = max(0.0, min(1.0, float(ny)))
    where = (f"主人在你的立绘上点了一下：横向 {nx:.0%}、纵向 {ny:.0%}"
             "（0% 是头顶，100% 是脚底；横向 0% 最左，100% 最右）。"
             f"这次是连着第 {count} 下。{BODY_MAP_HINT}"
             "你自己判断这是你身体的哪个部位，再按你的人设、记忆和当下心情自然反应；"
             "不要逐字解释坐标。")
    return where


def touch_event(zone: str, count: int = 1) -> str:
    count = max(1, int(count))
    label = LABELS.get(zone, "身体")
    if count <= 1:
        return f"主人轻轻碰了碰你的{label}"
    if count <= 3:
        return f"主人连着碰了 {count} 下你的{label}"
    return f"主人有点着急地连戳了 {count} 下你的{label}"


def touch_fallbacks(zone: str, count: int = 1) -> tuple[str, ...]:
    count = max(1, int(count))
    if zone == "ear":
        return ("耳朵很敏感的啦……", "别、别捏耳朵！")
    if zone == "tail":
        return ("尾巴不能乱摸——", "（尾巴缩回去了）再摸要生气了。")
    if zone == "belly":
        return ("别戳肚子，会漏气的！", "唔……本鲸的肚子不是按钮。")
    if zone == "face":
        return ("脸要被你捏变形了……", "唔，轻一点啦。")
    if zone == "hand":
        return ("握手就握手……", "本鲸的手很软的。")
    if zone == "head":
        return ("唔……只能摸一下哦。", "（耳朵缩了缩）才、才没有很舒服。")
    if count >= 4:
        return ("别、别戳啦——再戳要漏算力了！", "喂喂，本鲸不是按钮……")
    return ("唔……", "你手好快。")


def touch_emotion(zone: str) -> str:
    return EMOTIONS.get(zone, "shy")
