#!/usr/bin/env python3
"""从 YunYueSama/codex-deepseek-pet 图集构建「大肥鱼·重绘版」桌宠角色包。

数据流：
  1. research/codex-pet/assets/*.png（缺则从 raw.githubusercontent.com 下载，失败重试 2 次）
  2. 按 cell=512 切格，列数 = 图宽 // 512（motion-basic/motion-extra 是 8 列，其余 4 列）
  3. 取所有实际使用帧的并集 bbox 统一切片（保留跨帧对齐与脚底线）
  4. 预乘 alpha（RGBA→RGBa→LANCZOS→RGBA）缩放到 640×640 画布：角色高 600、底部居中
  5. 逐状态写 animated WebP（逐帧时长写入 webp，manifest 同时记录）
  6. 重新用 Pillow 打开全部 webp 自检，并输出 contact-sheet.png / manifest.json / README.md

用法（项目根目录）：python tools/build_codex_character.py [--quality 88] [--force-download]

许可：上游为「大肥鱼项目署名许可 1.0」，必须署名作者 YunYueSama 与仓库地址；
      角色是社区二创、非官方。完整许可文本复制到本包 licenses/。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "research" / "codex-pet"
ATLAS_DIR = DATA / "assets"
OUT = ROOT / "pet" / "assets" / "characters" / "codex-whale"
ANIM_DIR = OUT / "anim"

CELL = 512
CANVAS = (640, 640)          # 输出画布（与 dsh-fy 包一致，app 按 430px 宽显示）
CHAR_HEIGHT = 600            # 角色（含尾巴/举手等并集）在画布上的高度
BOTTOM_Y = 624               # 底边位置（640 画布留 16px 底部余量）
MAX_WIDTH = CANVAS[0] - 20   # 缩放后最宽不超过 620，保证左右各有余量

REPO = "https://github.com/YunYueSama/codex-deepseek-pet"
RAW_BASE = f"https://raw.githubusercontent.com/YunYueSama/codex-deepseek-pet/main/assets/whale/"
AUTHOR = "YunYueSama"
LICENSE_NAME = "大肥鱼项目署名许可 1.0"
FETCHED = "2026-10-25"

# name -> (预期字节数, 说明)。字节数来自 2026-10-25 的 GitHub contents API，用于识别截断下载。
ATLASES: dict[str, tuple[int, str]] = {
    "motion-basic": (11746160, "基础动作：eat 16-31 / wave 32-47（8 列）"),
    "motion-extra": (15760175, "扩展动作：jump 0-15 / dance 16-31 / sneak 32-47 / sleep 48-63（8 列）"),
    "motion-walk": (7131794, "行走 0-31（4 列）"),
    "motion-turn": (1755160, "转身 0-7（4 列）"),
    "motion-idle": (3873699, "待机呼吸/眨眼 0-15（4 列）"),
    "idle-front": (1516887, "正面高清待机（4 列，上游用于单帧呼吸+眨眼叠加；本包未使用）"),
    "actions": (3898755, "表情动作 0-15（4 列，情绪状态主来源）"),
    "expressions": (3905134, "补充表情 0-15（4 列，本包未使用）"),
}

# 状态定义（frames 为上游图集帧号；ms 可为整数或与 frames 等长的列表）
STATES: list[dict] = [
    dict(state="idle", atlas="motion-idle", frames=list(range(16)), ms=160, loop=True, sheet_frame=0,
         note="待机呼吸+眨眼"),
    dict(state="walk", atlas="motion-walk", frames=list(range(32)), ms=70, loop=True, sheet_frame=6,
         note="侧向行走（备用，app 暂未驱动）"),
    dict(state="thinking", atlas="motion-turn", frames=[0, 1, 2, 3, 4, 4, 3, 2, 1], ms=240,
         loop=True, sheet_frame=4, note="转身看侧面的往复循环，代替思考动作"),
    dict(state="talking", atlas="motion-idle", frames=[0, 1, 2, 1], ms=120, loop=True, sheet_frame=0,
         note="轻微起伏模拟说话（无真口型）"),
    dict(state="wave", atlas="motion-basic", frames=list(range(32, 48)), ms=90, loop=False, sheet_frame=40,
         note="抬手挥动一次"),
    dict(state="happy", atlas="motion-basic", frames=list(range(32, 48)), ms=90, loop=True, sheet_frame=40,
         note="复用举手挥动循环表达开心"),
    dict(state="eat", atlas="motion-basic", frames=list(range(16, 32)), ms=100, loop=True, sheet_frame=22,
         note="拿起口粮→吃→满足"),
    dict(state="jump", atlas="motion-extra", frames=list(range(16)), ms=90, loop=False, sheet_frame=5,
         note="下蹲→起跳→落地"),
    dict(state="dance", atlas="motion-extra", frames=list(range(16, 32)), ms=90, loop=True, sheet_frame=18,
         note="左右摇摆舞步"),
    dict(state="sneak", atlas="motion-extra", frames=list(range(32, 48)), ms=110, loop=True, sheet_frame=38,
         note="双手交握的潜行小步"),
    dict(state="sleepy", atlas="motion-extra", frames=list(range(48, 64)), ms=150, loop=True, sheet_frame=60,
         note="打哈欠→坐下→躺睡（循环时会重新起身）"),
    dict(state="shy", atlas="actions", frames=[0, 2, 2, 0], ms=240, loop=True, sheet_frame=2,
         note="脸红、双手抱在胸前（帧 2 本身带腮红）"),
    dict(state="angry", atlas="actions", frames=[0, 8, 8, 4, 0], ms=180, loop=True, sheet_frame=8,
         note="鼓腮→哼一声（帧 8 鼓腮，帧 4 抱胸）"),
    dict(state="sad", atlas="actions", frames=[0, 9, 9, 0], ms=450, loop=True, sheet_frame=9,
         note="委屈低头、双手交握"),
    dict(state="surprised", atlas="actions", frames=[0, 10, 10, 0], ms=180, loop=True, sheet_frame=10,
         note="正面张嘴举手（上游旧定义用帧 14 的侧身惊起，正面更清楚）"),
    dict(state="confused", atlas="actions", frames=[0, 3, 3, 0], ms=240, loop=True, sheet_frame=3,
         note="手托下巴、视线偏上（上游旧定义 0/6/6/0 是吃东西帧，不合适）"),
    dict(state="proud", atlas="actions", frames=[0, 4, 4, 0], ms=350, loop=True, sheet_frame=4,
         note="抱手、半眯眼的得意脸"),
    dict(state="hungry", atlas="actions", frames=[0, 6, 6, 0], ms=240, loop=True, sheet_frame=6,
         note="拿着口粮凑到嘴边"),
]

EMOTIONS = ["happy", "shy", "angry", "sad", "surprised", "confused", "sleepy", "proud", "hungry"]


# ---------------------------------------------------------------- 下载
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_once(url: str, dest: Path) -> bool:
    """urllib 优先，失败再试 curl.exe（本机有 curl）。成功返回 True。"""
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "codex-whale-builder/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as handle:
            shutil.copyfileobj(resp, handle)
        tmp.replace(dest)
        return True
    except Exception as exc:  # noqa: BLE001 - 网络异常种类多，统一降级重试
        print(f"    urllib 失败：{exc}")
        tmp.unlink(missing_ok=True)
    curl = shutil.which("curl")
    if not curl:
        return False
    try:
        result = subprocess.run([curl, "-L", "--fail", "--silent", "--show-error",
                                 "-o", str(tmp), url], timeout=180)
        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(dest)
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"    curl 失败：{exc}")
    tmp.unlink(missing_ok=True)
    return False


def ensure_atlases(force: bool = False) -> dict[str, Atlas]:
    ATLAS_DIR.mkdir(parents=True, exist_ok=True)
    log_lines = ["# 上游图集下载记录", "",
                 f"- 仓库：{REPO}（main 分支）",
                 f"- 作者：{AUTHOR}",
                 f"- 许可：{LICENSE_NAME}（允许使用/修改/分享/商用，须署名）",
                 f"- 记录日期：{FETCHED}", "",
                 "| 文件 | 字节 | sha256 |", "| --- | --- | --- |"]
    atlases: dict[str, Atlas] = {}
    for name, (expected, desc) in ATLASES.items():
        dest = ATLAS_DIR / f"{name}.png"
        url = RAW_BASE + f"{name}.png"
        valid = dest.exists() and dest.stat().st_size == expected
        if force or not valid:
            if dest.exists() and not valid:
                print(f"[下载] {name}.png 大小不符（{dest.stat().st_size} != {expected}），重新下载")
                dest.unlink()
            else:
                print(f"[下载] {name}.png ← {url}")
            ok = False
            for attempt in range(1, 4):  # 首次 + 失败重试 2 次
                if _download_once(url, dest) and dest.exists() and dest.stat().st_size == expected:
                    ok = True
                    print(f"    完成 attempt={attempt} size={dest.stat().st_size}")
                    break
                time.sleep(1.5 * attempt)
            if not ok:
                size = dest.stat().st_size if dest.exists() else 0
                raise SystemExit(f"下载失败（重试 2 次后仍不完整）：{url} size={size} expected={expected}")
        else:
            print(f"[下载] 跳过（已存在且大小正确）：{name}.png")
        sha = _sha256(dest)
        log_lines.append(f"| `{name}.png` | {dest.stat().st_size} | `{sha}` |")
        print(f"        {desc}")
        atlases[name] = Atlas(name, dest)
    log_lines += ["", "下载来源：`raw.githubusercontent.com/YunYueSama/codex-deepseek-pet/main/assets/whale/<name>.png`", ""]
    (ATLAS_DIR / "DOWNLOAD.md").write_text("\n".join(log_lines), encoding="utf-8")
    return atlases


# ---------------------------------------------------------------- 图集切片
class Atlas:
    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        image = Image.open(path)
        if image.size[0] % CELL or image.size[1] % CELL:
            raise SystemExit(f"{name} 尺寸 {image.size} 不是 {CELL} 的整数倍，布局与预期不符")
        self.image = image.convert("RGBA")
        self.columns = self.image.size[0] // CELL
        self.rows = self.image.size[1] // CELL
        self.count = self.columns * self.rows
        print(f"[图集] {name:14s} {image.size[0]:4d}x{image.size[1]:<4d} 列={self.columns} 行={self.rows} 帧={self.count}")

    def frame(self, index: int) -> Image.Image:
        if not 0 <= index < self.count:
            raise SystemExit(f"{self.name} 没有帧 {index}（共 {self.count} 帧）")
        row, col = divmod(index, self.columns)
        return self.image.crop((col * CELL, row * CELL, (col + 1) * CELL, (row + 1) * CELL))


# ---------------------------------------------------------------- 帧处理
def union_bbox(atlases: dict[str, Atlas]) -> tuple[int, int, int, int]:
    used: dict[str, set[int]] = {}
    for spec in STATES:
        used.setdefault(spec["atlas"], set()).update(spec["frames"])
    boxes = []
    for name, indexes in sorted(used.items()):
        atlas = atlases[name]
        for index in sorted(indexes):
            bbox = atlas.frame(index).getbbox()
            if bbox:
                boxes.append(bbox)
    if not boxes:
        raise SystemExit("所有帧都是空的，无法计算并集 bbox")
    ux0 = min(b[0] for b in boxes)
    uy0 = min(b[1] for b in boxes)
    ux1 = max(b[2] for b in boxes)
    uy1 = max(b[3] for b in boxes)
    print(f"[对齐] 使用帧并集 bbox=({ux0},{uy0},{ux1},{uy1}) 尺寸={ux1 - ux0}x{uy1 - uy0}")
    return ux0, uy0, ux1, uy1


def resize_premultiplied(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """预乘 alpha 缩放：RGBA→RGBa→LANCZOS→RGBA，避免浅色毛边。"""
    return image.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def collapse_identical(frames: list[Image.Image], durations: list[int]) -> tuple[list[Image.Image], list[int], list[int]]:
    """合并连续完全相同的帧（时长相加）。Pillow 的 WebP 编码器本来也会这样做，
    这里显式处理以便 manifest 记录的帧数与文件一致，同时保留源帧 → 输出帧的映射。

    返回 (输出帧, 输出时长, source_to_output)；source_to_output 与输入等长。
    """
    out_frames: list[Image.Image] = []
    out_durations: list[int] = []
    mapping: list[int] = []
    previous: bytes | None = None
    for frame, duration in zip(frames, durations):
        payload = frame.tobytes()
        if previous is not None and payload == previous:
            out_durations[-1] += duration
            mapping.append(len(out_frames) - 1)
            continue
        out_frames.append(frame)
        out_durations.append(duration)
        mapping.append(len(out_frames) - 1)
        previous = payload
    return out_frames, out_durations, mapping


def render_frames(frames: list[Image.Image], durations: list[int], bbox: tuple[int, int, int, int],
                  quality: int, dest: Path) -> dict:
    """把源帧渲染到 640×640 画布并写成 animated WebP，返回帧数/时长/源帧映射。"""
    ux0, uy0, ux1, uy1 = bbox
    crop_w, crop_h = ux1 - ux0, uy1 - uy0
    scale = CHAR_HEIGHT / float(crop_h)
    target_w, target_h = max(1, round(crop_w * scale)), CHAR_HEIGHT
    if target_w > MAX_WIDTH:
        scale = MAX_WIDTH / float(crop_w)
        target_w, target_h = MAX_WIDTH, max(1, round(crop_h * scale))
    offset_x = (CANVAS[0] - target_w) // 2
    offset_y = BOTTOM_Y - target_h
    canvas_frames = []
    for source in frames:
        cropped = source.crop((ux0, uy0, ux1, uy1))
        scaled = resize_premultiplied(cropped, (target_w, target_h))
        canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        canvas.paste(scaled, (offset_x, offset_y))
        canvas_frames.append(canvas)
    canvas_frames, canvas_durations, mapping = collapse_identical(canvas_frames, durations)
    first, rest = canvas_frames[0], canvas_frames[1:]
    first.save(dest, "WEBP", save_all=True, append_images=rest, duration=canvas_durations,
               loop=0, quality=quality, method=6, exact=True, alpha_quality=100)
    print(f"    缩放 {target_w}x{target_h} @({offset_x},{offset_y}) "
          f"源帧 {len(frames)} → 输出 {len(canvas_frames)} 帧（合并连续重复帧）→ {dest.name}")
    return {"frames": len(canvas_frames), "durations": canvas_durations, "source_to_output": mapping}


# ---------------------------------------------------------------- 校验
def verify(manifest: dict) -> dict:
    print("\n[自检] Pillow 重新打开全部 webp")
    print(f"{'状态':12s} {'文件':>16s} {'帧':>3s} {'尺寸':>10s} {'通道':>5s} {'透明':>4s} {'字节':>9s}")
    total = 0
    problems = []
    for name, meta in manifest["states"].items():
        path = OUT / meta["file"]
        total += path.stat().st_size
        image = Image.open(path)
        frames = 0
        durations = []
        while True:
            try:
                image.seek(frames)
            except EOFError:
                break
            if image.size != CANVAS:
                problems.append(f"{name} 第 {frames} 帧尺寸 {image.size} != {CANVAS}")
            if "A" not in image.getbands():
                problems.append(f"{name} 第 {frames} 帧没有 alpha 通道（{image.mode}/{image.getbands()}）")
            if frames == 0 and image.getchannel("A").getextrema()[0] != 0:
                problems.append(f"{name} 没有透明像素")
            durations.append(int(image.info.get("duration") or 0))
            frames += 1
            if not getattr(image, "is_animated", False):
                break
        if frames != meta["frames"]:
            problems.append(f"{name} 帧数 {frames} != manifest {meta['frames']}")
        image.seek(0)
        print(f"{name:12s} {path.name:>16s} {frames:3d} {str(image.size):>10s} "
              f"{image.mode:>5s} {'是' if 'A' in image.getbands() else '否':>4s} {path.stat().st_size:9d}")
    print(f"\n总字节：{total} ({total / 1024 / 1024:.2f} MB)  状态数：{len(manifest['states'])}")
    if total >= 40 * 1024 * 1024:
        problems.append(f"总体积 {total / 1024 / 1024:.1f}MB 超过 40MB")
    if len(manifest["states"]) < 14:
        problems.append(f"状态数 {len(manifest['states'])} 少于 14")
    if problems:
        print("[自检] 失败：")
        for item in problems:
            print("  -", item)
        raise SystemExit(1)
    print("[自检] 通过：尺寸/透明通道/帧数/体积/状态数全部符合预期")
    return {"total": total}


# ---------------------------------------------------------------- contact sheet
def build_contact_sheet(manifest: dict, built: dict[str, dict]) -> Path:
    thumb = 300
    pad = 14
    label_h = 30
    columns = 6
    names = list(manifest["states"])
    rows = (len(names) + columns - 1) // columns
    width = pad + columns * (thumb + pad)
    height = pad + rows * (thumb + label_h + pad)
    sheet = Image.new("RGB", (width, height), (34, 38, 46))
    draw = ImageDraw.Draw(sheet)
    for index, name in enumerate(names):
        meta = manifest["states"][name]
        row, col = divmod(index, columns)
        x = pad + col * (thumb + pad)
        y = pad + row * (thumb + label_h + pad)
        background = Image.new("RGB", (thumb, thumb), (208, 213, 222))
        pixel = ImageDraw.Draw(background)
        step = 14
        for by in range(0, thumb, step):
            for bx in range(0, thumb, step):
                if (bx // step + by // step) % 2:
                    pixel.rectangle([bx, by, bx + step - 1, by + step - 1], fill=(170, 177, 188))
        image = Image.open(OUT / meta["file"])
        spec = next(item for item in STATES if item["state"] == name)
        # sheet_frame 是上游图集帧号；映射到「合并连续重复帧后」的输出帧下标
        source_index = spec["frames"].index(spec["sheet_frame"]) if spec["sheet_frame"] in spec["frames"] else 0
        out_index = built[name]["source_to_output"][source_index]
        image.seek(min(out_index, meta["frames"] - 1))
        frame = image.convert("RGBA")
        frame.thumbnail((thumb, thumb), Image.LANCZOS)
        background.paste(frame, ((thumb - frame.width) // 2, thumb - frame.height), frame)
        sheet.paste(background, (x, y))
        draw.text((x + 2, y + thumb + 5),
                  f"{name}  {meta['frames']}f/{meta['duration_ms']}ms"
                  f"{'/loop' if meta['loop'] else ''}", fill=(236, 240, 248))
    dest = OUT / "contact-sheet.png"
    sheet.save(dest)
    print(f"[contact] {dest.relative_to(ROOT)} {sheet.size[0]}x{sheet.size[1]}")
    return dest


# ---------------------------------------------------------------- README
README_TEMPLATE = """# 大肥鱼·重绘版（`codex-whale` 角色包）

桌宠「大肥鱼」的可切换形象包：从社区项目
[YunYueSama/codex-deepseek-pet]({repo}) 的高清精灵图集裁切、对齐、缩放并重编码成
{count} 个状态（待机/说话/动作/9 个情绪），画布统一 640×640 透明背景，供 `pet/app.py`
按 430px 宽显示。

> 这是社区二创角色，不是 DeepSeek 官方形象/授权；与上游项目也无隶属或代言关系。

在 `pet/app.py` 里通过 `config.json` 的 `character` 或右键菜单「换形象」切换，角色 id 是
`codex-whale`（显示名「大肥鱼·重绘版」）；旧的 `dsh-fy` 高清立绘包继续保留，可随时切回。

## 来源链

1. 上游仓库：[{repo}]({repo})（main 分支）  
   作者：**{author}**  
   许可：**{license}**（自定义许可，允许使用/修改/分享/商用，必须署名；完整文本见 [`licenses/`](licenses/)）
2. 本包只使用上游 `assets/whale/` 下 8 张精灵图集（未下载整个仓库），原始文件保存在
   [`research/codex-pet/assets/`](../../../../research/codex-pet/assets/)，
   下载 URL、字节数与 sha256 记录在同目录 `DOWNLOAD.md`。
3. 帧号定义参考上游 `src/renderer/motion.js`（已存档为
   [`research/codex-pet/src_renderer_motion.js`](../../../../research/codex-pet/src_renderer_motion.js)）；
   上游后来新增了 `dense-*` / `inbetween-*` 等更高帧数的批次，本包未使用。
4. 本包相对上游的修改：按 512 单元格切帧；取所有使用帧的并集 bbox 统一裁切；预乘 alpha
   缩放（RGBA→RGBa→LANCZOS→RGBA，避免浅色毛边）；缩放到 640×640 画布、角色高 600、
   底部居中；逐状态重编码为 animated WebP；为情绪状态组合上游帧号成反应循环；
   把连续完全相同的帧合并（时长相加）后写进 webp。

### 必须保留的署名

```text
作者：{author}
仓库：{repo}
```

使用/展示/分发本包（或其衍生作品）时请保留以上两项署名，并说明经过修改。

## 图集布局（已核对实际像素尺寸）

| 图集 | 尺寸 | 列×行 | 帧数 | 本包用途 |
| --- | --- | --- | --- | --- |
| `motion-idle.png` | 2048×2048 | 4×4 | 16 | idle、talking |
| `motion-walk.png` | 2048×4096 | 4×8 | 32 | walk |
| `motion-turn.png` | 2048×1024 | 4×2 | 8 | thinking（0-4 往返） |
| `motion-basic.png` | 4096×3072 | 8×6 | 48 | eat（16-31）、wave/happy（32-47） |
| `motion-extra.png` | 4096×4096 | 8×8 | 64 | jump（0-15）、dance（16-31）、sneak（32-47）、sleepy（48-63） |
| `actions.png` | 2048×2048 | 4×4 | 16 | shy/angry/sad/surprised/confused/proud/hungry |
| `expressions.png` | 2048×2048 | 4×4 | 16 | 仅存档，本包未使用 |
| `idle-front.png` | 2048×1024 | 4×2 | 8 | 仅存档（上游用于单帧待机 + 眨眼叠加，本包改用 motion-idle） |

## 状态

| 状态 | 源 | 帧 | 帧时长 | 循环 | 说明 |
| --- | --- | --- | --- | --- | --- |
{state_table}

全部 webp 的逐帧时长已写入文件本身；`manifest.json` 的 `duration_ms` 只记录代表性帧时长，
`loop` 是声明值，实际播放是否循环由 `app.py` 的 `Animator.play(once=...)` 决定。
「帧」列是上游图集帧号；组合序列里相同帧会合并成更长的停留（例如 `shy` 的 `0,2,2,0`
输出为 3 帧，中间帧停留 480ms），manifest 的 `frames` 记录合并后的输出帧数。

## 重建

```powershell
cd <仓库目录>
python tools/build_codex_character.py
```

脚本可重复运行：图集已存在且字节数正确时跳过下载，输出会被完整覆盖；缺少 Pillow 时先
`pip install pillow`。自检会重新用 Pillow 打开每个 webp，打印帧数/尺寸/透明通道/体积，
并检查状态数 ≥ 14、总体积 < 40MB。

## 已知限制

- **没有真口型/分层骨骼**：所有帧都是上游整身逐帧画，嘴、手臂、尾巴不能单独驱动；
  `talking` 用 motion-idle 的轻微起伏代替代口型。
- **帧号来自上游批次**：`src/renderer/motion.js` 是上游新版；本包所用 8 张图集是同一角色
  的较早批次。上游 `dense-*`/`inbetween-*` 批次帧数更多、更细腻，本包未纳入。
- **部分情绪是上游单帧组合**：`shy`/`angry`/`sad`/`surprised`/`confused`/`proud`/`hungry`
  由 `actions.png` 的中性帧 + 表情帧组成短循环（见上表），表情本身没有逐帧过渡。
- `happy` 与 `wave` 复用同一组举手帧（上游 wave 32-47），区别只在是否循环播放。
- `sleepy` 是「打哈欠→坐下→躺下」的完整循环；循环重启时会从躺姿跳回站姿。
- `surprised` 用正面帧 10，`confused` 用托下巴帧 3，与上游旧版 `motion.js` 里
  `surprise` 用帧 14、`hungry` 用帧 6 的旧定义不同（旧定义里帧 6 是吃东西、帧 14 是侧身，
  放在桌宠正面显示不如前者清楚）；两者都来自同一张上游图集，没有重绘。
- 画布是 640×640、角色高约 600；比 `dsh-fy` 立绘包的 1280 设计稿小一档，但显示宽度同为
  430px，像素密度足够。

## 文件

- `anim/<state>.webp`：{count} 个状态的动画
- `manifest.json`：状态/时长/循环/来源帧映射（schema 与 `pet/assets/dfy/manifest.json` 一致，另有 `source_clips` 溯源）
- `contact-sheet.png`：各状态代表帧与状态名
- `licenses/`：上游完整许可文本（LICENSE、ASSET_LICENSE.md）

构建日期：{date}
"""


def build_manifest(atlases: dict[str, Atlas], total_bytes: int, bbox: tuple[int, int, int, int],
                   built: dict[str, dict]) -> dict:
    states = {}
    source_clips = {}
    for spec in STATES:
        name = spec["state"]
        frames = spec["frames"]
        durations = [spec["ms"]] * len(frames) if isinstance(spec["ms"], int) else list(spec["ms"])
        # 输出帧数与时长按实际写入 webp 的值记录（连续重复帧已合并时长）
        states[name] = {
            "file": f"anim/{spec['state']}.webp",
            "frames": built[name]["frames"],
            "duration_ms": built[name]["durations"][0],
            "loop": bool(spec["loop"]),
        }
        source_clips[name] = {"atlas": f"{spec['atlas']}.png", "frames": frames,
                              "durations_ms": durations, "loop": bool(spec["loop"]),
                              "output_frames": built[name]["frames"],
                              "output_durations_ms": built[name]["durations"],
                              "note": spec["note"]}
    manifest = {
        "schema": 1,
        "display_name": "大肥鱼·重绘版",
        "character": "codex-whale",
        "source": {
            "name": "大肥鱼·重绘版",
            "repo": REPO,
            "author": AUTHOR,
            "license": LICENSE_NAME,
            "license_files": ["licenses/LICENSE.txt", "licenses/ASSET_LICENSE.md"],
            "official": False,
            "community_derivative": True,
            "fetched": FETCHED,
            "modified": True,
            "note": "由上游精灵图集裁切/缩放/重编码；角色为社区二创，非 DeepSeek 官方形象。",
        },
        "designSize": [CELL, CELL],
        "size": list(CANVAS),
        "anchor": {"x": 0.5, "y": "bottom", "bottom_margin": CANVAS[1] - BOTTOM_Y},
        "char_height": CHAR_HEIGHT,
        "states": states,
        "emotions": EMOTIONS,
        "source_clips": source_clips,
        "alignment": {"cell": CELL, "union_bbox": list(bbox),
                      "atlas_columns": {name: atlas.columns for name, atlas in atlases.items()}},
        "build": {"script": "tools/build_codex_character.py", "date": time.strftime("%Y-%m-%d"),
                  "atlas_dir": "research/codex-pet/assets/"},
        "size_bytes": total_bytes,
    }
    return manifest


def write_readme(manifest: dict) -> None:
    rows = []
    for spec in STATES:
        name = spec["state"]
        ms = spec["ms"]
        durations = f"{ms}" if isinstance(ms, int) else "/".join(str(item) for item in ms)
        frames = spec["frames"]
        if frames == list(range(frames[0], frames[-1] + 1)):
            frame_text = f"{frames[0]}–{frames[-1]}"
        else:
            frame_text = ", ".join(str(item) for item in frames)
        rows.append(f"| `{name}` | {spec['atlas']}.png | {frame_text} | {durations} ms | "
                    f"{'是' if spec['loop'] else '否'} | {spec['note']} |")
    text = README_TEMPLATE.format(
        repo=REPO, author=AUTHOR, license=LICENSE_NAME,
        state_table="\n".join(rows), count=len(manifest["states"]),
        date=time.strftime("%Y-%m-%d"),
    )
    (OUT / "README.md").write_text(text, encoding="utf-8")
    print(f"[README] {OUT / 'README.md'}")


# ---------------------------------------------------------------- 主流程
def main() -> int:
    parser = argparse.ArgumentParser(description="构建「大肥鱼·重绘版」角色包")
    parser.add_argument("--quality", type=int, default=88, help="WebP 质量（默认 88）")
    parser.add_argument("--force-download", action="store_true", help="强制重新下载上游图集")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    ANIM_DIR.mkdir(parents=True, exist_ok=True)

    print("== 1/6 下载上游图集 ==")
    atlases = ensure_atlases(force=args.force_download)

    print("\n== 2/6 计算并集对齐 bbox ==")
    bbox = union_bbox(atlases)

    print("\n== 3/6 生成动画 ==")
    built: dict[str, dict] = {}
    for spec in STATES:
        atlas = atlases[spec["atlas"]]
        frames = [atlas.frame(index) for index in spec["frames"]]
        durations = [spec["ms"]] * len(frames) if isinstance(spec["ms"], int) else list(spec["ms"])
        if len(durations) != len(frames):
            raise SystemExit(f"{spec['state']} 帧数与时长数不一致")
        dest = ANIM_DIR / f"{spec['state']}.webp"
        print(f"[状态] {spec['state']}：{spec['atlas']} 帧 {spec['frames']} "
              f"时长 {durations[0]}ms×{len(frames)} {'loop' if spec['loop'] else 'once'}")
        built[spec["state"]] = render_frames(frames, durations, bbox, args.quality, dest)

    print("\n== 4/6 写成 manifest ==")
    total_bytes = sum((ANIM_DIR / f"{spec['state']}.webp").stat().st_size for spec in STATES)
    manifest = build_manifest(atlases, total_bytes, bbox, built)
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"[manifest] {OUT / 'manifest.json'}")

    print("\n== 5/6 复制许可文本 ==")
    license_dir = OUT / "licenses"
    license_dir.mkdir(exist_ok=True)
    copied = 0
    for src, dest_name in ((DATA / "LICENSE", "LICENSE.txt"),
                           (DATA / "ASSET_LICENSE.md", "ASSET_LICENSE.md")):
        if not src.exists():  # 干净克隆里没有 research/：从上游仓库补下许可文本（署名许可要求随素材分发）
            src.parent.mkdir(parents=True, exist_ok=True)
            raw = RAW_BASE.split("/assets/whale/")[0] + "/" + src.name
            if not _download_once(raw, src):
                print(f"[许可] 下载 {src.name} 失败：{raw}")
        if src.exists():
            shutil.copy2(src, license_dir / dest_name)
            copied += 1
            print(f"[许可] {dest_name} ← {src.relative_to(ROOT)}")
    if copied != 2:
        print("[许可] 警告：research/codex-pet/ 缺少 LICENSE 或 ASSET_LICENSE.md，未能完整复制")

    print("\n== 6/6 自检与产物 ==")
    verify(manifest)
    build_contact_sheet(manifest, built)
    write_readme(manifest)
    print(f"\n完成：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
