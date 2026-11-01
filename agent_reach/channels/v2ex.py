import re
import urllib.parse

from agent_reach.core import Arg, Channel, ReachError, get_json, limit_arg, register

API = "https://www.v2ex.com/api"

ch = register(Channel(
    name="v2ex",
    title="V2EX",
    description="Hot/latest topics, node listings, and full topic threads with replies.",
    domains=["v2ex.com"],
))


def _topic_brief(t):
    return {
        "id": t["id"],
        "title": t["title"],
        "node": (t.get("node") or {}).get("name"),
        "author": (t.get("member") or {}).get("username"),
        "replies": t.get("replies"),
        "url": t.get("url") or f"https://www.v2ex.com/t/{t['id']}",
    }


def parse_topic_id(ref: str) -> int:
    m = re.search(r"/t/(\d+)", ref) or re.fullmatch(r"\s*(\d+)\s*", ref)
    if not m:
        raise ReachError(f"cannot find a V2EX topic id in: {ref}")
    return int(m.group(1))


@ch.action("hot", "today's hot topics", [limit_arg()])
def hot(limit=10):
    return [_topic_brief(t) for t in get_json(f"{API}/topics/hot.json")[:limit]]


@ch.action("latest", "newest topics", [limit_arg()])
def latest(limit=10):
    return [_topic_brief(t) for t in get_json(f"{API}/topics/latest.json")[:limit]]


@ch.action("node", "topics in a node, e.g. `python`", [Arg("name", "node name"), limit_arg()])
def node(name, limit=10):
    return [_topic_brief(t) for t in get_json(f"{API}/topics/show.json?node_name={urllib.parse.quote(name)}")[:limit]]


@ch.action("read", "a topic and its replies", [Arg("url", "topic URL or id"), limit_arg(50)])
def read(url, limit=50):
    tid = parse_topic_id(url)
    topics = get_json(f"{API}/topics/show.json?id={tid}")
    if not topics:
        raise ReachError(f"V2EX topic {tid} not found (deleted, or hidden from the public API)")
    out = _topic_brief(topics[0])
    out["content"] = topics[0].get("content", "")
    replies = get_json(f"{API}/replies/show.json?topic_id={tid}")
    out["reply_list"] = [
        {"floor": i + 1, "author": (r.get("member") or {}).get("username"), "content": r.get("content", "")}
        for i, r in enumerate(replies[:limit])
    ]
    return out
