# -*- coding: utf-8 -*-
"""看视频：抽取少量关键帧拼成一张 storyboard，再交给原生视觉模型。

比逐帧多次调用便宜：一段视频通常只发 1 张合成图（6 帧），
提示里标好每帧时间；长视频只采样前 3 分钟，避免一次读太多。
"""
from __future__ import annotations

import time
from pathlib import Path


def sample_frames(video_path: Path, count: int = 6, max_width: int = 480) -> list[tuple[float, object]]:
    """返回 [(秒数, PIL.Image)]；依赖 imageio-ffmpeg（已随依赖安装）。"""
    try:
        import imageio_ffmpeg
        from PIL import Image
    except Exception:
        return []
    path = Path(video_path)
    if not path.is_file():
        return []
    frames: list[tuple[float, object]] = []
    try:
        reader = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24")
        meta = next(reader)
        size = meta.get("size") or (0, 0)
        fps = float(meta.get("fps") or 25.0)
        duration = float(meta.get("duration") or 0.0)
        total = max(1, int(duration * fps))
        count = max(2, min(8, int(count)))
        times = [min(max(0.0, duration * (i + 0.5) / count), max(0.0, duration - 0.05))
                 for i in range(count)]
        want = [min(total - 1, int(t * fps)) for t in times]
        wanted = set(want)
        for index, raw in enumerate(reader):
            if index in wanted:
                image = Image.frombytes("RGB", size, raw)
                if image.width > max_width:
                    h = max(1, round(image.height * max_width / image.width))
                    image = image.resize((max_width, h), Image.LANCZOS)
                frames.append((times[want.index(index)], image))
            if index > max(want):
                break
    except Exception:
        return []
    finally:
        try:
            reader.close()
        except Exception:
            pass
    return frames


def make_storyboard(frames: list[tuple[float, object]], out_dir: Path,
                    max_width: int = 1280, quality: int = 68) -> Path | None:
    """把帧按 3 列拼成一张带时间标签的 JPG。"""
    if not frames:
        return None
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    cols = 3
    rows = (len(frames) + cols - 1) // cols
    cell_w = max(320, int(max_width / cols))
    first = frames[0][1]
    cell_h = max(180, int(cell_w * first.height / max(1, first.width)))
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), "#10151f")
    draw = ImageDraw.Draw(sheet)
    for i, (seconds, image) in enumerate(frames):
        thumb = image.copy()
        thumb.thumbnail((cell_w, cell_h))
        x = (i % cols) * cell_w
        y = (i // cols) * cell_h
        sheet.paste(thumb, (x + (cell_w - thumb.width) // 2, y + 20))
        draw.text((x + 8, y + 3), f"#{i + 1}  {seconds:0.1f}s", fill="#ffffff")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "video-storyboard.jpg"
    sheet.save(target, "JPEG", quality=quality, optimize=True)
    return target


def describe_video(video_path: Path, brain, out_dir: Path, prompt: str = "") -> str:
    """抽帧 → storyboard → 原生视觉；返回中文描述（失败空串）。"""
    frames = sample_frames(video_path)
    if not frames:
        return ""
    storyboard = make_storyboard(frames, Path(out_dir))
    if storyboard is None:
        return ""
    ask = ("这是一段视频按时间顺序抽取的若干帧（已标 #序号 和时间）。"
           "请描述画面里发生了什么、人物/物体的动作变化；不要编造没出现的内容。"
           + (("\n用户关注：" + prompt) if prompt else ""))
    return str(brain.see_image(storyboard, ask) or "").strip()
