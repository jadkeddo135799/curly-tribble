"""Channel / action registry and shared HTTP helpers.

A *channel* is a site or source (bilibili, v2ex, github...). Each channel
exposes named *actions* (search, read, hot...). Channels also declare the
domains they own so `agent-reach read <url>` can route a link automatically.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

USER_AGENT = "agent-reach/0.1 (+https://github.com/jadkeddo135799/curly-tribble)"
DEFAULT_TIMEOUT = 15


class ReachError(Exception):
    """User-facing error: printed without a traceback."""


@dataclass
class Arg:
    name: str
    help: str
    # positional args are required; options become --name with a default
    positional: bool = True
    type: Callable[[str], Any] = str
    default: Any = None


@dataclass
class Action:
    name: str
    help: str
    func: Callable[..., Any]
    args: List[Arg] = field(default_factory=list)


@dataclass
class Channel:
    name: str
    title: str
    description: str
    domains: List[str] = field(default_factory=list)
    actions: Dict[str, Action] = field(default_factory=dict)
    # human-readable note about required setup (env vars, cookies...)
    requires: Optional[str] = None

    def action(self, name: str, help: str, args: Optional[List[Arg]] = None):
        def deco(func):
            self.actions[name] = Action(name, help, func, args or [])
            return func

        return deco

    def owns(self, host: str) -> bool:
        host = host.lower().split(":")[0]
        return any(host == d or host.endswith("." + d) for d in self.domains)


REGISTRY: Dict[str, Channel] = {}


def register(channel: Channel) -> Channel:
    if channel.name in REGISTRY:
        raise ValueError(f"duplicate channel: {channel.name}")
    REGISTRY[channel.name] = channel
    return channel


def route(url: str) -> Channel:
    """Pick the channel that should handle `url`; falls back to `web`."""
    parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ReachError(f"not a web URL: {url}")
    for ch in REGISTRY.values():
        if "read" in ch.actions and ch.owns(parsed.netloc):
            return ch
    return REGISTRY["web"]


def limit_arg(default: int = 10) -> Arg:
    return Arg("limit", "max results", positional=False, type=int, default=default)


# ---------------------------------------------------------------- HTTP


def _request(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        hint = {
            403: "the site refused the request (blocked or login required)",
            404: "not found",
            412: "rejected by the site's anti-bot check; a logged-in cookie may be required",
            429: "rate limited; wait and retry",
        }.get(e.code, e.reason)
        raise ReachError(f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}: {hint}") from None
    except urllib.error.URLError as e:
        raise ReachError(f"network error fetching {url}: {e.reason}") from None
    except TimeoutError:
        raise ReachError(f"timed out fetching {url}") from None


def get_text(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    return _request(url, headers, timeout).decode("utf-8", errors="replace")


def get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
    raw = _request(url, {"Accept": "application/json", **(headers or {})}, timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ReachError(f"expected JSON from {url}, got something else") from None


def env(name: str) -> Optional[str]:
    v = os.environ.get(name, "").strip()
    return v or None


def html_to_text(doc: str) -> str:
    """Crude but dependency-free HTML -> readable text."""
    doc = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", doc or "")
    doc = re.sub(r"(?i)<br\s*/?>|<p\b[^>]*>|</(p|div|li|h[1-6]|tr)>", "\n", doc)
    doc = re.sub(r"(?s)<[^>]+>", " ", doc)
    doc = html.unescape(doc)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in doc.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
