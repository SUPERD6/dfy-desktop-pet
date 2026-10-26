# -*- coding: utf-8 -*-
"""在桌面放一个启动图标：双击就打开桌宠（已经开着就把她叫出来，不会开第二只）。

- 图标：用当前形象的待机帧做成多尺寸 .ico（pet/icon.ico，本机生成，不进 Git——形象素材有授权限制）；
- 快捷方式：pythonw 启动 pet/app.py（无黑框），名字用 config.display_name；
- 用 Windows 自带的 WScript.Shell 建 .lnk，不装额外依赖。

用法：python tools/make_shortcut.py [--remove]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PET = ROOT / "pet"


def config() -> dict:
    try:
        return json.loads((PET / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_icon(cfg: dict) -> Path:
    character = str(cfg.get("character") or "")
    candidates = [PET / "assets" / "characters" / character / "anim" / "idle.webp",
                  PET / "assets" / "anim" / "idle.webp"]
    source = next((c for c in candidates if c.exists()), None)
    icon = PET / "icon.ico"
    if source is None:
        img = Image.new("RGBA", (256, 256), (70, 110, 200, 255))
    else:
        img = Image.open(source)
        img.seek(0)
        img = img.convert("RGBA")
        box = img.getchannel("A").getbbox()
        if box:
            img = img.crop(box)
        # 只取上半身（头像更认得出来），放进正方形
        img = img.crop((0, 0, img.width, min(img.height, int(img.width * 1.05))))
        side = max(img.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
        img = canvas.resize((256, 256), Image.LANCZOS)
    img.save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return icon


def desktop() -> Path:
    """桌面目录（可能被 OneDrive 重定向、路径带中文）：直接问 Windows，返回 Unicode，不经控制台编码。"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:  # CSIDL_DESKTOPDIRECTORY
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Desktop"


def pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else (shutil.which("pythonw") or exe))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    cfg = config()
    name = str(cfg.get("display_name") or "大肥鱼")
    link = desktop() / f"{name}.lnk"
    if args.remove:
        if link.exists():
            link.unlink()
            print(f"已删除桌面图标：{link}")
        return 0
    icon = build_icon(cfg)
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:DFY_LNK);"
        "$s.TargetPath=$env:DFY_TARGET;$s.Arguments=$env:DFY_ARGS;$s.WorkingDirectory=$env:DFY_WD;"
        "$s.IconLocation=$env:DFY_ICON;$s.Description=$env:DFY_DESC;$s.Save()"
    )
    env = {"DFY_LNK": str(link), "DFY_TARGET": pythonw(), "DFY_ARGS": f'"{PET / "app.py"}"',
           "DFY_WD": str(PET), "DFY_ICON": f"{icon},0", "DFY_DESC": f"{name}：住在桌面上的鲸鱼娘"}
    import os
    result = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env={**os.environ, **env},
                            capture_output=True, text=True, encoding="mbcs", errors="replace")
    if result.returncode != 0 or not link.exists():
        print("建快捷方式失败：", result.stderr.strip()[:300])
        return 1
    print(f"桌面图标：{link}（图标 {icon.name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
