import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_contract_test", os.path.join(HERE, "contract.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")

REEL = [("Services", "A booking-ready website and a managed Google Ads campaign for Harbor Lane Dental."),
        ("Fee", "$399 per month, no setup fee, billed monthly."),
        ("Term", "Month to month. Cancel anytime with 30 days notice."),
        ("Launch", "Website live within 14 days of signing.")]


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class Build(unittest.TestCase):
    def test_the_reel_agreement(self):
        data = example()
        deal = C.P.find(data["deals"], "harbor-lane-website")
        md = C.build(data, deal, "October 29, 2026", REEL)
        self.assertTrue(md.startswith("# Services Agreement"))
        self.assertIn("**Your Agency** and **Harbor Lane Dental**", md)
        self.assertIn("## 2. Fee\n\n$399 per month, no setup fee, billed monthly.", md)
        self.assertIn("## 9. Changes", md)
        self.assertIn("Dana Ortiz, Harbor Lane Dental: ______", md)
        html = C.to_html(md)
        self.assertIn("<h1>Services Agreement</h1>", html)
        self.assertIn("<strong>Your Agency</strong>", html)

    def test_empty_sections_are_refused(self):
        data = example()
        deal = C.P.find(data["deals"], "harbor-lane-website")
        with self.assertRaises(C.ContractError):
            C.build(data, deal, "today", [("Fee", "[fill: fee]")])

    def test_fee_is_checked_against_the_call(self):
        data = example()
        deal = C.P.find(data["deals"], "harbor-lane-website")
        self.assertEqual(C.check_fee(data, deal, "$399 per month"), [])
        self.assertTrue(C.check_fee(data, deal, "$349 per month"))
        self.assertTrue(C.check_fee(data, deal, "Monthly, as agreed"))


class Record(unittest.TestCase):
    def test_record_needs_a_draft_and_a_yes(self):
        tmp = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            data = example()
            deal = C.P.find(data["deals"], "juniper-booking")
            self.assertFalse(C.record(data, "juniper-booking", "yes", "2026-11-03T11:00")[0])
            md_path, html_path, _ = C.paths_for(data, deal)
            os.makedirs(C.OUT_DIR)
            with open(md_path, "w") as fh:
                fh.write("x")
            with open(html_path, "w") as fh:
                fh.write("x")
            self.assertFalse(C.record(data, "juniper-booking", "yes to all", "2026-11-03T11:00")[0])
            ok, msg = C.record(data, "juniper-booking", "yes", "2026-11-03T11:00")
            self.assertTrue(ok, msg)
            self.assertEqual(deal["stage"], "contract-sent")
            self.assertEqual(C.P.validate(data)[0], [])
        finally:
            os.chdir(cwd)
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
