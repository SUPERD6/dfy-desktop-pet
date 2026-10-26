# -*- coding: utf-8 -*-
"""把一张透明底立绘变成 3D 模型（.glb，带顶点颜色），给 body3d 用。

用 TripoSR（MIT，Tripo AI & Stability AI）在本机显卡上跑；代码与依赖在 .tools/（不进 Git）：
    .tools/TripoSR/        代码（isosurface 换成 scikit-image 的 marching cubes，免编译）
    .tools/tripo-venv/     独立虚拟环境（复用系统里的 CUDA 版 PyTorch）
    .tools/hf-cache/       模型权重缓存（首次约 2GB）

单张正面图生成：背面是模型猜的，没有骨骼和表情；要能做表情的正式形象仍需 VRoid/工房模型。

用法（用 .tools/tripo-venv 里的 python 运行）：
    python tools/image_to_3d.py <透明底图片> <输出.glb> [--resolution 256]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
os.environ.setdefault("HF_HOME", str(TOOLS / "hf-cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(TOOLS / "hf-cache" / "transformers"))
sys.path.insert(0, str(TOOLS / "TripoSR"))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402


def prepare(path: Path, size: int = 512, ratio: float = 0.85) -> Image.Image:
    """裁到不透明区域 → 方形居中、角色占 85% → 贴在 50% 灰底上（TripoSR 训练时的输入习惯）。"""
    img = Image.open(path)
    img.seek(0)
    img = img.convert("RGBA")
    box = img.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    if box:
        img = img.crop(box)
    side = int(max(img.size) / ratio)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    canvas = canvas.resize((size, size), Image.LANCZOS)
    rgba = np.asarray(canvas).astype(np.float32) / 255.0
    rgb = rgba[..., :3] * rgba[..., 3:4] + 0.5 * (1 - rgba[..., 3:4])
    return Image.fromarray((rgb * 255).clip(0, 255).astype(np.uint8))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--resolution", type=int, default=256, help="marching cubes 网格分辨率")
    args = parser.parse_args()

    from tsr.system import TSR

    device = "cuda" if torch.cuda.is_available() else "cpu"
    image = prepare(args.image)
    image.save(args.output.with_suffix(".input.png"))
    model = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
    model.renderer.set_chunk_size(8192)
    model.to(device)
    with torch.no_grad():
        scene_codes = model([image], device=device)
        meshes = model.extract_mesh(scene_codes, True, resolution=args.resolution)
    mesh = meshes[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(args.output))
    print(f"3D 模型：{args.output}（顶点 {len(mesh.vertices)}，面 {len(mesh.faces)}，"
          f"范围 {np.round(mesh.bounds, 3).tolist()}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
