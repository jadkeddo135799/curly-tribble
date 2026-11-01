"""Importing this package registers every built-in channel.

`web` must stay registered: it is the fallback for `agent-reach read`.
"""

from agent_reach.channels import bilibili, github, hackernews, v2ex, web  # noqa: F401
