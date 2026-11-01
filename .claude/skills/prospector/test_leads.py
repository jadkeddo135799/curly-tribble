import datetime
import importlib.util
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_leads_test", os.path.join(HERE, "leads.py"))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")

GOOD = ('<html><head><title>Good Dental</title><meta name="viewport" content="width=device-width"></head>'
        '<body><a href="https://calendly.com/good">Book online</a><script>gtag("config","AW-123456789")</script>'
        '<div>Reviews</div></body></html>')
BARE = "<html><head><title>Harbor Lane Dental | Austin</title></head><body><p>Call us.</p></body></html>"


def example():
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class Audit(unittest.TestCase):
    def test_a_site_with_everything_has_no_gaps(self):
        gaps, title = L.analyze(GOOD, 1.2, "Dentist")
        self.assertEqual(gaps, [])
        self.assertEqual(title, "Good Dental")

    def test_a_bare_dental_site(self):
        gaps, title = L.analyze(BARE, 5.0, "Dentist")
        keys = [g["key"] for g in gaps]
        self.assertEqual(keys[:2], ["no-booking", "not-mobile"])
        self.assertIn("slow", keys)
        self.assertIn("Site loads in 5 s", [g["why"] for g in gaps])
        self.assertNotIn("no-ordering", keys)  # a dentist does not need online ordering

    def test_a_bakery_needs_ordering_not_booking(self):
        keys = [g["key"] for g in L.analyze(BARE, 1.0, "Bakery")[0]]
        self.assertIn("no-ordering", keys)
        self.assertNotIn("no-booking", keys)

    def test_rank(self):
        rows = [{"name": "A", "website": "a"}, {"name": "B", "website": "b"}]
        audits = [{"gaps": [{"key": "no-reviews", "score": 1, "why": "No reviews on site"}]},
                  {"gaps": [{"key": "no-booking", "score": 3, "why": "No online booking"}]}]
        ranked = L.rank(rows, audits)
        self.assertEqual([r["name"] for r in ranked], ["B", "A"])
        self.assertEqual(ranked[0]["why"], "No online booking")

    def test_the_example_csv_reads(self):
        rows = L.read_csv(os.path.join(HERE, "leads.example.csv"))
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["who"], "Dana Ortiz")


class Outreach(unittest.TestCase):
    def test_add_skips_suppressed_and_existing(self):
        data = example()
        ranked = [{"name": "Mesa Verde Med Spa", "who": "Elena Ruiz", "email": "elena@mesaverdemedspa.com",
                   "website": "x", "why": "No Google Ads tag on the site", "gaps": ["no-ads"]},
                  {"name": "Harbor Lane Dental", "who": "Dana Ortiz", "email": "dana@harborlanedental.com",
                   "website": "x", "why": "No online booking", "gaps": ["no-booking"]},
                  {"name": "Oak Hill Vet", "who": "Sam Park", "email": "sam@oakhillvet.example",
                   "website": "https://oakhillvet.example", "why": "No online booking", "gaps": ["no-booking"]}]
        added, skipped = L.add_leads(data, ranked, 10, 2500, datetime.date(2026, 10, 30))
        self.assertEqual(added, ["sam-oak-hill-vet"])
        self.assertEqual([w for _, w in skipped], ["on the suppression list", "already on the board"])
        self.assertEqual(L.P.validate(data)[0], [])

    def test_draft_has_the_legal_footer_and_the_gap(self):
        d = L.draft(example(), L.load_openers(), "luis-redbud")
        self.assertEqual(d["gap"], "slow")
        self.assertIn("100 Congress Ave", d["body"])
        self.assertIn('Reply "no thanks"', d["body"])
        self.assertEqual(d["subject"], "Quick idea for Redbud Family Chiropractic")

    def test_no_address_no_email(self):
        data = example()
        data["me"]["address"] = None
        with self.assertRaises(L.ProspectError):
            L.draft(data, L.load_openers(), "luis-redbud")

    def test_never_to_do_not_contact_or_twice(self):
        with self.assertRaises(L.ProspectError):
            L.draft(example(), L.load_openers(), "elena-mesa-verde")
        with self.assertRaises(L.ProspectError):
            L.draft(example(), L.load_openers(), "dana-harbor-lane")

    def test_record_needs_a_yes_and_respects_the_cap(self):
        data = example()
        self.assertFalse(L.record(data, "luis-redbud", "Hi Luis", "sure", "2026-10-30T09:00")[0])
        ok, msg = L.record(data, "luis-redbud", "Hi Luis", "yes", "2026-10-30T09:00", "Quick idea")
        self.assertTrue(ok, msg)
        self.assertEqual(L.P.find(data["deals"], "redbud-speed")["stage"], "contacted")
        self.assertEqual(L.P.validate(data)[0], [])
        busy = example()
        for i in range(L.DAILY_CAP):
            L.P.add_activity(busy, date="2026-10-30T08:%02d" % (i % 60), contact_id="luis-redbud", channel="email",
                             type="opening", direction="out", approval="yes")
        self.assertFalse(L.record(busy, "luis-redbud", "Hi", "yes", "2026-10-30T09:00")[0])


if __name__ == "__main__":
    unittest.main()
