# -*- coding: utf-8 -*-
"""桌宠配置的默认值（app-window-1，2026-10-28 从 app.py 的 112 个 setdefault 原样搬来）。

- apply_defaults：把缺的键补上默认值（读配置后调用一次），逻辑与原来逐行相同；
- save_config：只把和默认值不同的键写回 config.json——以前每次保存都把全部默认值写进被 Git 跟踪的文件，
  代码里改默认值也到不了老用户那里；
- tests/fixtures/config_defaults_snapshot.json 是改动前用空配置跑旧代码得到的结果，测试保证一模一样。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def apply_defaults(config: dict, project_root: Path, foods) -> dict:
    """在 config 上就地补齐默认值并返回它。"""
    config = config if isinstance(config, dict) else {}
    config.setdefault("llm", {})
    config.setdefault("voice", "zh-CN-XiaoyiNeural")
    config.setdefault("voice_rate", "-4%")
    config.setdefault("voice_pitch", "+6Hz")
    config.setdefault("greet_on_start", True)
    config.setdefault("always_on_top", True)
    config.setdefault("mute", False)
    config.setdefault("volume", 800)
    proactive = config.setdefault("proactive", {})
    if not isinstance(proactive, dict):
        proactive = {}
        config["proactive"] = proactive
    proactive.setdefault("enabled", True)
    proactive.setdefault("min_idle_minutes", 25)
    proactive.setdefault("min_interval_minutes", 25)
    proactive.setdefault("daily_limit", 8)
    feed_counts = config.setdefault("feed_counts", {})
    if not isinstance(feed_counts, dict):
        feed_counts = {}
        config["feed_counts"] = feed_counts
    for food in foods:
        feed_counts.setdefault(food, 0)
    config.setdefault("inner_voice_chance", 1 / 6)
    config.setdefault("sticker_chance", 0.3)  # 聊天回复后顺手甩个表情包的概率
    dsh_status = config.setdefault("dsh_status", {})
    if not isinstance(dsh_status, dict):
        dsh_status = {}
        config["dsh_status"] = dsh_status
    dsh_status.setdefault("enabled", True)
    dsh_status.setdefault("path", "~/.dsh/whale-bridge/status.json")
    dsh_status.setdefault("poll_ms", 1000)
    personal = config.setdefault("personal", {})
    if not isinstance(personal, dict):
        personal = {}
        config["personal"] = personal
    personal.setdefault("enabled", False)
    personal.setdefault("library_path", "")
    personal.setdefault("max_chars", 1600)
    overview = config.setdefault("overview", {})
    if not isinstance(overview, dict):
        overview = {}
        config["overview"] = overview
    overview.setdefault("enabled", True)
    overview.setdefault("min_interval_minutes", 10)
    overview.setdefault("refresh_minutes", 15)
    overview.setdefault("roots", [])
    agent = config.setdefault("agent", {})
    if not isinstance(agent, dict):
        agent = {}
        config["agent"] = agent
    agent.setdefault("enabled", True)
    agent.setdefault("max_steps", 6)
    agent.setdefault("allowed_roots", [str(project_root)])
    config.setdefault("quick_chat", False)
    config.setdefault("chat_open_on_start", False)
    config.setdefault("autonomy_enabled", True)
    # 自主进化默认关：要等 worktree 隔离 + 测试闸门做好再开（2026-10-27 安全闸）
    config.setdefault("self_improve_enabled", False)
    config.setdefault("self_improve_autonomous", False)
    config.setdefault("self_improve_model_enabled", False)
    config.setdefault("self_improve_home_enabled", False)
    config.setdefault("self_improve_interval_min", 30)
    config.setdefault("self_improve_home_interval_min", 60)
    config.setdefault("self_improve_estimate_tokens", 0)
    config.setdefault("autonomy_daily_limit", 0)
    config.setdefault("autonomy_model", "deepseek-v4-pro")
    config.setdefault("autonomy_max_steps", 24)
    config.setdefault("position_awareness", True)
    config.setdefault("ambient_screen_enabled", False)  # 截图会发给模型：默认不看，主人打开才看
    config.setdefault("ambient_screen_interval", 300)
    config.setdefault("roam_enabled", False)
    config.setdefault("click_sound", True)
    config.setdefault("click_sound_style", "duck")
    config.setdefault("personality_reactions", True)
    config.setdefault("tray_enabled", True)
    config.setdefault("autostart", False)
    config.setdefault("click_through", False)
    config.setdefault("lock_position", False)
    config.setdefault("shift_drag_only", False)
    config.setdefault("window_opacity", 1.0)
    config.setdefault("hotkeys_enabled", True)
    config.setdefault("free_placement", True)
    vision_cfg = config.setdefault("vision", {})
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
        config["vision"] = vision_cfg
    vision_cfg.setdefault("enabled", False)
    vision_cfg.setdefault("base_url", "")
    vision_cfg.setdefault("model", "")
    vision_cfg.setdefault("timeout_s", 60)
    vision_cfg.setdefault("max_tokens", 400)
    video_cfg = config.setdefault("video_gen", {})
    if not isinstance(video_cfg, dict):
        video_cfg = {}
        config["video_gen"] = video_cfg
    video_cfg.setdefault("model", "doubao-seedance-1-5-pro-251215")
    video_cfg.setdefault("ratio", "16:9")
    video_cfg.setdefault("duration", 5)
    video_cfg.setdefault("resolution", "720p")
    video_cfg.setdefault("generate_audio", True)
    config.setdefault("voice_backend", "qwen3")  # 本机 Qwen3-TTS；服务不在时自动退回 edge
    qwen_cfg = config.setdefault("qwen_tts", {})
    if isinstance(qwen_cfg, dict):  # 2026-10-28 主人选定：默认 v2 原版（角色卡声线）；菜单可切 v2 + 30% Seed-VC
        qwen_cfg.setdefault("voice", "mingyue-v2")
        qwen_cfg.setdefault("mix_ref", "docs/seedvc-sample.wav")
        qwen_cfg.setdefault("mix_weight", 0.0)
    gs_cfg = config.setdefault("gptsovits", {})
    if not isinstance(gs_cfg, dict):
        gs_cfg = {}
        config["gptsovits"] = gs_cfg
    gs_cfg.setdefault("base_url", "http://127.0.0.1:9880")
    gs_cfg.setdefault("ref_audio_path", "cache/gptsovits/ref.wav")
    gs_cfg.setdefault("prompt_text", "本鲸叫小鲸鱼，最喜欢白饭和主人的摸头。")
    gs_cfg.setdefault("prompt_lang", "zh")
    gs_cfg.setdefault("text_lang", "zh")
    gs_cfg.setdefault("text_split_method", "cut5")
    config.setdefault("voice_auto_send", False)
    config.setdefault("asr_backend", "sensevoice")  # 本机 SenseVoice（带语气）；不在就用本机 Whisper
    config.setdefault("barge_in_enabled", True)
    config.setdefault("click_jump", True)
    config.setdefault("double_click_chat", False)
    config.setdefault("visual_memory", True)
    config.setdefault("visual_memory_interval", 300)
    agent_cfg = config.setdefault("agent", {})
    if isinstance(agent_cfg, dict):
        # 全权限托管只在本次运行内有效（会话级，30 分钟自动关），绝不写进被 Git 跟踪的 config.json
        agent_cfg.pop("auto_approve", None)
        agent_cfg.setdefault("auto_approve_own_home", True)  # 只自动放行 pet/home
    wake_cfg = config.setdefault("wake", {})
    if not isinstance(wake_cfg, dict):
        wake_cfg = {}
        config["wake"] = wake_cfg
    wake_cfg.setdefault("enabled", False)
    wake_cfg.setdefault("phrases", ["大肥鱼", "肥鱼", "小肥鱼"])
    wake_cfg.setdefault("language", "zh-CN")
    server_cfg = config.setdefault("server", {})
    if not isinstance(server_cfg, dict):
        server_cfg = {}
        config["server"] = server_cfg
    server_cfg.setdefault("enabled", True)
    server_cfg.setdefault("port", 8765)
    server_cfg.setdefault("token", "")
    qq_cfg = config.setdefault("qq", {})
    if not isinstance(qq_cfg, dict):
        qq_cfg = {}
        config["qq"] = qq_cfg
    qq_cfg.setdefault("enabled", False)
    qq_cfg.setdefault("base_url", "http://127.0.0.1:3000")
    qq_cfg.setdefault("token", "")
    qq_cfg.setdefault("private_user", "")
    qq_cfg.setdefault("group_id", "")
    qq_cfg.setdefault("whitelist", [])
    qq_cfg.setdefault("poll_seconds", 4)
    wechat_cfg = config.setdefault("wechat", {})
    if not isinstance(wechat_cfg, dict):
        wechat_cfg = {}
        config["wechat"] = wechat_cfg
    wechat_cfg.setdefault("enabled", False)
    wechat_cfg.setdefault("port", 8800)
    wechat_cfg.setdefault("token", "")
    proactive = config.get("proactive")
    if isinstance(proactive, dict):
        proactive.setdefault("quiet_hours", [1, 8])
    return config


def default_config(project_root: Path, foods) -> dict:
    return apply_defaults({}, project_root, foods)


def diff_from_defaults(config, defaults):
    """config 里和 defaults 不一样的部分（字典逐层比，其他类型整体比）。"""
    if not isinstance(config, dict) or not isinstance(defaults, dict):
        return config
    out = {}
    for key, value in config.items():
        if key not in defaults:
            out[key] = value
        elif isinstance(value, dict) and isinstance(defaults[key], dict):
            sub = diff_from_defaults(value, defaults[key])
            if sub:
                out[key] = sub
        elif value != defaults[key]:
            out[key] = value
    return out


def save_config(path: Path, config: dict, defaults: dict) -> None:
    """只写主人改过的值；原子替换。第一次瘦身前给旧文件留一份 .bak-full。"""
    path = Path(path)
    data = diff_from_defaults(config, defaults)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    backup = path.with_name(path.name + ".bak-full")
    if path.exists() and not backup.exists():
        try:
            backup.write_bytes(path.read_bytes())
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
