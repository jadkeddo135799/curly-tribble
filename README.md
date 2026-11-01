# agent-reach

One CLI that lets an agent (or a person) search and read across internet channels.
Only the Python standard library, no dependencies. Python ≥ 3.9.

```bash
agent-reach channels                          # 有哪些渠道、每个渠道能做什么
agent-reach bilibili search "大模型"           # agent-reach <渠道> <动作> [参数]
agent-reach read https://www.v2ex.com/t/123   # 按链接自动选渠道
```

## Install

```bash
pip install .            # installs the `agent-reach` command
python -m agent_reach    # or run without installing
```

## Channels

| channel      | actions                          | needs                                   |
|--------------|----------------------------------|-----------------------------------------|
| `bilibili`   | `search`, `read`                 | `BILIBILI_COOKIE` (search returns HTTP 412 without it) |
| `github`     | `search`, `read` (repo / issue / PR) | optional `GITHUB_TOKEN` (60 req/h without it) |
| `hackernews` | `search`, `top`, `read`          | none                                    |
| `v2ex`       | `hot`, `latest`, `node`, `read`  | none                                    |
| `web`        | `read`                           | optional `JINA_API_KEY`; `AGENT_REACH_NO_JINA=1` to fetch directly |

`agent-reach channels` prints the live list, generated from the code.

## Conventions

- `agent-reach <channel> <action> [args] [--limit N] [--json]`
- `agent-reach read <url> [--limit N] [--channel NAME]` picks the channel from the URL's domain
  (subdomains match: `m.bilibili.com` → `bilibili`). Unknown domains go to `web`.
- Output is readable text by default. `--json` gives stable JSON for agents; `read --json` adds a `channel` field.
- Errors go to stderr with exit code 1 and say what to fix (e.g. which env var to set).

## Adding a channel

Create `agent_reach/channels/<name>.py` and import it in `agent_reach/channels/__init__.py`:

```python
from agent_reach.core import Arg, Channel, get_json, limit_arg, register

ch = register(Channel(name="example", title="Example", description="...", domains=["example.org"]))

@ch.action("search", "search posts", [Arg("query", "keywords"), limit_arg()])
def search(query, limit=10):
    return [...]          # list/dict of plain JSON-able data

@ch.action("read", "read a post", [Arg("url", "post URL")])
def read(url):            # a `read` action + `domains` makes `agent-reach read` route here
    return {...}
```

The parser, `channels` listing and URL routing all come from the registry. Nothing else needs editing.

## Tests

```bash
python -m unittest discover -s tests   # offline; network is stubbed
```
