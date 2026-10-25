# -*- coding: utf-8 -*-
"""verify_assets.py -- 校验 pet/assets/ 下的提取产物（Pillow 打开每个 WebP、检查 MP3 头）。

校验内容:
  * manifest.json 结构与必填字段（source / animations / voices）
  * 每个动画 WebP: 能用 Pillow 打开，打印帧数、尺寸、透明像素、字节数；
    并从 RIFF/ANMF 块独立解析逐帧时长，与 manifest 对照
  * 每个 <state>_frame0.png: 存在、RGBA、尺寸与动画一致
  * 每个语音 MP3: 存在、字节数、文件头（ID3 或 0xFF 0xEx 帧同步）
  * lines.json: 台词表分类/条数、voice_texts 与 manifest.voices 的 id 一致
  * 语音文本 vs LINES 槽位文本的一致性（含 {done}/{left} 占位符的条目仅提示）

用法:
  python tools/verify_assets.py                     # 默认 pet/assets
  python tools/verify_assets.py --assets DIR

退出码: 0 = 全部通过; 1 = 存在失败项。
"""

from __future__ import annotations

import argparse
import io
import json
import re
import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_ASSETS = REPO_ROOT / "pet" / "assets"

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF\u2190-\u21FF\u200D\u20E3]"
)


def normalize(s: str) -> str:
    return EMOJI_RE.sub("", str(s)).replace(" ", "").strip()


def webp_frame_durations(raw: bytes) -> list[int]:
    """独立实现：解析 RIFF 容器 ANMF 块的逐帧时长（毫秒）。"""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        raise ValueError("not RIFF/WEBP")
    pos, end = 12, min(8 + struct.unpack("<I", raw[4:8])[0], len(raw))
    durs: list[int] = []
    while pos + 8 <= end:
        tag = raw[pos : pos + 4]
        size = struct.unpack("<I", raw[pos + 4 : pos + 8])[0]
        payload = raw[pos + 8 : pos + 8 + size]
        if tag == b"ANMF" and len(payload) >= 16:
            durs.append(int.from_bytes(payload[12:15], "little"))
        pos += 8 + size + (size & 1)
    return durs


def mp3_header(raw: bytes) -> str:
    if len(raw) >= 3 and raw[:3] == b"ID3":
        return "ID3"
    if len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0:
        return "MPEG"
    return "BAD"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify extracted whale-pet assets")
    ap.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    assets: Path = args.assets.resolve()
    manifest_path = assets / "manifest.json"
    lines_path = assets / "lines.json"
    failures: list[str] = []
    infos: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)
        print("[FAIL] " + msg)

    def ok(msg: str) -> None:
        print("[ OK ] " + msg)

    print("========== 鲸鱼娘素材验证 (verify_assets.py) ==========")
    print("assets dir : %s" % assets)
    print("python     : %s" % sys.version.split()[0])
    try:
        import PIL
        from PIL import Image

        print("Pillow     : %s" % PIL.__version__)
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] Pillow 不可用: %s" % exc)
        return 1

    if not manifest_path.is_file():
        print("[FAIL] manifest.json 不存在: %s" % manifest_path)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    src = manifest.get("source") or {}
    print("manifest   : %s" % manifest_path)
    print(
        "source     : project=%s | fetched=%s | license=%s"
        % (src.get("project"), src.get("fetched"), src.get("license"))
    )
    for key in ("project", "url", "license", "fetched"):
        if not src.get(key):
            fail("manifest.source.%s 缺失" % key)

    animations = manifest.get("animations") or {}
    voices = manifest.get("voices") or {}

    # ---------- animations ----------
    print("\n---------- animations: %d ----------" % len(animations))
    for state, e in animations.items():
        if not isinstance(e, dict):
            fail("%s: manifest 条目不是对象" % state)
            continue
        rel = e.get("file")
        problems = []
        for key in ("file", "frames", "size", "durations_ms", "has_alpha", "bytes"):
            if key not in e:
                problems.append("manifest 缺少字段 %s" % key)
        path = assets / str(rel)
        if not path.is_file():
            fail("%s: 文件不存在 %s" % (state, path))
            continue
        raw = path.read_bytes()
        png_bytes = 0
        try:
            im = Image.open(io.BytesIO(raw))
            im.load()
            frames = int(getattr(im, "n_frames", 1))
            width, height = im.size
            im.seek(0)
            hist = im.convert("RGBA").getchannel("A").histogram()
            transparent_px = sum(hist[:255])  # 透明通道 < 255 的像素数（Pillow 12+ 无弃用告警）
            has_alpha = transparent_px > 0
            durations = webp_frame_durations(raw)
        except Exception as exc:  # noqa: BLE001
            fail("%s: Pillow 打开失败: %s" % (state, exc))
            continue

        if frames != e.get("frames"):
            problems.append("帧数 %d != manifest %s" % (frames, e.get("frames")))
        if [width, height] != list(e.get("size") or []):
            problems.append("尺寸 %dx%d != manifest %s" % (width, height, e.get("size")))
        if bool(has_alpha) != bool(e.get("has_alpha")):
            problems.append("透明=%s != manifest %s" % (has_alpha, e.get("has_alpha")))
        if len(raw) != e.get("bytes"):
            problems.append("字节数 %d != manifest %s" % (len(raw), e.get("bytes")))
        if durations != list(e.get("durations_ms") or []):
            problems.append("帧时长 %s != manifest %s" % (durations, e.get("durations_ms")))

        png_rel = e.get("frame0_png")
        is_static = state == "idle_static"
        if not is_static:
            if not png_rel:
                problems.append("缺少 frame0_png 字段")
            else:
                png_path = assets / str(png_rel)
                if not png_path.is_file():
                    problems.append("frame0 PNG 不存在: %s" % png_rel)
                else:
                    try:
                        pim = Image.open(png_path)
                        pim.load()
                        png_bytes = png_path.stat().st_size
                        if pim.size != (width, height):
                            problems.append("PNG 尺寸 %s != %dx%d" % (pim.size, width, height))
                        if pim.mode != "RGBA":
                            problems.append("PNG mode=%s (期望 RGBA)" % pim.mode)
                    except Exception as exc:  # noqa: BLE001
                        problems.append("PNG 打开失败: %s" % exc)

        line = (
            "%s frames=%d/%s size=%dx%d alpha=%s transparent_px=%d/%d bytes=%d durations=%s"
            % (
                rel,
                frames,
                e.get("frames"),
                width,
                height,
                "yes" if has_alpha else "no",
                transparent_px,
                width * height,
                len(raw),
                ("%d/%d" % (len(durations), frames)) if durations else "[]",
            )
        )
        if png_rel:
            line += " png=%s(%dB)" % (png_rel, png_bytes)
        if problems:
            fail(line + "  -> " + "; ".join(problems))
        else:
            ok(line)

    # ---------- voices ----------
    print("\n---------- voices: %d ----------" % len(voices))
    total_bytes = 0
    for vid, e in voices.items():
        if not isinstance(e, dict):
            fail("%s: manifest 条目不是对象" % vid)
            continue
        rel = e.get("file")
        path = assets / str(rel)
        if not path.is_file():
            fail("%s: 文件不存在 %s" % (vid, path))
            continue
        raw = path.read_bytes()
        total_bytes += len(raw)
        hdr = mp3_header(raw)
        problems = []
        if len(raw) != e.get("bytes"):
            problems.append("字节数 %d != manifest %s" % (len(raw), e.get("bytes")))
        if hdr == "BAD":
            problems.append("文件头不是 ID3 / 0xFFEx")
        if len(raw) < 1024:
            problems.append("文件过小 (%d B)" % len(raw))
        if not isinstance(e.get("text"), str) or not e.get("text").strip():
            problems.append("text 为空")
        line = "%s bytes=%d header=%s text=%r" % (rel, len(raw), hdr, e.get("text"))
        if problems:
            fail(line + "  -> " + "; ".join(problems))
        else:
            ok(line)
    print("voices total bytes: %d" % total_bytes)

    # ---------- extra mp3 files (not declared in manifest, e.g. edge-tts test) ----------
    declared = {(assets / str(e.get("file"))).resolve() for e in voices.values() if isinstance(e, dict)}
    extras = sorted(p for p in (assets / "voice").glob("*.mp3") if p.resolve() not in declared)
    if extras:
        print("\n---------- extra mp3 (not in manifest): %d ----------" % len(extras))
        for p in extras:
            raw = p.read_bytes()
            hdr = mp3_header(raw)
            problems = []
            if hdr == "BAD":
                problems.append("文件头不是 ID3 / 0xFFEx")
            if len(raw) < 1024:
                problems.append("文件过小 (%d B)" % len(raw))
            line = "%s bytes=%d header=%s" % (p.relative_to(assets).as_posix(), len(raw), hdr)
            if problems:
                fail(line + "  -> " + "; ".join(problems))
            else:
                ok(line)

    # ---------- lines.json ----------
    print("\n---------- lines.json ----------")
    if not lines_path.is_file():
        fail("lines.json 不存在")
    else:
        doc = json.loads(lines_path.read_text(encoding="utf-8"))
        lines = doc.get("lines") or {}
        total = sum(len(v) for v in lines.values())
        cats = ", ".join("%s=%d" % (k, len(v)) for k, v in lines.items())
        if lines:
            ok("lines.json: %d categories (%s), %d strings" % (len(lines), cats, total))
        else:
            fail("lines.json 的 lines 为空")
        vt = doc.get("voice_texts") or {}
        if set(vt) != set(voices):
            missing = sorted(set(voices) - set(vt))
            extra = sorted(set(vt) - set(voices))
            fail("voice_texts 与 manifest.voices id 不一致 (缺 %s / 多 %s)" % (missing, extra))
        else:
            ok("voice_texts ids == manifest voices ids (%d)" % len(vt))
        exact = compared = 0
        soft: list[str] = []
        for vid in voices:
            m = re.fullmatch(r"([A-Za-z_]+?)(\d+)", vid)
            if not m or m.group(1) not in lines:
                continue
            idx = int(m.group(2))
            if idx >= len(lines[m.group(1)]):
                continue
            line_text = lines[m.group(1)][idx]
            if "{" in line_text:
                soft.append(vid)
                continue
            compared += 1
            if normalize(line_text) == normalize(voices[vid].get("text", "")):
                exact += 1
            else:
                infos.append("%s: 与 LINES 槽位文本不同" % vid)
        ok(
            "audio-text vs LINES slots: %d/%d exact (placeholder slots skipped: %s)"
            % (exact, compared, ", ".join(soft) if soft else "-")
        )

    for msg in infos:
        print("[INFO] " + msg)

    # ---------- summary ----------
    print("\n========== SUMMARY ==========")
    n_anim = len(animations)
    n_png = sum(1 for e in animations.values() if isinstance(e, dict) and e.get("frame0_png"))
    n_voice = len(voices)
    print("animations: %d webp + %d frame0 png | voices: %d mp3" % (n_anim, n_png, n_voice))
    if failures:
        print("RESULT: FAIL  (%d failure(s))" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print(
        "RESULT: PASS  (%d/%d webp, %d/%d png, %d/%d mp3, manifest/lines 一致)"
        % (n_anim, n_anim, n_png, n_png, n_voice, n_voice)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
