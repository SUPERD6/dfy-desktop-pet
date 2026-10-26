# -*- coding: utf-8 -*-
"""她的公开上网入口：只读公开 HTTP(S)，拒绝本机/内网地址。

给 Agent 的 web_search / web_fetch 用；不做登录、不提交表单、不读本地文件。
"""
from __future__ import annotations

import html
import ipaddress
import re
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MingyuePet/1.0"
MAX_BYTES = 900_000
MAX_CHARS = 12_000


def _host_allowed(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    if not host or host in ("localhost", "0.0.0.0"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global
    except ValueError:
        pass
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    return True


def validate_url(url: str) -> str:
    text = str(url or "").strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只允许 http/https 公开网页")
    if not parsed.hostname or not _host_allowed(parsed.hostname):
        raise ValueError("不允许访问本机或内网地址")
    return text


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def fetch_url(url: str, timeout: float = 12.0, max_chars: int = MAX_CHARS) -> dict:
    """读取公开网页并返回标题/正文摘要；失败抛 ValueError（不返回伪内容）。"""
    target = validate_url(url)
    request = urllib.request.Request(target, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES).decode("utf-8", "replace")
    title = ""
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    if match:
        title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()[:200]
    text = _strip_html(raw)[:max_chars]
    return {"url": target, "title": title, "text": text}


def search_web(query: str, limit: int = 5, timeout: float = 12.0) -> list[dict]:
    """DuckDuckGo HTML 搜索，返回 [{title,url,snippet}]；失败返回空列表。"""
    query = str(query or "").strip()
    if not query:
        return []
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query[:300])
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES).decode("utf-8", "replace")
    except Exception:
        return []
    results: list[dict] = []
    for match in re.finditer(r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                             r'(?:class="result__snippet"[^>]*>(.*?)</a>)?', raw):
        link = html.unescape(match.group(1))
        title = _strip_html(match.group(2))
        snippet = _strip_html(match.group(3) or "")
        if link.startswith("//duckduckgo.com/l/?uddg="):
            try:
                link = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse("https:" + link).query)["uddg"][0])
            except Exception:
                continue
        if title and link.startswith(("http://", "https://")):
            results.append({"title": title[:200], "url": link, "snippet": snippet[:300]})
        if len(results) >= limit:
            break
    return results
