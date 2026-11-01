"""agent-reach command line.

    agent-reach channels                          # 有哪些渠道、每个渠道能做什么
    agent-reach bilibili search "大模型"           # agent-reach <渠道> <动作> [参数]
    agent-reach read https://www.v2ex.com/t/123   # 按链接自动选渠道
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

import agent_reach.channels  # noqa: F401  (registers channels)
from agent_reach import __version__
from agent_reach.core import REGISTRY, Action, ReachError, route

LONG_FIELDS = ("content", "readme", "body", "text", "description")


# ---------------------------------------------------------------- output


def _scalar_lines(d: dict, indent: str = "") -> List[str]:
    return [f"{indent}{k}: {v}" for k, v in d.items()
            if v not in (None, "") and k not in LONG_FIELDS and not isinstance(v, (list, dict))]


def render_text(data: Any) -> str:
    if isinstance(data, list):
        if not data:
            return "(no results)"
        blocks = []
        for i, item in enumerate(data, 1):
            if isinstance(item, dict):
                lines = _scalar_lines(item, "   ")
                head = lines.pop(0).strip() if lines else ""
                blocks.append(f"{i}. {head}\n" + "\n".join(lines))
            else:
                blocks.append(f"{i}. {item}")
        return "\n\n".join(b.rstrip() for b in blocks)
    if isinstance(data, dict):
        out = _scalar_lines(data)
        for k in LONG_FIELDS:
            if data.get(k):
                out += ["", f"--- {k} ---", str(data[k]).strip()]
        for k, v in data.items():
            if isinstance(v, list):
                out += ["", f"--- {k} ({len(v)}) ---"]
                for item in v:
                    if isinstance(item, dict):
                        pad = "  " * int(item.get("depth", 0))
                        who = item.get("author") or "?"
                        tag = f"#{item['floor']} " if "floor" in item else ""
                        txt = item.get("content") or item.get("text") or item.get("body") or ""
                        out.append(f"{pad}{tag}[{who}] {str(txt).strip()}")
                    else:
                        out.append(f"- {item}")
        return "\n".join(out).strip()
    return str(data)


def emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_text(data))


def channels_listing() -> list:
    return [
        {
            "channel": ch.name,
            "title": ch.title,
            "description": ch.description,
            "domains": ch.domains,
            "requires": ch.requires,
            "actions": [
                {"action": a.name, "help": a.help,
                 "usage": f"agent-reach {ch.name} {a.name} " + " ".join(_usage_arg(x) for x in a.args)}
                for a in ch.actions.values()
            ],
        }
        for ch in REGISTRY.values()
    ]


def _usage_arg(a) -> str:
    return f"<{a.name}>" if a.positional else f"[--{a.name} {a.default}]"


def render_channels(listing: list) -> str:
    out = []
    for ch in listing:
        out.append(f"{ch['channel']}  —  {ch['title']}")
        out.append(f"  {ch['description']}")
        if ch["domains"]:
            out.append(f"  domains: {', '.join(ch['domains'])}")
        if ch["requires"]:
            out.append(f"  needs:   {ch['requires']}")
        for a in ch["actions"]:
            out.append(f"    {a['usage'].strip():<58} {a['help']}")
        out.append("")
    out.append("Any URL:  agent-reach read <url>   (routes by domain; unknown sites use `web`)")
    return "\n".join(out)


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS on sub-parsers so `agent-reach --json x y` isn't reset to False by them
    top = argparse.ArgumentParser(add_help=False)
    top.add_argument("--json", action="store_true", help="output JSON instead of text")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="output JSON instead of text")

    p = argparse.ArgumentParser(
        prog="agent-reach",
        description="Search and read across internet channels.",
        epilog="Examples:\n" + "\n".join("  " + ln.strip() for ln in __doc__.strip().splitlines()[1:]),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[top],
    )
    p.add_argument("--version", action="version", version=f"agent-reach {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("channels", parents=[common], help="list channels and their actions")
    rp = sub.add_parser("read", parents=[common], help="read a URL, auto-selecting the channel")
    rp.add_argument("url")
    rp.add_argument("--channel", help="force a channel instead of routing by domain")
    rp.add_argument("--limit", type=int, help="max replies/comments, if the channel supports it")

    for ch in REGISTRY.values():
        cp = sub.add_parser(ch.name, parents=[common], help=ch.title, description=ch.description)
        csub = cp.add_subparsers(dest="action", metavar="<action>")
        for a in ch.actions.values():
            ap = csub.add_parser(a.name, parents=[common], help=a.help)
            for arg in a.args:
                if arg.positional:
                    ap.add_argument(arg.name, help=arg.help, type=arg.type)
                else:
                    ap.add_argument(f"--{arg.name}", help=f"{arg.help} (default {arg.default})",
                                    type=arg.type, default=arg.default)
            ap.set_defaults(_action=a)
    return p


def run_action(action: Action, ns: argparse.Namespace) -> Any:
    kwargs = {a.name: getattr(ns, a.name) for a in action.args}
    if "limit" in kwargs and kwargs["limit"] is not None and kwargs["limit"] < 1:
        raise ReachError("--limit must be at least 1")
    return action.func(**kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        if ns.command is None:
            parser.print_help()
            return 0
        if ns.command == "channels":
            listing = channels_listing()
            print(json.dumps(listing, ensure_ascii=False, indent=2) if ns.json else render_channels(listing))
            return 0
        if ns.command == "read":
            if ns.channel:
                ch = REGISTRY.get(ns.channel)
                if ch is None or "read" not in ch.actions:
                    raise ReachError(f"channel `{ns.channel}` has no read action; see `agent-reach channels`")
            else:
                ch = route(ns.url)
            act = ch.actions["read"]
            kwargs = {}
            if ns.limit is not None:
                if not any(a.name == "limit" for a in act.args):
                    raise ReachError(f"`{ch.name} read` does not take --limit")
                if ns.limit < 1:
                    raise ReachError("--limit must be at least 1")
                kwargs["limit"] = ns.limit
            data = act.func(ns.url, **kwargs)
            if ns.json and isinstance(data, dict):
                data = {"channel": ch.name, **data}
            emit(data, ns.json)
            return 0
        action = getattr(ns, "_action", None)
        if action is None:  # `agent-reach bilibili` with no action
            parser.parse_args([ns.command, "--help"])
            return 0
        emit(run_action(action, ns), ns.json)
        return 0
    except ReachError as e:
        print(f"agent-reach: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
