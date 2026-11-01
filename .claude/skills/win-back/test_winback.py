import datetime
import importlib.util
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_winback_test", os.path.join(HERE, "winback.py"))
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")
D = datetime.date


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class Assess(unittest.TestCase):
    def test_the_example_board(self):
        rows = {r["deal_id"]: r["state"] for r in W.candidates(example(), D(2026, 10, 29))}
        self.assertEqual(rows["lakeline-website"], "ready")
        self.assertEqual(rows["mesa-verde-ads"], "never")
        self.assertEqual(rows["cedar-oak-ordering"], "skip")
        self.assertEqual(rows["harbor-lane-website"], "skip")

    def test_sixty_days_and_the_revisit_date(self):
        data = example()
        deal = W.P.find(data["deals"], "lakeline-website")
        self.assertEqual(W.assess(data, deal, D(2026, 10, 8))[0], "later")   # 59 days after Aug 10
        self.assertEqual(W.assess(data, deal, D(2026, 10, 9))[0], "ready")
        deal["revisit"] = "2026-11-15"
        self.assertEqual(W.assess(data, deal, D(2026, 10, 29))[0], "later")
        self.assertEqual(W.assess(data, deal, D(2026, 11, 15))[0], "ready")

    def test_competitor_waits_longer(self):
        data = example()
        deal = W.P.find(data["deals"], "lakeline-website")
        deal["lost_reason"] = "went with another agency"
        self.assertEqual(W.assess(data, deal, D(2026, 10, 29))[0], "later")
        self.assertEqual(W.assess(data, deal, D(2027, 2, 6))[0], "ready")

    def test_one_win_back_only(self):
        data = example()
        ok, msg = W.record(data, "lakeline-website", "Hi Tom, checking back in.", "yes", "2026-10-29T10:00")
        self.assertTrue(ok, msg)
        deal = W.P.find(data["deals"], "lakeline-website")
        self.assertEqual(deal["stage"], "contacted")
        self.assertEqual(W.P.validate(data)[0], [])
        state, why, _ = W.assess(data, deal, D(2027, 6, 1))
        self.assertEqual(state, "never")
        self.assertIn("one is the limit", why)

    def test_record_needs_a_yes(self):
        self.assertFalse(W.record(example(), "lakeline-website", "Hi", "ok", "2026-10-29T10:00")[0])
        self.assertFalse(W.record(example(), "mesa-verde-ads", "Hi", "yes", "2026-10-29T10:00")[0])


class Draft(unittest.TestCase):
    def test_the_timing_draft_keeps_the_easy_out(self):
        d = W.draft(example(), "lakeline-website", D(2026, 10, 29))
        self.assertEqual(d["kind"], "timing")
        self.assertEqual(d["subject"], "Checking back in, Tom")
        self.assertIn("until the fall", d["body"])
        self.assertIn("I'll leave it", d["body"])

    def test_budget_uses_plan_b(self):
        data = example()
        W.P.find(data["deals"], "lakeline-website")["lost_reason"] = "budget"
        d = W.draft(data, "lakeline-website", D(2026, 10, 29))
        self.assertIn("$399 monthly plan", d["body"])

    def test_no_draft_for_never(self):
        with self.assertRaises(W.WinbackError):
            W.draft(example(), "mesa-verde-ads", D(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()
