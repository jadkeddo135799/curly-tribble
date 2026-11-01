from agent_reach.core import Arg, Channel, ReachError, env, get_text, html_to_text, register

ch = register(Channel(
    name="web",
    title="任意网页 / Any web page",
    description="Read any URL as clean Markdown (via Jina Reader, raw-HTML fallback). Used by `read` when no channel owns the domain.",
    requires="optional: JINA_API_KEY for higher rate limits; AGENT_REACH_NO_JINA=1 to fetch directly",
))


@ch.action("read", "fetch a page as Markdown/text", [Arg("url", "page URL")])
def read(url: str):
    if "://" not in url:
        url = "https://" + url
    if not env("AGENT_REACH_NO_JINA"):
        headers = {"X-Return-Format": "markdown"}
        key = env("JINA_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            return {"url": url, "content": get_text("https://r.jina.ai/" + url, headers, timeout=30).strip()}
        except ReachError:
            pass  # fall through to a direct fetch
    return {"url": url, "content": html_to_text(get_text(url, {"Accept": "text/html,*/*"}))}
