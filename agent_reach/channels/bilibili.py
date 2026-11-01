import html
import re
import urllib.parse

from agent_reach.core import Arg, Channel, ReachError, env, get_json, limit_arg, register

API = "https://api.bilibili.com"

ch = register(Channel(
    name="bilibili",
    title="哔哩哔哩 / Bilibili",
    description="Search videos and read video details (title, uploader, stats, description).",
    domains=["bilibili.com", "b23.tv"],
    requires="BILIBILI_COOKIE: your own logged-in browser cookie; without it Bilibili usually answers HTTP 412",
))


def _headers():
    h = {"Referer": "https://www.bilibili.com/"}
    cookie = env("BILIBILI_COOKIE")
    if cookie:
        h["Cookie"] = cookie
    return h


def _call(path):
    try:
        data = get_json(API + path, _headers())
    except ReachError as e:
        if "HTTP 412" in str(e) and not env("BILIBILI_COOKIE"):
            raise ReachError(f"{e}. Set BILIBILI_COOKIE to the Cookie header from your logged-in browser session.") from None
        raise
    if data.get("code") != 0:
        raise ReachError(f"Bilibili API error {data.get('code')}: {data.get('message')}")
    return data["data"]


def _clean(s):
    # search results wrap matches in <em class="keyword">
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


@ch.action("search", "search videos", [Arg("keyword", "search keyword"), limit_arg()])
def search(keyword, limit=10):
    q = urllib.parse.urlencode({"search_type": "video", "keyword": keyword})
    results = _call(f"/x/web-interface/search/type?{q}").get("result") or []
    return [
        {
            "bvid": v["bvid"],
            "title": _clean(v.get("title")),
            "author": v.get("author"),
            "plays": v.get("play"),
            "duration": v.get("duration"),
            "url": f"https://www.bilibili.com/video/{v['bvid']}",
        }
        for v in results[:limit]
    ]


def parse_bvid(ref: str) -> str:
    m = re.search(r"(BV[0-9A-Za-z]{10})", ref)
    if m:
        return m.group(1)
    if "b23.tv" in ref:
        raise ReachError("b23.tv short links aren't expanded yet; open it once and pass the full bilibili.com/video/BV... URL")
    raise ReachError(f"cannot find a BV id in: {ref}")


@ch.action("read", "video details", [Arg("url", "video URL or BV id")])
def read(url):
    v = _call(f"/x/web-interface/view?bvid={parse_bvid(url)}")
    s = v.get("stat", {})
    return {
        "bvid": v["bvid"],
        "title": v["title"],
        "author": v["owner"]["name"],
        "duration_sec": v.get("duration"),
        "views": s.get("view"),
        "likes": s.get("like"),
        "danmaku": s.get("danmaku"),
        "url": f"https://www.bilibili.com/video/{v['bvid']}",
        "description": v.get("desc"),
    }
