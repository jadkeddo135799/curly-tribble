import re
import urllib.parse

from agent_reach.core import Arg, Channel, ReachError, get_json, html_to_text, limit_arg, register

ALGOLIA = "https://hn.algolia.com/api/v1"

ch = register(Channel(
    name="hackernews",
    title="Hacker News",
    description="Search stories, list the front page, read a story with its comment tree.",
    domains=["news.ycombinator.com"],
))


def _hit(h):
    oid = h["objectID"]
    return {
        "id": int(oid),
        "title": h.get("title"),
        "points": h.get("points"),
        "comments": h.get("num_comments"),
        "author": h.get("author"),
        "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
        "discussion": f"https://news.ycombinator.com/item?id={oid}",
    }


@ch.action("search", "search stories", [Arg("query", "keywords"), limit_arg()])
def search(query, limit=10):
    q = urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": limit})
    return [_hit(h) for h in get_json(f"{ALGOLIA}/search?{q}")["hits"]]


@ch.action("top", "current front page", [limit_arg()])
def top(limit=10):
    return [_hit(h) for h in get_json(f"{ALGOLIA}/search?tags=front_page&hitsPerPage={limit}")["hits"]]


def parse_item_id(ref: str) -> int:
    m = re.search(r"[?&]id=(\d+)", ref) or re.fullmatch(r"\s*(\d+)\s*", ref)
    if not m:
        raise ReachError(f"cannot find a Hacker News item id in: {ref}")
    return int(m.group(1))


def flatten_comments(node, depth=0, out=None, limit=100):
    out = [] if out is None else out
    for c in node.get("children") or []:
        if len(out) >= limit:
            break
        if c.get("text"):
            out.append({"depth": depth, "author": c.get("author"), "text": html_to_text(c["text"])})
        flatten_comments(c, depth + 1, out, limit)
    return out


@ch.action("read", "a story/comment and its thread", [Arg("url", "item URL or id"), limit_arg(100)])
def read(url, limit=100):
    item = get_json(f"{ALGOLIA}/items/{parse_item_id(url)}")
    return {
        "id": item["id"],
        "title": item.get("title"),
        "author": item.get("author"),
        "points": item.get("points"),
        "url": item.get("url"),
        "text": html_to_text(item["text"]) if item.get("text") else None,
        "comment_list": flatten_comments(item, limit=limit),
    }
