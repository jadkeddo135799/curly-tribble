import datetime
import importlib.util
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_forecast_test", os.path.join(HERE, "forecast.py"))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")
D = datetime.date


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class Forecast(unittest.TestCase):
    def test_november_on_the_reel_board(self):
        f = F.forecast(example(), "2026-11", D(2026, 10, 29))
        self.assertEqual((f["commit"], f["likely"], f["best"]), (4788, 6474, 16864))
        self.assertEqual(f["rows"][0]["deal_id"], "harbor-lane-website")
        self.assertIn("standard stage chances", f["basis"])

    def test_october_counts_the_win(self):
        f = F.forecast(example(), "2026-10", D(2026, 10, 29))
        self.assertEqual(f["won"], 2500)
        self.assertEqual(f["rows"], [])

    def test_unsubscribes_are_not_lost_sales(self):
        r = F.rates(example())
        self.assertEqual((r["won"], r["lost"], r["win_rate"]), (1, 1, 0.5))
        self.assertFalse(r["enough"])
        self.assertEqual(r["avg_days_to_close"], 24)

    def test_chances_scale_with_real_history(self):
        data = example()
        for i in range(12):
            data["contacts"].append({"id": "c%d" % i, "name": "C %d" % i})
            data["deals"].append({"id": "d%d" % i, "contact_id": "c%d" % i, "title": "x", "value": 100,
                                  "stage": "won" if i < 6 else "lost", "lost_reason": "budget"})
        ch, basis = F.chances(data)
        self.assertIn("scaled to your win rate", basis)
        self.assertGreater(ch["proposal"], F.STAGE_CHANCE["proposal"])
        self.assertLessEqual(ch["contract-sent"], 0.95)

    def test_month_bounds(self):
        self.assertEqual(F.month_bounds("2026-12"), (D(2026, 12, 1), D(2026, 12, 31)))
        self.assertEqual(F.month_bounds("2027-02"), (D(2027, 2, 1), D(2027, 2, 28)))

    def test_health(self):
        data = example()
        self.assertEqual(F.health(data, D(2026, 10, 29)), [])
        flags = dict(F.health(data, D(2026, 11, 10)))
        self.assertIn("has passed", flags["harbor-lane-website"])
        self.assertIn("quiet", flags["pine-street-website"])


if __name__ == "__main__":
    unittest.main()
