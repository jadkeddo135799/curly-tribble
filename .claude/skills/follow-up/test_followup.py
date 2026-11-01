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
spec = importlib.util.spec_from_file_location("closer_followup_test", os.path.join(HERE, "followup.py"))
fu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fu)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")
D = datetime.date


def dana_before(cut):
    """The example board, with Dana's thread as it stood before `cut` (and her deal still at contacted)."""
    with open(EXAMPLE, encoding="utf-8") as fh:
        data = json.load(fh)
    data["activities"] = [a for a in data["activities"] if a["contact_id"] != "dana-harbor-lane" or a["date"] < cut]
    fu.P.find(data["deals"], "harbor-lane-website")["stage"] = "contacted"
    return data


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fu.main(argv)
    return code, out.getvalue(), err.getvalue()


class Cadence(unittest.TestCase):
    def test_the_rule_constants(self):
        self.assertEqual((fu.MAX_FOLLOWUPS, fu.GAPS, fu.SILENCE_DAYS), (3, (2, 2, 4), 30))

    def test_the_reel_dates(self):
        # Mon Oct 19 opening, then Wed 21, Fri 23, Tue 27
        for cut, day, n in (("2026-10-20", D(2026, 10, 21), 1), ("2026-10-22", D(2026, 10, 23), 2),
                            ("2026-10-24", D(2026, 10, 27), 3)):
            st = fu.contact_state(dana_before(cut), "dana-harbor-lane", day)
            self.assertEqual((st["state"], st["followup"], st["due"]), ("due", n, day.isoformat()))
        st = fu.contact_state(dana_before("2026-10-24"), "dana-harbor-lane", D(2026, 10, 26))
        self.assertEqual(st["state"], "waiting")

    def test_weekend_moves_to_monday(self):
        self.assertEqual(fu.weekday(D(2026, 10, 24)), D(2026, 10, 26))
        self.assertEqual(fu.weekday(D(2026, 10, 23)), D(2026, 10, 23))


class StopRules(unittest.TestCase):
    def test_three_follow_ups_then_stop(self):
        st = fu.contact_state(dana_before("2026-10-27T10"), "dana-harbor-lane", D(2026, 11, 3))
        self.assertEqual(st["state"], "stopped")
        self.assertIn("3 follow-ups", st["reason"])

    def test_a_reply_is_your_turn_and_a_booking_stops_it(self):
        data = dana_before("2026-10-27T12")
        st = fu.contact_state(data, "dana-harbor-lane", D(2026, 10, 27))
        self.assertEqual(st["state"], "your-turn")
        data = dana_before("2026-10-27T13")
        self.assertEqual(fu.contact_state(data, "dana-harbor-lane", D(2026, 10, 28))["state"], "booked")
        with self.assertRaises(fu.FollowupError):
            fu.draft(data, fu.load_templates(), "dana-harbor-lane", D(2026, 10, 28))

    def test_thirty_days_of_silence(self):
        data = dana_before("2026-10-20")
        self.assertEqual(fu.contact_state(data, "dana-harbor-lane", D(2026, 11, 19))["state"], "stopped")

    def test_a_no_stops_and_loses_the_deal(self):
        data = dana_before("2026-10-22")
        msg = fu.log_reply(data, "dana-harbor-lane", "Thanks, but we're all set.", "2026-10-22T10:00")
        self.assertIn("said no", msg)
        self.assertEqual(fu.P.find(data["deals"], "harbor-lane-website")["stage"], "lost")
        self.assertEqual(fu.contact_state(data, "dana-harbor-lane", D(2026, 10, 23))["state"], "stopped")
        ok, _ = fu.record(data, "dana-harbor-lane", "Bumping this.", "yes", "2026-10-23T09:00")
        self.assertFalse(ok)
        self.assertEqual(fu.P.validate(data)[0], [])

    def test_unsubscribe_is_forever(self):
        data = dana_before("2026-10-22")
        msg = fu.log_reply(data, "dana-harbor-lane", "Please remove me from your list.", "2026-10-22T10:00")
        self.assertIn("suppression", msg)
        self.assertIn("dana@harborlanedental.com", data["suppression"])
        self.assertTrue(fu.P.find(data["contacts"], "dana-harbor-lane")["do_not_contact"])
        self.assertEqual(fu.contact_state(data, "dana-harbor-lane", D(2030, 1, 1))["state"], "stopped")

    def test_the_example_board_on_the_29th(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            data = json.load(fh)
        states = {p["contact_id"]: p["state"] for p in fu.due_report(data, D(2026, 10, 29))}
        self.assertEqual(states["marcus-pine-street"], "due")
        self.assertEqual(states["priya-bluebird"], "your-turn")
        self.assertEqual(states["dana-harbor-lane"], "booked")
        self.assertEqual(states["elena-mesa-verde"], "stopped")
        self.assertEqual(states["tom-lakeline"], "stopped")


class Drafts(unittest.TestCase):
    def test_follow_up_1_is_the_bump_in_the_same_thread(self):
        d = fu.draft(dana_before("2026-10-20"), fu.load_templates(), "dana-harbor-lane", D(2026, 10, 21))
        self.assertEqual(d["subject"], "Re: Quick idea for Harbor Lane Dental")
        self.assertIn("just bumping this up", d["body"])
        self.assertEqual(d["missing"], [])

    def test_follow_up_2_needs_a_hook_and_3_says_last(self):
        d = fu.draft(dana_before("2026-10-22"), fu.load_templates(), "dana-harbor-lane", D(2026, 10, 23))
        self.assertIn("hook", d["missing"])
        d = fu.draft(dana_before("2026-10-24"), fu.load_templates(), "dana-harbor-lane", D(2026, 10, 27),
                     hook="Thursday")
        self.assertIn("last note", d["body"])

    def test_record_needs_a_yes_and_no_placeholders(self):
        data = dana_before("2026-10-20")
        self.assertFalse(fu.record(data, "dana-harbor-lane", "Bump", "ok", "2026-10-21T09:00")[0])
        self.assertFalse(fu.record(data, "dana-harbor-lane", "Hi [fill: hook]", "yes", "2026-10-21T09:00")[0])
        ok, msg = fu.record(data, "dana-harbor-lane", "Bump", "yes", "2026-10-21T09:00")
        self.assertTrue(ok, msg)
        self.assertIn("2026-10-23", msg)
        self.assertEqual(fu.P.validate(data)[0], [])

    def test_cli_due_and_reply(self):
        tmp = tempfile.mkdtemp()
        try:
            f = os.path.join(tmp, "pipeline.json")
            with open(f, "w") as fh:
                json.dump(dana_before("2026-10-24"), fh)
            code, out, _ = run(["--file", f, "due", "--today", "2026-10-27"])
            self.assertEqual(code, 0)
            self.assertIn("DUE NOW", out)
            self.assertIn("follow-up 3 of 3", out)
            code, out, _ = run(["--file", f, "reply", "--contact", "dana-harbor-lane",
                                "--text", "Sorry, crazy week! Thursday at 2 works.", "--at", "2026-10-27T11:48"])
            self.assertEqual(code, 0)
            self.assertIn("/scheduler", out)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
