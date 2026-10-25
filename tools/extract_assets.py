# -*- coding: utf-8 -*-
"""extract_assets.py -- 从 pet.html 中提取鲸鱼娘动画与语音素材（可重复运行，幂等）。

输入（默认）: pet/assets/source/pet.html
输出目录    : pet/assets/

产出:
  pet/assets/anim/<state>.webp            原始动画 WebP（保留透明通道，逐字节复制）
  pet/assets/anim/<state>_frame0.png      每个动画状态的第一帧 PNG（静态兜底）
  pet/assets/anim/idle_static.webp        待机静止帧（源中 ANIM_IDLE_STATIC）
  pet/assets/voice/<id>.mp3               49 条预合成语音（原始 MP3 字节）
  pet/assets/manifest.json                状态→动画、id→台词/文件 的清单
  pet/assets/lines.json                   原始台词表（LINES）+ 语音 id→文本映射

用法:
  python tools/extract_assets.py                       # 默认路径
  python tools/extract_assets.py --source X --out Y    # 自定义路径

幂等性: 每次运行按 manifest 旧记录清理已不再产出的文件，其余文件原样覆盖；
重复运行得到的输出内容完全相同（不含时间戳）。不会删除 manifest 未记录的文件
（例如 test_edge_tts.mp3）。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_SOURCE = REPO_ROOT / "pet" / "assets" / "source" / "pet.html"
DEFAULT_OUT = REPO_ROOT / "pet" / "assets"

SOURCE_PROJECT = "aceice01/dsh-whale-pet"
SOURCE_URL = (
    "https://raw.githubusercontent.com/aceice01/dsh-whale-pet/master/"
    "plugin/dsh-balance-widget/lib/pet.html"
)
SOURCE_LICENSE = "非商业；角色素材 CC BY-NC-SA 4.0"
SOURCE_FETCHED = "2026-10-25"

# emoji / 符号区间（与 pet.html 中 speak() 的清理规则一致，用于台词比对）
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF\u2190-\u21FF\u200D\u20E3]"
)


# ---------------------------------------------------------------- parsing utils
def extract_balanced(text: str, anchor: str, open_ch: str = "{", close_ch: str = "}") -> str:
    """返回 anchor 之后第一个 open_ch..close_ch 平衡片段（忽略字符串字面量内的括号）。"""
    start = text.index(anchor) + len(anchor)
    i = text.index(open_ch, start)
    depth = 0
    in_str: str | None = None
    j = i
    while j < len(text):
        c = text[j]
        if in_str:
            if c == "\\":
                j += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in "\"'":
                in_str = c
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return text[i : j + 1]
        j += 1
    raise ValueError("unbalanced %r starting at %d" % (anchor, i))


def parse_js_object_literal(text: str) -> dict:
    """解析只有「键: [字符串, ...]」形式的 JS 对象字面量（如 LINES）。"""
    body = text.strip()
    if not (body.startswith("{") and body.endswith("}")):
        raise ValueError("not an object literal")
    body = body[1:-1]
    out: dict[str, list[str]] = {}
    pos = 0
    key_re = re.compile(r"\s*(?:\"([^\"]+)\"|'([^']+)'|([A-Za-z_$][\w$]*))\s*:\s*\[")
    str_re = re.compile(r'"(?:[^"\\]|\\.)*"')
    while True:
        m = key_re.match(body, pos)
        if not m:
            break
        key = m.group(1) or m.group(2) or m.group(3)
        arr_start = m.end() - 1
        # 找到与之匹配的 ]
        depth = 0
        in_str = None
        j = arr_start
        while j < len(body):
            c = body[j]
            if in_str:
                if c == "\\":
                    j += 2
                    continue
                if c == in_str:
                    in_str = None
            else:
                if c in "\"'":
                    in_str = c
                elif c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        arr_text = body[arr_start : j + 1]
        items = [json.loads(s) for s in str_re.findall(arr_text)]
        out[key] = items
        pos = j + 1
        # 跳过逗号/空白
        while pos < len(body) and body[pos] in " \t\r\n,":
            pos += 1
    return out


def webp_chunks(raw: bytes) -> list[tuple[bytes, bytes]]:
    if raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        raise ValueError("not a RIFF/WEBP container")
    riff_size = struct.unpack("<I", raw[4:8])[0]
    pos = 12
    end = min(8 + riff_size, len(raw))
    chunks = []
    while pos + 8 <= end:
        tag = raw[pos : pos + 4]
        size = struct.unpack("<I", raw[pos + 4 : pos + 8])[0]
        chunks.append((tag, raw[pos + 8 : pos + 8 + size]))
        pos += 8 + size + (size & 1)
    return chunks


def webp_frame_durations(raw: bytes) -> list[int]:
    """从 ANMF 块读取逐帧时长（毫秒）；静态图或无 ANMF 时返回 []。"""
    try:
        return [
            int.from_bytes(payload[12:15], "little")
            for tag, payload in webp_chunks(raw)
            if tag == b"ANMF" and len(payload) >= 16
        ]
    except Exception:
        return []


def mp3_header_ok(raw: bytes) -> str:
    """返回 'ID3' / 'MPEG' / 'BAD'。"""
    if len(raw) >= 3 and raw[:3] == b"ID3":
        return "ID3"
    if len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0:
        return "MPEG"
    return "BAD"


def normalize_text(s: str) -> str:
    return EMOJI_RE.sub("", str(s)).replace(" ", "").strip()


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract whale-pet anim/voice assets from pet.html")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="pet.html path")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output assets dir")
    args = ap.parse_args(argv)

    src: Path = args.source.resolve()
    out: Path = args.out.resolve()
    anim_dir = out / "anim"
    voice_dir = out / "voice"
    manifest_path = out / "manifest.json"
    lines_path = out / "lines.json"

    if not src.is_file():
        print("[ERROR] source not found: %s" % src, file=sys.stderr)
        print("        download it with:", file=sys.stderr)
        print("        curl.exe -L -o \"%s\" %s" % (src, SOURCE_URL), file=sys.stderr)
        return 1
    try:
        from PIL import Image  # noqa: F401  (only needed to inspect/export frames)
    except Exception as exc:  # pragma: no cover
        print("[ERROR] Pillow is required: %s" % exc, file=sys.stderr)
        return 2

    html = src.read_text(encoding="utf-8-sig", errors="strict")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I)
    if not scripts:
        print("[ERROR] no <script> block found in %s" % src, file=sys.stderr)
        return 1
    js = scripts[-1]

    # ---- ANIM (animated webp by state) + ANIM_IDLE_STATIC
    anim_body = extract_balanced(js, "var ANIM =")
    anim_pairs = re.findall(
        r'([A-Za-z_$][\w$]*)\s*:\s*"data:image/webp;base64,([A-Za-z0-9+/=]+)"', anim_body
    )
    if not anim_pairs:
        print("[ERROR] ANIM entries not found", file=sys.stderr)
        return 1
    m_idle = re.search(
        r'var ANIM_IDLE_STATIC\s*=\s*"data:image/webp;base64,([A-Za-z0-9+/=]+)"', js
    )
    idle_static_b64 = m_idle.group(1) if m_idle else None

    # ---- AUDIO (pre-synthesized mp3 + text)
    audio_body = extract_balanced(js, "var AUDIO =")
    try:
        audio = json.loads(audio_body)
    except json.JSONDecodeError as exc:
        print("[ERROR] AUDIO JSON parse failed: %s" % exc, file=sys.stderr)
        return 1
    if not isinstance(audio, dict) or not audio:
        print("[ERROR] AUDIO is empty", file=sys.stderr)
        return 1

    # ---- LINES (raw line table)
    lines: dict[str, list[str]] = {}
    try:
        lines = parse_js_object_literal(extract_balanced(js, "var LINES ="))
    except Exception as exc:  # noqa: BLE001
        print("[WARN] LINES parse failed (%s); lines.json will contain voice_texts only" % exc)

    anim_dir.mkdir(parents=True, exist_ok=True)
    voice_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    new_files: list[str] = []  # relative to out/

    # ---- decode animations
    animations: dict[str, dict] = {}
    Image = __import__("PIL.Image", fromlist=["Image"])
    decoded_anims: list[tuple[str, bytes]] = [(name, base64.b64decode(b64)) for name, b64 in anim_pairs]
    if idle_static_b64:
        decoded_anims.append(("idle_static", base64.b64decode(idle_static_b64)))

    for state, raw in decoded_anims:
        webp_path = anim_dir / ("%s.webp" % state)
        webp_path.write_bytes(raw)
        new_files.append("anim/%s.webp" % state)

        try:
            im = Image.open(io.BytesIO(raw))
            im.load()
            frames = int(getattr(im, "n_frames", 1))
            width, height = im.size
            has_alpha = False
            if "A" in im.getbands():
                for idx in range(frames):
                    im.seek(idx)
                    lo, _hi = im.convert("RGBA").getchannel("A").getextrema()
                    if lo < 255:
                        has_alpha = True
                        break
            im.seek(0)
        except Exception as exc:  # noqa: BLE001
            print("[ERROR] cannot open %s as image: %s" % (state, exc), file=sys.stderr)
            return 1

        entry: dict = {
            "file": "anim/%s.webp" % state,
            "frames": frames,
            "size": [width, height],
            "durations_ms": webp_frame_durations(raw),
            "has_alpha": has_alpha,
            "bytes": len(raw),
        }
        if state != "idle_static":
            frame0 = im.convert("RGBA")
            png_path = anim_dir / ("%s_frame0.png" % state)
            frame0.save(png_path, format="PNG")
            new_files.append("anim/%s_frame0.png" % state)
            entry["frame0_png"] = "anim/%s_frame0.png" % state
        animations[state] = entry

    # ---- decode voices
    voices: dict[str, dict] = {}
    for vid, meta in audio.items():
        b64 = meta.get("b64") if isinstance(meta, dict) else None
        text = meta.get("text") if isinstance(meta, dict) else None
        declared = meta.get("bytes") if isinstance(meta, dict) else None
        if not b64 or not isinstance(text, str):
            warnings.append("voice %s: missing b64/text, skipped" % vid)
            continue
        raw = base64.b64decode(b64)
        mp3_path = voice_dir / ("%s.mp3" % vid)
        mp3_path.write_bytes(raw)
        new_files.append("voice/%s.mp3" % vid)
        if isinstance(declared, int) and declared != len(raw):
            warnings.append("voice %s: declared bytes %d != decoded %d" % (vid, declared, len(raw)))
        hdr = mp3_header_ok(raw)
        if hdr == "BAD":
            warnings.append("voice %s: not an ID3/MPEG header" % vid)
        voices[vid] = {"file": "voice/%s.mp3" % vid, "text": text, "bytes": len(raw)}

    # ---- stale cleanup (only files recorded in the previous manifest)
    removed: list[str] = []
    if manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            old_files: list[str] = []
            for e in (old.get("animations") or {}).values():
                if isinstance(e, dict):
                    old_files.append(e.get("file"))
                    old_files.append(e.get("frame0_png"))
            for e in (old.get("voices") or {}).values():
                if isinstance(e, dict):
                    old_files.append(e.get("file"))
            for rel in old_files:
                if not rel or rel in new_files:
                    continue
                p = (out / rel).resolve()
                if p.is_file() and out in p.parents:
                    p.unlink()
                    removed.append(rel)
        except Exception as exc:  # noqa: BLE001
            warnings.append("stale-cleanup skipped: %s" % exc)

    # ---- manifest.json (schema requested by the app)
    manifest = {
        "source": {
            "project": SOURCE_PROJECT,
            "url": SOURCE_URL,
            "license": SOURCE_LICENSE,
            "fetched": SOURCE_FETCHED,
        },
        "animations": animations,
        "voices": voices,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    # ---- lines.json (raw LINES table + voice id -> text)
    line_slots = sum(len(v) for v in lines.values())
    lines_doc = {
        "source": "pet/assets/source/pet.html",
        "note": "从 pet.html 的 var LINES 原样提取；{done}/{total}/{left} 为运行时代入占位符。",
        "lines": lines,
        "voice_texts": {vid: meta["text"] for vid, meta in voices.items()},
    }
    lines_path.write_text(
        json.dumps(lines_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    # ---- text coverage cross-check (audio clip text vs LINES table)
    exact = 0
    slot_total = 0
    for vid, meta in voices.items():
        m = re.fullmatch(r"([A-Za-z_]+?)(\d+)", vid)
        if not m or m.group(1) not in lines:
            continue
        idx = int(m.group(2))
        if idx >= len(lines[m.group(1)]):
            continue
        slot_total += 1
        if normalize_text(lines[m.group(1)][idx]) == normalize_text(meta["text"]):
            exact += 1

    print("source : %s" % src)
    print("source size: %d bytes" % src.stat().st_size)
    print("out    : %s" % out)
    print("animations: %d  (%s)" % (len(animations), ", ".join(animations)))
    for state, e in animations.items():
        print(
            "  %-12s frames=%d size=%dx%d alpha=%s bytes=%d durations=%s"
            % (
                state,
                e["frames"],
                e["size"][0],
                e["size"][1],
                "yes" if e["has_alpha"] else "no",
                e["bytes"],
                e["durations_ms"] if e["durations_ms"] else "[]",
            )
        )
    print("voices : %d mp3, %d bytes" % (len(voices), sum(v["bytes"] for v in voices.values())))
    print("lines  : %d categories, %d strings" % (len(lines), line_slots))
    print("audio-text/line-slot match: %d/%d" % (exact, slot_total))
    print("wrote  : pet/assets/manifest.json, pet/assets/lines.json")
    if removed:
        print("removed stale: %s" % ", ".join(removed))
    for w in warnings:
        print("[WARN] %s" % w)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
