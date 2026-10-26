# -*- coding: utf-8 -*-
"""iTunes / Apple Music（Windows iTunes）控制。

通过 iTunes COM 控制已安装的 iTunes；没有安装/未授权时返回错误，不假装成功。
只控制播放，不读取或上传音乐库。
"""
from __future__ import annotations

import pythoncom
import win32com.client


def _with_app(fn):
    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.Dispatch("iTunes.Application")
        return fn(app)
    finally:
        try:
            if app is not None:
                del app
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def play_pause() -> str:
    def action(app):
        app.PlayPause()
        return "iTunes 播放/暂停"
    return _with_app(action)


def previous() -> str:
    def action(app):
        app.PreviousTrack()
        return "iTunes 上一首"
    return _with_app(action)


def next_track() -> str:
    def action(app):
        app.NextTrack()
        return "iTunes 下一首"
    return _with_app(action)


def stop() -> str:
    def action(app):
        app.Stop()
        return "iTunes 停止"
    return _with_app(action)


def play_first() -> str:
    def action(app):
        app.LibraryPlaylist.PlayFirstTrack()
        return "iTunes 从音乐库第一首开始播放"
    return _with_app(action)


def search_play(query: str) -> str:
    query = str(query or "").strip()
    if not query:
        raise ValueError("搜索词不能为空")

    def action(app):
        result = app.LibraryPlaylist.Search(query, 1)
        try:
            count = int(result.Count)
        except Exception:
            count = 0
        if count <= 0:
            raise ValueError(f"iTunes 音乐库里没找到「{query}」")
        track = result.Item(1)
        title = str(getattr(track, "Name", "") or "")
        artist = str(getattr(track, "Artist", "") or "")
        track.Play()
        return f"正在播放：{title}" + (f" — {artist}" if artist else "")

    return _with_app(action)


def now_playing() -> str:
    def action(app):
        state = int(getattr(app, "PlayerState", 0) or 0)
        name = ""
        try:
            name = str(getattr(app.CurrentTrack, "Name", "") or "")
        except Exception:
            name = ""
        return f"{name or '没有正在播放的曲目'}" + ("" if state else "（iTunes 已停止）")

    return _with_app(action)


def current_track_path() -> str:
    """当前 iTunes 曲目的本地文件路径；没有则空串。"""
    def action(app):
        try:
            return str(getattr(app.CurrentTrack, "Location", "") or "")
        except Exception:
            return ""

    return _with_app(action)
