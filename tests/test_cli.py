"""Offline tests: network calls are stubbed, so these run anywhere."""

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

from agent_reach import cli, core
from agent_reach.channels import bilibili, github, hackernews, v2ex


def run(*argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Routing(unittest.TestCase):
    def test_routes_by_domain(self):
        cases = {
            "https://www.v2ex.com/t/123": "v2ex",
            "https://v2ex.com/t/123#reply5": "v2ex",
            "https://www.bilibili.com/video/BV1GJ411x7h7": "bilibili",
            "https://m.bilibili.com/video/BV1GJ411x7h7": "bilibili",
            "https://github.com/a/b": "github",
            "https://news.ycombinator.com/item?id=1": "hackernews",
            "https://example.com/post": "web",
            "example.com": "web",
            # suffix match must respect label boundaries
            "https://notv2ex.com/t/1": "web",
        }
        for url, want in cases.items():
            self.assertEqual(core.route(url).name, want, url)

    def test_rejects_non_web(self):
        with self.assertRaises(core.ReachError):
            core.route("ftp://example.com")

    def test_every_channel_listed_with_actions(self):
        listing = cli.channels_listing()
        self.assertEqual({c["channel"] for c in listing}, set(core.REGISTRY))
        self.assertTrue(all(c["actions"] for c in listing))


class IdParsing(unittest.TestCase):
    def test_v2ex(self):
        self.assertEqual(v2ex.parse_topic_id("https://www.v2ex.com/t/123?p=2"), 123)
        self.assertEqual(v2ex.parse_topic_id("456"), 456)
        with self.assertRaises(core.ReachError):
            v2ex.parse_topic_id("https://www.v2ex.com/go/python")

    def test_hn(self):
        self.assertEqual(hackernews.parse_item_id("https://news.ycombinator.com/item?id=42"), 42)
        with self.assertRaises(core.ReachError):
            hackernews.parse_item_id("https://news.ycombinator.com/news")

    def test_bilibili(self):
        self.assertEqual(bilibili.parse_bvid("https://www.bilibili.com/video/BV1GJ411x7h7/?p=1"), "BV1GJ411x7h7")
        with self.assertRaises(core.ReachError):
            bilibili.parse_bvid("https://b23.tv/abc")

    def test_github(self):
        self.assertEqual(github.parse_github_url("https://github.com/a/b.git"), ("a", "b", "repo", None))
        self.assertEqual(github.parse_github_url("https://github.com/a/b/pull/7/files"), ("a", "b", "pull", 7))
        self.assertEqual(github.parse_github_url("https://github.com/a/b/tree/main"), ("a", "b", "repo", None))
        with self.assertRaises(core.ReachError):
            github.parse_github_url("https://github.com/a")


class Commands(unittest.TestCase):
    def test_channels_text_and_json(self):
        code, out, _ = run("channels")
        self.assertEqual(code, 0)
        self.assertIn("agent-reach bilibili search <keyword>", out)
        code, out, _ = run("--json", "channels")  # global flag before the subcommand
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(out), list)

    def test_bilibili_search_strips_highlight(self):
        payload = {"code": 0, "data": {"result": [
            {"bvid": "BV1GJ411x7h7", "title": '<em class="keyword">大模型</em> &amp; 你', "author": "u", "play": 9, "duration": "1:00"},
        ]}}
        with mock.patch.object(bilibili, "get_json", return_value=payload) as gj:
            code, out, _ = run("bilibili", "search", "大模型", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["title"], "大模型 & 你")
        self.assertIn("keyword=%E5%A4%A7", gj.call_args[0][0])

    def test_bilibili_412_hints_cookie(self):
        err412 = core.ReachError("HTTP 412 from api.bilibili.com: rejected")
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(bilibili, "get_json", side_effect=err412):
            code, _, err = run("bilibili", "search", "x")
        self.assertEqual(code, 1)
        self.assertIn("BILIBILI_COOKIE", err)

    def test_read_routes_v2ex_and_applies_limit(self):
        topic = [{"id": 123, "title": "T", "content": "body", "replies": 3,
                  "node": {"name": "qna"}, "member": {"username": "op"}}]
        replies = [{"member": {"username": f"u{i}"}, "content": f"r{i}"} for i in range(3)]

        def fake(url, *a, **k):
            return topic if "topics/show" in url else replies

        with mock.patch.object(v2ex, "get_json", side_effect=fake):
            code, out, _ = run("--json", "read", "https://www.v2ex.com/t/123", "--limit", "2")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["channel"], "v2ex")
        self.assertEqual([r["author"] for r in data["reply_list"]], ["u0", "u1"])

    def test_read_limit_on_unsupported_channel(self):
        code, _, err = run("read", "https://example.com", "--limit", "2")
        self.assertEqual(code, 1)
        self.assertIn("does not take --limit", err)

    def test_hn_comments_are_plain_text_and_capped(self):
        item = {"id": 1, "title": "S", "children": [
            {"author": "a", "text": "it&#x27;s<p>two", "children": [
                {"author": "b", "text": "nested", "children": []}]},
            {"author": "c", "text": "third", "children": []},
        ]}
        with mock.patch.object(hackernews, "get_json", return_value=item):
            data = hackernews.read("1", limit=2)
        self.assertEqual(data["comment_list"][0]["text"], "it's\ntwo")
        self.assertEqual([(c["author"], c["depth"]) for c in data["comment_list"]], [("a", 0), ("b", 1)])

    def test_bad_limit(self):
        code, _, err = run("v2ex", "hot", "--limit", "0")
        self.assertEqual(code, 1)
        self.assertIn("at least 1", err)

    def test_channel_without_action_shows_help(self):
        with self.assertRaises(SystemExit) as cm:
            run("bilibili")
        self.assertEqual(cm.exception.code, 0)


class HtmlToText(unittest.TestCase):
    def test_strips_scripts_and_tags(self):
        doc = "<html><head><title>x</title></head><body><script>bad()</script><p>Hi&nbsp;there</p><div>2</div></body></html>"
        self.assertEqual(core.html_to_text(doc), "Hi there\n2")


if __name__ == "__main__":
    unittest.main()
