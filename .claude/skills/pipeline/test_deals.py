import copy
import datetime
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_deals_test", os.path.join(HERE, "deals.py"))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
EXAMPLE = os.path.join(HERE, "pipeline.example.json")
D = datetime.date


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = P.main(argv)
    return code, out.getvalue(), err.getvalue()


class Schema(unittest.TestCase):
    def test_the_example_is_valid(self):
        errors, warnings = P.validate(example())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_an_email_out_needs_an_approval(self):
        d = example()
        del d["activities"][0]["approval"]
        errors, _ = P.validate(d)
        self.assertTrue(any("no recorded approval" in e for e in errors))

    def test_lost_needs_a_reason_and_ids_must_resolve(self):
        d = example()
        P.find(d["deals"], "lakeline-website")["lost_reason"] = None
        d["activities"][0]["contact_id"] = "nobody"
        errors, _ = P.validate(d)
        self.assertTrue(any("no lost_reason" in e for e in errors))
        self.assertTrue(any("unknown contact 'nobody'" in e for e in errors))

    def test_save_refuses_an_invalid_file(self):
        d = example()
        d["deals"][0]["stage"] = "maybe"
        tmp = tempfile.mkdtemp()
        try:
            with self.assertRaises(P.PipelineError):
                P.save(d, os.path.join(tmp, "p.json"))
            self.assertFalse(os.path.exists(os.path.join(tmp, "p.json")))
        finally:
            shutil.rmtree(tmp)


class Yes(unittest.TestCase):
    def test_only_an_explicit_yes_for_one_message(self):
        for r in ("yes", "Yes.", "y", "yes, send it", "YES send"):
            self.assertTrue(P.parse_yes(r)[0], r)
        for r in ("", "ok", "sure", "go ahead", "yeah", "yes to all", "yes send them all", "yes but tomorrow",
                  "yes?", "no", "yes, both", "yes if she hasn't replied", "yes after lunch"):
            self.assertFalse(P.parse_yes(r)[0], r)

    def test_no_words(self):
        for t in ("Not interested, thanks.", "Please remove me from your list.", "We're all set.", "No thanks",
                  "unsubscribe", "We went with another agency"):
            self.assertTrue(P.NO_RE.search(t), t)
        for t in ("Sorry, crazy week! Thursday at 2 works.", "Maybe! Can you send me prices?", "Nothing to add",
                  "Notable work"):
            self.assertFalse(P.NO_RE.search(t), t)


class Board(unittest.TestCase):
    def test_the_reel_board(self):
        out = P.board(example(), D(2026, 10, 29))
        self.assertIn("CONTRACT-SENT (1, $4,788)", out)
        self.assertIn("Harbor Lane Dental", out)
        self.assertIn("Open: $19,364 across 5 deals. Won to date: $2,500.", out)

    def test_move_to_lost_needs_a_reason_and_logs_it(self):
        d = example()
        with self.assertRaises(P.PipelineError):
            P.move(d, "pine-street-website", "lost", D(2026, 10, 30))
        P.move(d, "pine-street-website", "lost", D(2026, 10, 30), reason="budget")
        self.assertEqual(P.find(d["deals"], "pine-street-website")["lost_reason"], "budget")
        self.assertEqual(d["activities"][-1]["type"], "stage")
        self.assertEqual(P.validate(d)[0], [])

    def test_cli_init_contact_deal_move(self):
        tmp = tempfile.mkdtemp()
        try:
            f = os.path.join(tmp, "closer", "pipeline.json")
            self.assertEqual(run(["--file", f, "init", "--name", "You", "--company", "Your Agency",
                                  "--email", "hello@youragency.studio", "--address", "1 Main St"])[0], 0)
            self.assertEqual(run(["--file", f, "init"])[0], 1)
            self.assertEqual(run(["--file", f, "contact", "--name", "Dana Ortiz", "--company", "Harbor Lane Dental",
                                  "--email", "dana@harborlanedental.com"])[0], 0)
            code, out, _ = run(["--file", f, "deal", "--id", "harbor", "--contact", "dana-ortiz-harbor-lane-dental",
                                "--title", "Website", "--value", "2500", "--next", "Call", "--today", "2026-10-19"])
            self.assertEqual(code, 0, out)
            self.assertEqual(run(["--file", f, "move", "--deal", "harbor", "--stage", "won", "--today", "2026-10-29"])[0], 0)
            self.assertEqual(run(["--file", f, "validate"])[0], 0)
            with open(f) as fh:
                deal = json.load(fh)["deals"][0]
            self.assertEqual((deal["stage"], deal["value"], deal["close_date"]), ("won", 2500, "2026-10-29"))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
