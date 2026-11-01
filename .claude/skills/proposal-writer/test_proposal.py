import datetime
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_proposal_test", os.path.join(HERE, "proposal.py"))
PR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PR)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def options():
    with open(os.path.join(HERE, "options.example.json"), encoding="utf-8") as fh:
        return json.load(fh)


class Build(unittest.TestCase):
    def test_the_bluebird_proposal(self):
        data = example()
        deal = PR.P.find(data["deals"], "bluebird-ads")
        md = PR.build(data, deal, options(), datetime.date(2026, 10, 29))
        self.assertIn("# Proposal for Bluebird Pet Spa", md)
        self.assertIn("Valid until November 12, 2026.", md)
        self.assertIn("- Maybe! Can you send me prices?", md)
        self.assertIn("| Full build | $2,500 setup | $2,500 |", md)
        self.assertIn("| Monthly plan (recommended) | $399/month | $4,788 |", md)
        self.assertIn("Most practices win the cost back with 2 new patients a month.", md)

    def test_bad_options_are_refused(self):
        for bad in ([], options() * 2, [{"name": "Free", "includes": ["x"]}],
                    [dict(o, recommended=True) for o in options()]):
            with self.assertRaises(PR.ProposalError):
                PR.check_options(bad)


class Record(unittest.TestCase):
    def test_record_refuses_placeholders_then_logs(self):
        tmp = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            data = example()
            deal = PR.P.find(data["deals"], "bluebird-ads")
            md = PR.build(data, deal, options(), datetime.date(2026, 10, 29))
            os.makedirs(PR.OUT_DIR)
            path = os.path.join(PR.OUT_DIR, "bluebird-pet-spa.md")
            with open(path, "w") as fh:
                fh.write(md)
            self.assertFalse(PR.record(data, "bluebird-ads", "yes", "2026-10-29T16:00")[0])
            with open(path, "w") as fh:
                fh.write(md.replace("- [Claude: 3 to 5 lines, what they get and by when]", "- Ads live in 7 days"))
            self.assertFalse(PR.record(data, "bluebird-ads", "sure", "2026-10-29T16:00")[0])
            ok, msg = PR.record(data, "bluebird-ads", "yes", "2026-10-29T16:00")
            self.assertTrue(ok, msg)
            self.assertEqual(deal["stage"], "proposal")
            self.assertIn("$2,500 setup / $399/month", data["activities"][-1]["text"])
            self.assertEqual(PR.P.validate(data)[0], [])
        finally:
            os.chdir(cwd)
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
