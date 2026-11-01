import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_log_test", os.path.join(HERE, "log.py"))
log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class Sync(unittest.TestCase):
    def test_matches_dedupes_and_classifies(self):
        data = example()
        emails = [
            # already logged: the reel's reply
            {"date": "2026-10-27T11:48", "from": "Dana Ortiz <dana@harborlanedental.com>",
             "to": ["hello@youragency.studio"], "subject": None, "text": "Sorry, crazy week! Thursday at 2 works."},
            {"date": "2026-10-30T08:00", "from": "Dana Ortiz <dana@harborlanedental.com>",
             "to": ["hello@youragency.studio"], "subject": "Signed!", "text": "Signed and sent back."},
            {"date": "2026-10-30T09:15", "from": "You <hello@youragency.studio>",
             "to": ["Priya Shah <priya@bluebirdpetspa.com>"], "subject": "Prices", "text": "Here you go."},
            {"date": "2026-10-30T10:00", "from": "someone@newbiz.example", "to": ["hello@youragency.studio"],
             "subject": "Do you build sites?", "text": "Hi"},
        ]
        logged, skipped, unknown = log.sync(data, emails)
        self.assertEqual(len(logged), 2)
        self.assertEqual(unknown, ["someone@newbiz.example"])
        signed, prices = logged
        self.assertEqual((signed["direction"], signed["type"], signed["deal_id"]), ("in", "reply", "harbor-lane-website"))
        self.assertEqual((prices["direction"], prices["type"]), ("out", "reply"))
        self.assertEqual(prices["approval"], log.SENT_BY_YOU)
        self.assertEqual(log.P.validate(data)[0], [])
        logged, skipped, _ = log.sync(data, emails[1:3])
        self.assertEqual((len(logged), len(skipped)), (0, 2))

    def test_first_email_to_a_new_contact_is_the_opening(self):
        data = example()
        logged, _, _ = log.sync(data, [{"date": "2026-10-30T09:00", "from": "hello@youragency.studio",
                                        "to": ["luis@redbudfamilychiropractic.com"], "subject": "Quick idea",
                                        "text": "Hi Luis"}])
        self.assertEqual(logged[0]["type"], "opening")


class Calls(unittest.TestCase):
    def test_call_notes_are_saved_and_summarised(self):
        data = example()
        tmp = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            c = log.P.find(data["contacts"], "hannah-juniper")
            path = log.save_call_notes(data, c, "2026-11-03T10:00", 22, "- Wants class booking\n- Budget ok\n")
            self.assertEqual(path, os.path.join("closer", "calls", "2026-11-03-juniper-yoga-loft.md"))
            self.assertTrue(os.path.exists(path))
        finally:
            os.chdir(cwd)
            shutil.rmtree(tmp)
        self.assertEqual(log.summary("# Notes\n- Wants class booking\n- Budget ok"), "Wants class booking Budget ok")

    def test_timeline(self):
        out = log.timeline(example(), "dana-harbor-lane")
        self.assertIn("Dana Ortiz, Harbor Lane Dental", out)
        self.assertIn("Thursday at 2 works", out)
        self.assertEqual(len(out.splitlines()), 9)


if __name__ == "__main__":
    unittest.main()
