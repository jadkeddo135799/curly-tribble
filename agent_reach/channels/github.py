import base64
import re
import urllib.parse

from agent_reach.core import Arg, Channel, ReachError, env, get_json, limit_arg, register

API = "https://api.github.com"

ch = register(Channel(
    name="github",
    title="GitHub",
    description="Search repositories; read a repo (metadata + README) or an issue/PR thread.",
    domains=["github.com"],
    requires="optional: GITHUB_TOKEN (unauthenticated limit is 60 requests/hour)",
))


def _headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = env("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _get(path):
    try:
        return get_json(API + path, _headers())
    except ReachError as e:
        if ("HTTP 403" in str(e) or "HTTP 429" in str(e)) and not env("GITHUB_TOKEN"):
            raise ReachError(f"{e}. Likely the unauthenticated rate limit (60/hour); set GITHUB_TOKEN.") from None
        raise


@ch.action("search", "search repositories", [Arg("query", "GitHub search query"), limit_arg()])
def search(query, limit=10):
    q = urllib.parse.urlencode({"q": query, "per_page": limit})
    return [
        {
            "repo": r["full_name"],
            "stars": r["stargazers_count"],
            "language": r.get("language"),
            "description": r.get("description"),
            "url": r["html_url"],
        }
        for r in _get(f"/search/repositories?{q}")["items"]
    ]


def parse_github_url(url: str):
    """Return (owner, repo, kind, number) where kind is 'repo', 'issues' or 'pull'."""
    path = urllib.parse.urlparse(url if "://" in url else "https://" + url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ReachError(f"not a GitHub repository URL: {url}")
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    if len(parts) >= 4 and parts[2] in ("issues", "pull") and parts[3].isdigit():
        return owner, repo, parts[2], int(parts[3])
    return owner, repo, "repo", None


@ch.action("read", "a repo (with README) or an issue/PR thread", [Arg("url", "github.com URL"), limit_arg(50)])
def read(url, limit=50):
    owner, repo, kind, num = parse_github_url(url)
    if kind == "repo":
        r = _get(f"/repos/{owner}/{repo}")
        out = {
            "repo": r["full_name"],
            "description": r.get("description"),
            "stars": r["stargazers_count"],
            "language": r.get("language"),
            "updated": r.get("pushed_at"),
            "url": r["html_url"],
        }
        try:
            readme = _get(f"/repos/{owner}/{repo}/readme")
            out["readme"] = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
        except ReachError:
            out["readme"] = None
        return out
    # PRs are issues in the REST API, so one endpoint covers both
    issue = _get(f"/repos/{owner}/{repo}/issues/{num}")
    comments = _get(f"/repos/{owner}/{repo}/issues/{num}/comments?per_page={min(limit, 100)}")
    return {
        "title": issue["title"],
        "state": issue["state"],
        "author": issue["user"]["login"],
        "url": issue["html_url"],
        "body": issue.get("body"),
        "comment_list": [{"author": c["user"]["login"], "body": c["body"]} for c in comments[:limit]],
    }
