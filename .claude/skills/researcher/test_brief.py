import importlib.util
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("closer_brief_test", os.path.join(HERE, "brief.py"))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
EXAMPLE = os.path.join(HERE, "..", "pipeline", "pipeline.example.json")

HOME = """<html><head><title>Harbor Lane Dental | Family Dentist in Austin</title>
<meta name="description" content="Gentle family dentistry on Harbor Lane since 2009."></head>
<body><h1>Family dentistry in Austin</h1><h2>Cleanings &amp; checkups</h2>
<a href="/about">About us</a><a href="/services">Our services</a><a href="https://other.example/x">Partner</a>
<a href="/blog/1">Blog</a><p>Serving Austin since 2009. Call (512) 555-0142 or email frontdesk@harborlanedental.com</p>
<a href="https://www.instagram.com/harborlanedental">Instagram</a>
<script type="application/ld+json">{"aggregateRating": {"ratingValue": "4.8", "reviewCount": "212"}}</script>
</body></html>"""
ABOUT = "<html><body><h2>Meet Dr. Ortiz</h2><p>Our team</p></body></html>"


class Facts(unittest.TestCase):
    def test_pick_pages_stays_on_site(self):
        picked = B.pick_pages("https://harborlanedental.com", HOME)
        self.assertEqual(picked, ["https://harborlanedental.com/about", "https://harborlanedental.com/services"])

    def test_facts(self):
        f = B.facts({"https://harborlanedental.com": HOME, "https://harborlanedental.com/about": ABOUT})
        self.assertEqual(f["title"], "Harbor Lane Dental | Family Dentist in Austin")
        self.assertEqual(f["since"], 2009)
        self.assertEqual(f["rating"]["count"], 212)
        self.assertEqual(f["phones"], ["(512) 555-0142"])
        self.assertEqual(f["emails"], ["frontdesk@harborlanedental.com"])
        self.assertIn("Cleanings & checkups", f["headings"])
        self.assertIn("Meet Dr. Ortiz", f["headings"])
        self.assertIn("booking", f["tools_missing"])
        self.assertEqual(f["socials"], ["https://www.instagram.com/harborlanedental"])

    def test_brief_cites_sources_and_leaves_slots_for_claude(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            data = json.load(fh)
        c = B.P.find(data["contacts"], "dana-harbor-lane")
        f = B.facts({"https://harborlanedental.com": HOME})
        md = B.brief(c, None, f)
        self.assertTrue(md.startswith("# Brief: Harbor Lane Dental"))
        self.assertIn("- In business since 2009", md)
        self.assertIn("## Sources\n\n- https://harborlanedental.com", md)
        self.assertIn("[Claude:", md)
        self.assertIn("a hint, not proof", md)


if __name__ == "__main__":
    unittest.main()
